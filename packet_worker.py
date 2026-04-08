from __future__ import annotations

import os
import time
from typing import Callable, List, Optional, Tuple

from rpd_io import PartRow


def _format_error(exc: Exception) -> str:
    name = type(exc).__name__
    text = str(exc or "").strip()
    return f"{name}: {text}" if text else name


def _skip_result(
    *,
    index: int,
    status: str,
    stage: str,
    started_at: float,
    exc: Optional[Exception] = None,
) -> dict:
    detail = _format_error(exc) if exc is not None else ""
    status_text = f"{status} ({stage})" if not detail else f"{status} ({stage}: {detail})"
    result = {
        "idx": index,
        "status": status_text,
        "missing": 1,
        "skip": True,
        "error_stage": stage,
        "elapsed_ms": int((time.perf_counter() - started_at) * 1000.0),
    }
    if detail:
        result["error"] = detail
    return result


def process_packet_part(
    index: int,
    part: PartRow,
    *,
    resolver: Callable[[str, str], Optional[str]],
    render_mode: str,
    fitz_module,
    apply_packet_layer_policy_fn: Callable[[object], bool],
    highlight_red_target_layers_fn: Callable[..., None],
    highlight_red_text_fn: Callable[..., None],
    render_page_pixmap_fn: Callable[..., object],
    gate_mask_from_boxes_fn: Callable[..., object],
    grayscale_preserve_red_and_highlight_fn: Callable[..., object],
    pix_to_image_stream_fn: Callable[[object], bytes],
    packet_raster_dpi: int,
) -> dict:
    t0 = time.perf_counter()
    t_open0 = t0
    part_name = os.path.splitext(os.path.basename(getattr(part, "sym", "") or ""))[0]
    status = part_name or f"Part {index}"
    qty = int(getattr(part, "qty", None) or 1)
    extra = int(getattr(part, "extra", None) or 0)
    pdf = resolver(part.sym, ".pdf")
    if not pdf or not os.path.exists(pdf):
        return _skip_result(index=index, status=status, stage="missing PDF", started_at=t0)
    try:
        src = fitz_module.open(pdf)
    except Exception as exc:
        return _skip_result(index=index, status=status, stage="open failed", started_at=t0, exc=exc)
    try:
        if src.page_count < 1:
            return _skip_result(index=index, status=status, stage="empty PDF", started_at=t0)

        mode = str(render_mode or "raster").strip().lower()
        if mode == "vector":
            return {
                "idx": index,
                "status": status,
                "missing": 0,
                "skip": False,
                "mode": "vector",
                "pdf_path": pdf,
                "qty": qty,
                "extra": extra,
                "elapsed_ms": int((time.perf_counter() - t0) * 1000.0),
            }

        open_ms = int((time.perf_counter() - t_open0) * 1000.0)
        apply_packet_layer_policy_fn(src)

        src_page = src.load_page(0)
        src_rect = src_page.rect

        t_gate0 = time.perf_counter()
        gate_boxes: List[Tuple[float, float, float, float]] = []
        highlight_red_target_layers_fn(src_page)
        highlight_red_text_fn(src_page, gate_boxes=gate_boxes)
        gate_ms = int((time.perf_counter() - t_gate0) * 1000.0)

        t_render0 = time.perf_counter()
        pix = render_page_pixmap_fn(src_page, dpi=packet_raster_dpi)
        red_gate_mask = None
        if pix is not None and gate_boxes:
            red_gate_mask = gate_mask_from_boxes_fn(
                src_page,
                int(getattr(pix, "width", 0)),
                int(getattr(pix, "height", 0)),
                gate_boxes,
            )
        pix = grayscale_preserve_red_and_highlight_fn(pix, red_gate_mask=red_gate_mask)
        render_ms = int((time.perf_counter() - t_render0) * 1000.0)

        t_encode0 = time.perf_counter()
        stream = pix_to_image_stream_fn(pix)
        encode_ms = int((time.perf_counter() - t_encode0) * 1000.0)
        return {
            "idx": index,
            "status": status,
            "missing": 0,
            "skip": False,
            "mode": "raster",
            "w": float(src_rect.width),
            "h": float(src_rect.height),
            "qty": qty,
            "extra": extra,
            "img_stream": stream,
            "open_ms": open_ms,
            "gate_ms": gate_ms,
            "render_ms": render_ms,
            "encode_ms": encode_ms,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000.0),
        }
    except Exception as exc:
        return _skip_result(index=index, status=status, stage="process failed", started_at=t0, exc=exc)
    finally:
        try:
            src.close()
        except Exception:
            pass
