# pdf_packet.py
# PDF packet generation (watermarked kit printout)
#
# Scope:
# - Build a PDF packet by concatenating each part's PDF (single-page expected) and overlaying a QTY mark.
# - Uses only fitz (PyMuPDF) + simple path resolution helpers.
# - QTY mark: bottom-left with rounded outline box.
#
# Notes:
# - No Qt / UI code belongs in this module.
# - Deterministic: missing PDFs are counted; function returns (pages_written, missing_count).

import os
import shutil
import tempfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import time
from typing import Callable, List, Optional, Tuple

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None
try:
    import numpy as np
except Exception:
    np = None

from config import (
    ENG_RELEASE_MAP as CFG_ENG_RELEASE_MAP,
    W_RELEASE_ROOT as CFG_W_RELEASE_ROOT,
)
from file_utils import ensure_parent_dir
from packet_annotations import (
    collect_red_symbol_dimension_chars as _collect_red_symbol_dimension_chars_impl,
    color_to_rgb as _color_to_rgb_impl,
    grayscale_title_layer as _grayscale_title_layer_impl,
    highlight_red_target_layers as _highlight_red_target_layers_impl,
    highlight_red_text as _highlight_red_text_impl,
    is_red_rgb as _is_red_rgb_impl,
    is_red_text_color as _is_red_text_color_impl,
    looks_like_dimension_text as _looks_like_dimension_text_impl,
    overlay_red_symbol_dimension_chars as _overlay_red_symbol_dimension_chars_impl,
    overlay_red_text_runs as _overlay_red_text_runs_impl,
)
from packet_apply import apply_packet_result as _apply_packet_result_impl
from packet_layers import (
    apply_packet_layer_policy as _apply_packet_layer_policy,
    collect_layer_zero_masks as _collect_layer_zero_masks,
    erase_layer_zero_overlays as _erase_layer_zero_overlays,
    first_toggle_layer_aliases as _first_toggle_layer_aliases,
    is_title_layer as _is_title_layer,
    is_symbol_or_dimension_layer_name as _is_symbol_or_dimension_layer_name,
    layer_is_target as _layer_is_target,
)
from packet_paths import (
    force_w_candidates as _force_w_candidates_impl,
    map_to_eng_release as _map_to_eng_release_impl,
    resolve_asset as _resolve_asset_impl,
)
from packet_worker import process_packet_part as _process_packet_part_impl
import runtime_trace as rt
from rpd_io import PartRow

# Engineering release root (preferred for production)
W_RELEASE_ROOT = CFG_W_RELEASE_ROOT
ENG_RELEASE_MAP: List[Tuple[str, str]] = list(CFG_ENG_RELEASE_MAP)

# Watermark style
WATERMARK_TEXT_SCALE = 2.4
WATERMARK_STROKE_COLOR = (0.25, 1.00, 0.25)  # fluorescent green for QTY box
WATERMARK_STROKE_WIDTH = 6.6
WATERMARK_STROKE_OPACITY = 0.94
WATERMARK_RADIUS = 14.0
WATERMARK_TEXT_COLOR = (0, 0, 0)  # black
DIM_HILITE_COLOR = (0.08, 0.96, 0.08)  # denser fluorescent green outline for dimensions
DIM_HILITE_STROKE_WIDTH = 3.6
DIM_HILITE_PAD_X = 1.8
DIM_HILITE_PAD_Y_TOP = 3.2
DIM_HILITE_PAD_Y_BOTTOM = 1.8
DIM_HILITE_STROKE_OPACITY = 0.90  # 10% transparent
DIM_HILITE_RADIUS = 6.0
TITLE_GRAYSCALE_COLOR = (0.80, 0.80, 0.80)
TITLE_GRAYSCALE_OPACITY = 0.40
PACKET_RASTER_DPI = 220
PACKET_BW_THRESHOLD = 208
PACKET_JPEG_QUALITY = 82
# PyMuPDF page processing can be unstable in threaded mode on some systems.
# Keep stable single-thread default; allow override via env for testing.
try:
    PACKET_MAX_WORKERS = max(1, int(str(os.environ.get("RK_PACKET_MAX_WORKERS", "1")).strip() or "1"))
except Exception:
    PACKET_MAX_WORKERS = 1
# Default OFF: keep normal black-on-white output unless explicitly requested.
PACKET_INVERT_BW = str(os.environ.get("RK_PACKET_INVERT_BW", "0")).strip().lower() not in ("0", "false", "off", "no")


class PacketBuildCanceled(Exception):
    def __init__(
        self,
        message: str = "Packet build canceled.",
        pages: int = 0,
        missing: int = 0,
    ):
        super().__init__(message)
        self.pages = int(pages)
        self.missing = int(missing)


class PacketBuildEmpty(Exception):
    def __init__(
        self,
        message: str = "No packet pages were created.",
        pages: int = 0,
        missing: int = 0,
    ):
        super().__init__(message)
        self.pages = int(pages)
        self.missing = int(missing)


def _format_error(exc: Exception) -> str:
    name = type(exc).__name__
    text = str(exc or "").strip()
    return f"{name}: {text}" if text else name


def _format_qty_watermark_text(qty: int, extra: int = 0) -> str:
    text = f"QTY {int(qty or 0)}"
    if int(extra or 0) != 0:
        text += " + S/U"
    return text


def _render_page_pixmap(page, dpi: int = PACKET_RASTER_DPI):
    if fitz is None:
        return None
    scale = max(72.0, float(dpi)) / 72.0
    mat = fitz.Matrix(scale, scale)
    try:
        fitz.TOOLS.set_graphics_min_line_width(1.0)
    except Exception:
        pass
    return page.get_pixmap(matrix=mat, alpha=False)


def _pix_to_image_stream(pix) -> bytes:
    if pix is None:
        return b""
    try:
        jpg = pix.tobytes("jpg", jpg_quality=int(PACKET_JPEG_QUALITY))
        if jpg:
            return jpg
    except Exception:
        pass
    try:
        png = pix.tobytes("png")
        if png:
            return png
    except Exception:
        pass
    return b""


def _gate_mask_from_boxes(page, pix_w: int, pix_h: int, gate_boxes):
    if np is None or pix_w <= 0 or pix_h <= 0 or not gate_boxes:
        return None
    try:
        pr = page.rect
        sx = float(pix_w) / max(1e-9, float(pr.width))
        sy = float(pix_h) / max(1e-9, float(pr.height))
        ox = float(pr.x0)
        oy = float(pr.y0)
    except Exception:
        return None

    mask = np.zeros((int(pix_h), int(pix_w)), dtype=bool)
    for bb in gate_boxes:
        try:
            x0, y0, x1, y1 = float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])
        except Exception:
            continue
        if x1 <= x0 or y1 <= y0:
            continue
        ix0 = max(0, min(int(pix_w), int((x0 - ox) * sx) - 4))
        iy0 = max(0, min(int(pix_h), int((y0 - oy) * sy) - 4))
        ix1 = max(0, min(int(pix_w), int((x1 - ox) * sx) + 4))
        iy1 = max(0, min(int(pix_h), int((y1 - oy) * sy) + 4))
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        mask[iy0:iy1, ix0:ix1] = True
    return mask


def _grayscale_preserve_red_and_highlight(pix, red_gate_mask=None):
    """
    Convert to black/white while preserving:
    - red content gated by symbol/dimension layer mask
    - green highlight overlays
    """
    if fitz is None or pix is None:
        return pix
    if np is None:
        return pix
    try:
        h, w, n = int(pix.height), int(pix.width), int(pix.n)
        if h <= 0 or w <= 0 or n < 3:
            return pix
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape((h, w, n))
        rgb = arr[:, :, :3]
        # Use uint32 to avoid overflow in weighted grayscale math:
        # 587 * 255 = 149685 (exceeds uint16 max 65535).
        r = rgb[:, :, 0].astype(np.uint32)
        g = rgb[:, :, 1].astype(np.uint32)
        b = rgb[:, :, 2].astype(np.uint32)

        red_pixels = (r >= 70) & (r >= (g + 6)) & (r >= (b + 6))
        if red_gate_mask is None:
            red_mask = np.zeros_like(red_pixels, dtype=bool)
        elif red_gate_mask.shape == red_pixels.shape:
            gate = red_gate_mask
            # Within symbol/dimension gate, accept both strict and soft red shades.
            red_mask = (red_pixels & gate) | (gate & (r >= (g + 2)) & (r >= (b + 2)))
        else:
            red_mask = np.zeros_like(red_pixels, dtype=bool)
        # Keep our fluorescent green highlights.
        green_mask = (g >= 165) & (r <= 200) & (b <= 170)
        keep_color = red_mask | green_mask

        gray = ((299 * r + 587 * g + 114 * b) // 1000).astype(np.uint8)
        # Some PDFs render with dark background; auto-invert to keep white paper look.
        dark_frac = float(np.mean(gray < 70))
        light_frac = float(np.mean(gray > 200))
        mean_gray = float(np.mean(gray))
        if mean_gray < 118.0 or (dark_frac > 0.50 and light_frac < 0.28):
            gray = (255 - gray).astype(np.uint8)
        bw = np.where(gray >= int(PACKET_BW_THRESHOLD), 255, 0).astype(np.uint8)
        if PACKET_INVERT_BW:
            bw = (255 - bw).astype(np.uint8)

        out = np.empty((h, w, 3), dtype=np.uint8)
        out[:, :, 0] = bw
        out[:, :, 1] = bw
        out[:, :, 2] = bw
        out[keep_color] = rgb[keep_color]
        return fitz.Pixmap(fitz.csRGB, w, h, out.tobytes(), False)
    except Exception:
        return pix


def _grayscale_pixmap(pix):
    """Convert a pixmap to plain grayscale (no threshold / no invert)."""
    if fitz is None or pix is None or np is None:
        return pix
    try:
        h, w, n = int(pix.height), int(pix.width), int(pix.n)
        if h <= 0 or w <= 0 or n < 3:
            return pix
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape((h, w, n))
        rgb = arr[:, :, :3]
        # Use uint32 to avoid overflow in weighted grayscale math.
        r = rgb[:, :, 0].astype(np.uint32)
        g = rgb[:, :, 1].astype(np.uint32)
        b = rgb[:, :, 2].astype(np.uint32)
        gray = ((299 * r + 587 * g + 114 * b) // 1000).astype(np.uint8)
        out = np.empty((h, w, 3), dtype=np.uint8)
        out[:, :, 0] = gray
        out[:, :, 1] = gray
        out[:, :, 2] = gray
        return fitz.Pixmap(fitz.csRGB, w, h, out.tobytes(), False)
    except Exception:
        return pix


def _layer_is_target(layer_name: object) -> bool:
    s = str(layer_name or "").lower()
    if not s:
        return False
    return "dimension" in s


def _is_title_layer(layer_name: object) -> bool:
    s = str(layer_name or "").lower()
    return "title" in s


def _is_red_rgb(color: object) -> bool:
    return _is_red_rgb_impl(color)


def _is_red_text_color(color: object) -> bool:
    return _is_red_text_color_impl(color)


def _color_to_rgb(color: object) -> Optional[Tuple[float, float, float]]:
    return _color_to_rgb_impl(color)


def _looks_like_dimension_text(s: str) -> bool:
    return _looks_like_dimension_text_impl(s)


def _highlight_red_target_layers(
    page,
    gate_boxes: Optional[List[Tuple[float, float, float, float]]] = None,
    dim_boxes: Optional[List[Tuple[float, float, float, float]]] = None,
    draw: bool = True,
) -> None:
    _highlight_red_target_layers_impl(
        page,
        gate_boxes=gate_boxes,
        dim_boxes=dim_boxes,
        draw=draw,
        fitz_module=fitz,
        layer_is_target_fn=_layer_is_target,
        draw_dim_mask_fn=_draw_dim_mask,
    )


def _highlight_red_text(
    page,
    gate_boxes: Optional[List[Tuple[float, float, float, float]]] = None,
    dim_boxes: Optional[List[Tuple[float, float, float, float]]] = None,
    overlay_runs: Optional[List[dict]] = None,
    draw: bool = True,
) -> None:
    _highlight_red_text_impl(
        page,
        gate_boxes=gate_boxes,
        dim_boxes=dim_boxes,
        overlay_runs=overlay_runs,
        draw=draw,
        fitz_module=fitz,
        is_symbol_or_dimension_layer_name_fn=_is_symbol_or_dimension_layer_name,
        draw_dim_mask_fn=_draw_dim_mask,
    )


def _collect_red_symbol_dimension_chars(page) -> List[dict]:
    return _collect_red_symbol_dimension_chars_impl(
        page,
        is_symbol_or_dimension_layer_name_fn=_is_symbol_or_dimension_layer_name,
    )


def _overlay_red_symbol_dimension_chars(page, chars: List[dict], *, fitz_module=None) -> None:
    _overlay_red_symbol_dimension_chars_impl(page, chars, fitz_module=fitz_module or fitz)


def _overlay_red_text_runs(page, runs: List[dict], *, fitz_module=None) -> None:
    _overlay_red_text_runs_impl(page, runs, fitz_module=fitz_module or fitz)


def _grayscale_title_layer(page) -> None:
    _grayscale_title_layer_impl(
        page,
        fitz_module=fitz,
        is_title_layer_fn=_is_title_layer,
        title_grayscale_color=TITLE_GRAYSCALE_COLOR,
        title_grayscale_opacity=TITLE_GRAYSCALE_OPACITY,
    )


def _draw_dim_mask(page, rect) -> None:
    x0, y0, x1, y1 = float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)
    # Ensure thin lines still get visible mask boxes.
    if (x1 - x0) < 1.0:
        cx = 0.5 * (x0 + x1)
        x0, x1 = cx - 0.5, cx + 0.5
    if (y1 - y0) < 1.0:
        cy = 0.5 * (y0 + y1)
        y0, y1 = cy - 0.5, cy + 0.5
    x0 -= DIM_HILITE_PAD_X
    y0 -= DIM_HILITE_PAD_Y_TOP
    x1 += DIM_HILITE_PAD_X
    y1 += DIM_HILITE_PAD_Y_BOTTOM
    _draw_rounded_stroke_rect(
        page,
        fitz.Rect(x0, y0, x1, y1),
        stroke_color=DIM_HILITE_COLOR,
        stroke_width=DIM_HILITE_STROKE_WIDTH,
        stroke_opacity=DIM_HILITE_STROKE_OPACITY,
        radius=DIM_HILITE_RADIUS,
    )


def _draw_rounded_filled_rect(page, rect, fill_color, fill_opacity, radius: float) -> None:
    x0, y0, x1, y1 = float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)
    if x1 <= x0 or y1 <= y0:
        return
    r = max(0.0, min(float(radius), (x1 - x0) * 0.5, (y1 - y0) * 0.5))
    if r <= 0.1:
        page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=None, fill=fill_color, fill_opacity=fill_opacity)
        return

    # Center bands.
    page.draw_rect(fitz.Rect(x0 + r, y0, x1 - r, y1), color=None, fill=fill_color, fill_opacity=fill_opacity)
    page.draw_rect(fitz.Rect(x0, y0 + r, x1, y1 - r), color=None, fill=fill_color, fill_opacity=fill_opacity)
    # Rounded corners.
    page.draw_circle(fitz.Point(x0 + r, y0 + r), r, color=None, fill=fill_color, fill_opacity=fill_opacity)
    page.draw_circle(fitz.Point(x1 - r, y0 + r), r, color=None, fill=fill_color, fill_opacity=fill_opacity)
    page.draw_circle(fitz.Point(x0 + r, y1 - r), r, color=None, fill=fill_color, fill_opacity=fill_opacity)
    page.draw_circle(fitz.Point(x1 - r, y1 - r), r, color=None, fill=fill_color, fill_opacity=fill_opacity)


def _draw_rounded_stroke_rect(page, rect, stroke_color, stroke_width: float, stroke_opacity: float, radius: float) -> None:
    x0, y0, x1, y1 = float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)
    if x1 <= x0 or y1 <= y0:
        return

    r = max(0.0, min(float(radius), (x1 - x0) * 0.5, (y1 - y0) * 0.5))
    if r <= 0.1:
        page.draw_rect(
            fitz.Rect(x0, y0, x1, y1),
            color=stroke_color,
            fill=None,
            width=stroke_width,
            stroke_opacity=stroke_opacity,
        )
        return

    # Cubic-Bezier circle approximation factor for quarter arcs.
    k = 0.552284749831 * r
    sh = page.new_shape()

    # Top edge and top-right corner.
    sh.draw_line((x0 + r, y0), (x1 - r, y0))
    sh.draw_bezier((x1 - r, y0), (x1 - r + k, y0), (x1, y0 + r - k), (x1, y0 + r))
    # Right edge and bottom-right corner.
    sh.draw_line((x1, y0 + r), (x1, y1 - r))
    sh.draw_bezier((x1, y1 - r), (x1, y1 - r + k), (x1 - r + k, y1), (x1 - r, y1))
    # Bottom edge and bottom-left corner.
    sh.draw_line((x1 - r, y1), (x0 + r, y1))
    sh.draw_bezier((x0 + r, y1), (x0 + r - k, y1), (x0, y1 - r + k), (x0, y1 - r))
    # Left edge and top-left corner.
    sh.draw_line((x0, y1 - r), (x0, y0 + r))
    sh.draw_bezier((x0, y0 + r), (x0, y0 + r - k), (x0 + r - k, y0), (x0 + r, y0))

    sh.finish(
        width=stroke_width,
        color=stroke_color,
        fill=None,
        lineCap=1,
        lineJoin=1,
        closePath=True,
        stroke_opacity=stroke_opacity,
    )
    sh.commit()
def map_to_eng_release(sym_path: str) -> str:
    return _map_to_eng_release_impl(sym_path, eng_release_map=ENG_RELEASE_MAP)


def _force_w_candidates(sym_path: str) -> List[str]:
    return _force_w_candidates_impl(
        sym_path,
        w_release_root=W_RELEASE_ROOT,
        eng_release_map=ENG_RELEASE_MAP,
    )


def _process_packet_part(
    i: int,
    p: PartRow,
    *,
    resolver: Callable[[str, str], Optional[str]],
    render_mode: str,
) -> dict:
    return _process_packet_part_impl(
        i,
        p,
        resolver=resolver,
        render_mode=render_mode,
        fitz_module=fitz,
        apply_packet_layer_policy_fn=_apply_packet_layer_policy,
        highlight_red_target_layers_fn=_highlight_red_target_layers,
        highlight_red_text_fn=_highlight_red_text,
        render_page_pixmap_fn=_render_page_pixmap,
        gate_mask_from_boxes_fn=_gate_mask_from_boxes,
        grayscale_preserve_red_and_highlight_fn=_grayscale_preserve_red_and_highlight,
        pix_to_image_stream_fn=_pix_to_image_stream,
        packet_raster_dpi=PACKET_RASTER_DPI,
    )


def resolve_asset(sym_path: str, ext: str) -> str:
    return _resolve_asset_impl(
        sym_path,
        ext,
        w_release_root=W_RELEASE_ROOT,
        eng_release_map=ENG_RELEASE_MAP,
    )


def _apply_packet_result(
    dst,
    res: dict,
    *,
    progress_done: int,
    progress_total: int,
    progress_cb: Optional[Callable[[int, int, str], None]],
    span,
    emit_progress: bool = True,
) -> Tuple[int, int, int]:
    return _apply_packet_result_impl(
        dst,
        res,
        fitz_module=fitz,
        progress_done=progress_done,
        progress_total=progress_total,
        progress_cb=progress_cb,
        span=span,
        emit_progress=emit_progress,
        first_toggle_layer_aliases_fn=_first_toggle_layer_aliases,
        collect_layer_zero_masks_fn=_collect_layer_zero_masks,
        apply_packet_layer_policy_fn=_apply_packet_layer_policy,
        collect_red_symbol_dimension_chars_fn=_collect_red_symbol_dimension_chars,
        highlight_red_target_layers_fn=_highlight_red_target_layers,
        highlight_red_text_fn=_highlight_red_text,
        erase_layer_zero_overlays_fn=_erase_layer_zero_overlays,
        draw_dim_mask_fn=_draw_dim_mask,
        overlay_red_symbol_dimension_chars_fn=_overlay_red_symbol_dimension_chars,
        overlay_red_text_runs_fn=_overlay_red_text_runs,
        format_qty_watermark_text_fn=_format_qty_watermark_text,
        draw_rounded_stroke_rect_fn=_draw_rounded_stroke_rect,
        watermark_stroke_color=WATERMARK_STROKE_COLOR,
        watermark_stroke_width=WATERMARK_STROKE_WIDTH,
        watermark_stroke_opacity=WATERMARK_STROKE_OPACITY,
        watermark_radius=WATERMARK_RADIUS,
        watermark_text_scale=WATERMARK_TEXT_SCALE,
        watermark_text_color=WATERMARK_TEXT_COLOR,
    )


def build_watermarked_packet(
    parts: List[PartRow],
    out_pdf_path: str,
    resolve_asset_fn: Optional[Callable[[str, str], Optional[str]]] = None,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    should_cancel_cb: Optional[Callable[[], bool]] = None,
    max_workers: Optional[int] = None,
    render_mode: str = "raster",
) -> Tuple[int, int]:
    """
    Create a concatenated packet and stamp QTY in bottom-left on each page.
    Rendering policy:
    - Preserve red + highlight colors.
    - Convert everything else to grayscale.
    - Always suppresses OCG layer '0' (including names like '0 (ANSI)') when present.
    Returns: (pages_written, missing_pdf_count)
    """
    ensure_parent_dir(out_pdf_path)

    pages = 0
    missing = 0

    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is not available. Install it in your runtime Python.")

    resolver = resolve_asset_fn or resolve_asset
    total = len(parts)
    progress_total = max(1, total)
    configured_workers = int(PACKET_MAX_WORKERS)
    if max_workers is not None:
        try:
            configured_workers = int(max_workers)
        except Exception:
            configured_workers = int(PACKET_MAX_WORKERS)
    configured_workers = max(1, min(8, configured_workers))
    max_workers = min(configured_workers, max(1, total))
    span = rt.begin(
        "packet_engine",
        out_pdf_path=out_pdf_path,
        total_parts=int(total),
        workers=int(max_workers),
        dpi=int(PACKET_RASTER_DPI),
        jpeg_quality=int(PACKET_JPEG_QUALITY),
        suppress_layer_0=True,
    )

    def _should_cancel() -> bool:
        if should_cancel_cb is None:
            return False
        try:
            return bool(should_cancel_cb())
        except Exception:
            return False

    if _should_cancel():
        span.skip(reason="user_canceled", pages=0, missing=0)
        raise PacketBuildCanceled(
            "Packet build canceled before start.",
            pages=0,
            missing=0,
        )

    dst = fitz.open()
    if progress_cb is not None:
        try:
            progress_cb(0, progress_total, "Starting packet build")
        except Exception:
            pass

    try:
        total_part_ms = 0
        max_part_ms = 0
        done_parts = 0
        canceled = False
        last_failure_status = ""
        if max_workers <= 1:
            for i, p in enumerate(parts, start=1):
                if _should_cancel():
                    canceled = True
                    break
                res = _process_packet_part(
                    i,
                    p,
                    resolver=resolver,
                    render_mode=render_mode,
                )
                miss_i, pages_i, elapsed_ms = _apply_packet_result(
                    dst,
                    res,
                    progress_done=i,
                    progress_total=progress_total,
                    progress_cb=progress_cb,
                    span=span,
                )
                missing += miss_i
                pages += pages_i
                total_part_ms += elapsed_ms
                max_part_ms = max(max_part_ms, elapsed_ms)
                done_parts += 1
                if pages_i <= 0:
                    last_failure_status = str(res.get("status") or last_failure_status or f"Part {i}")
        else:
            # Threaded processing with completion-driven progress + ordered writes.
            next_write_idx = 1
            completed_count = 0
            ready: dict[int, dict] = {}
            idx_parts = list(enumerate(parts, start=1))
            submitted = 0
            pool = ThreadPoolExecutor(max_workers=max_workers)
            in_flight: dict[object, int] = {}
            try:
                def _submit_next() -> bool:
                    nonlocal submitted
                    if submitted >= len(idx_parts):
                        return False
                    idx, part = idx_parts[submitted]
                    submitted += 1
                    fut = pool.submit(
                        _process_packet_part,
                        idx,
                        part,
                        resolver=resolver,
                        render_mode=render_mode,
                    )
                    in_flight[fut] = idx
                    return True

                for _ in range(min(max_workers, len(idx_parts))):
                    _submit_next()

                while in_flight:
                    if _should_cancel():
                        canceled = True
                        break
                    done_set, _ = wait(set(in_flight.keys()), timeout=0.2, return_when=FIRST_COMPLETED)
                    if not done_set:
                        continue
                    for fut in done_set:
                        idx = int(in_flight.pop(fut))
                        try:
                            res = fut.result()
                        except Exception as exc:
                            detail = _format_error(exc)
                            res = {
                                "idx": idx,
                                "status": f"Part {idx} (worker failed: {detail})",
                                "missing": 1,
                                "skip": True,
                                "error_stage": "worker_failed",
                                "error": detail,
                                "elapsed_ms": 0,
                            }
                        ready[idx] = res
                        completed_count += 1
                        status_done = str(res.get("status") or f"Part {idx}")
                        if progress_cb is not None:
                            try:
                                progress_cb(completed_count, progress_total, status_done)
                            except Exception:
                                pass
                        try:
                            span.progress(completed_count, progress_total, status_done)
                        except Exception:
                            pass
                        # Emit ordered results as soon as contiguous prefixes are available.
                        while next_write_idx in ready:
                            res2 = ready.pop(next_write_idx)
                            miss_i, pages_i, elapsed_ms = _apply_packet_result(
                                dst,
                                res2,
                                progress_done=completed_count,
                                progress_total=progress_total,
                                progress_cb=progress_cb,
                                span=span,
                                emit_progress=False,
                            )
                            missing += miss_i
                            pages += pages_i
                            total_part_ms += elapsed_ms
                            max_part_ms = max(max_part_ms, elapsed_ms)
                            done_parts += 1
                            if pages_i <= 0:
                                last_failure_status = str(
                                    res2.get("status") or last_failure_status or f"Part {next_write_idx}"
                                )
                            next_write_idx += 1
                        if not canceled:
                            _submit_next()
            finally:
                if canceled:
                    for fut in list(in_flight.keys()):
                        try:
                            fut.cancel()
                        except Exception:
                            pass
                    try:
                        pool.shutdown(wait=False, cancel_futures=True)
                    except TypeError:
                        pool.shutdown(wait=False)
                else:
                    pool.shutdown(wait=True)

        if canceled:
            if progress_cb is not None:
                try:
                    progress_cb(done_parts, progress_total, "Packet build canceled")
                except Exception:
                    pass
            span.skip(
                reason="user_canceled",
                pages=int(pages),
                missing=int(missing),
                processed_parts=int(done_parts),
            )
            raise PacketBuildCanceled(
                "Packet build canceled.",
                pages=int(pages),
                missing=int(missing),
            )

        if int(pages) <= 0 or int(getattr(dst, "page_count", 0) or 0) <= 0:
            if progress_cb is not None:
                try:
                    progress_cb(progress_total, progress_total, "No packet pages created")
                except Exception:
                    pass
            span.skip(
                reason="no_packet_pages",
                pages=int(pages),
                missing=int(missing),
                processed_parts=int(done_parts),
            )
            detail = str(last_failure_status or "").strip()
            msg = "No packet pages were created from the selected rows."
            if detail:
                msg += f" Last failure: {detail}"
            raise PacketBuildEmpty(
                msg,
                pages=int(pages),
                missing=int(missing),
            )

        if progress_cb is not None:
            try:
                progress_cb(progress_total, progress_total, "Saving packet to temp file")
            except Exception:
                pass

        tmp_fd = -1
        tmp_pdf_path = ""
        try:
            tmp_fd, tmp_pdf_path = tempfile.mkstemp(prefix="rk_packet_", suffix=".pdf")
            os.close(tmp_fd)
            tmp_fd = -1
            dst.save(
                tmp_pdf_path,
                deflate=False,
                deflate_images=False,
                garbage=0,
            )
            if progress_cb is not None:
                try:
                    progress_cb(progress_total, progress_total, "Copying packet file")
                except Exception:
                    pass
            shutil.copyfile(tmp_pdf_path, out_pdf_path)
        finally:
            if tmp_fd >= 0:
                try:
                    os.close(tmp_fd)
                except Exception:
                    pass
            if tmp_pdf_path:
                try:
                    os.remove(tmp_pdf_path)
                except Exception:
                    pass
        out_size = 0
        try:
            out_size = int(os.path.getsize(out_pdf_path))
        except Exception:
            out_size = 0

        if progress_cb is not None:
            try:
                progress_cb(progress_total, progress_total, "Packet complete")
            except Exception:
                pass
        span.success(
            pages=int(pages),
            missing=int(missing),
            out_size_bytes=int(out_size),
            avg_part_ms=int(total_part_ms / max(1, done_parts)),
            max_part_ms=int(max_part_ms),
        )
    except Exception as exc:
        span.fail(exc)
        raise
    finally:
        try:
            dst.close()
        except Exception:
            pass
    return pages, missing
