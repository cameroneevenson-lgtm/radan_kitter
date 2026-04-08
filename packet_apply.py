from __future__ import annotations

import os
from typing import Callable, List, Optional, Tuple


def _format_error(exc: Exception) -> str:
    name = type(exc).__name__
    text = str(exc or "").strip()
    return f"{name}: {text}" if text else name


def _mark_apply_failure(res: dict, status: str, stage: str, exc: Exception) -> str:
    detail = _format_error(exc)
    status_text = f"{status} ({stage}: {detail})"
    res["status"] = status_text
    res["error_stage"] = stage
    res["error"] = detail
    return status_text


def apply_packet_result(
    dst,
    res: dict,
    *,
    fitz_module,
    progress_done: int,
    progress_total: int,
    progress_cb: Optional[Callable[[int, int, str], None]],
    span,
    emit_progress: bool,
    first_toggle_layer_aliases_fn: Callable[[object], List[str]],
    collect_layer_zero_masks_fn: Callable[..., Tuple[List[dict], List[Tuple[float, float, float, float]], float]],
    apply_packet_layer_policy_fn: Callable[[object], bool],
    collect_red_symbol_dimension_chars_fn: Callable[..., List[dict]],
    highlight_red_target_layers_fn: Callable[..., None],
    highlight_red_text_fn: Callable[..., None],
    erase_layer_zero_overlays_fn: Callable[..., None],
    draw_dim_mask_fn: Callable[[object, object], None],
    overlay_red_symbol_dimension_chars_fn: Callable[..., None],
    overlay_red_text_runs_fn: Callable[..., None],
    format_qty_watermark_text_fn: Callable[[int, int], str],
    draw_rounded_stroke_rect_fn: Callable[..., None],
    watermark_stroke_color,
    watermark_stroke_width: float,
    watermark_stroke_opacity: float,
    watermark_radius: float,
    watermark_text_scale: float,
    watermark_text_color,
) -> Tuple[int, int, int]:
    index = int(res.get("idx", 0) or 0)
    status = str(res.get("status") or f"Part {index}")
    missing_inc = int(res.get("missing", 0) or 0)
    pages_inc = 0
    elapsed_ms = int(res.get("elapsed_ms", 0) or 0)

    if not bool(res.get("skip", False)):
        mode = str(res.get("mode", "raster") or "raster").strip().lower()
        qty = int(res.get("qty", 1) or 1)
        extra = int(res.get("extra", 0) or 0)
        dst_page = None
        if mode == "vector":
            pdf_path = str(res.get("pdf_path") or "").strip()
            src = None
            flat = None
            try:
                if pdf_path and os.path.exists(pdf_path):
                    src = fitz_module.open(pdf_path)
                    if src.page_count >= 1:
                        src_page = src.load_page(0)
                        zero_aliases = first_toggle_layer_aliases_fn(src)
                        zero_draw_masks, zero_text_boxes, zero_page_area = collect_layer_zero_masks_fn(
                            src_page,
                            zero_layer_aliases=zero_aliases,
                        )
                        apply_packet_layer_policy_fn(src)
                        red_chars = collect_red_symbol_dimension_chars_fn(src_page)
                        red_dim_text_runs: List[dict] = []
                        dim_boxes: List[Tuple[float, float, float, float]] = []
                        highlight_red_target_layers_fn(src_page, dim_boxes=dim_boxes, draw=False)
                        highlight_red_text_fn(
                            src_page,
                            dim_boxes=dim_boxes,
                            overlay_runs=red_dim_text_runs,
                            draw=False,
                        )

                        try:
                            src_page.recolor(1)
                        except Exception:
                            pass

                        try:
                            flat_bytes = src.convert_to_pdf()
                            flat = fitz_module.open("pdf", flat_bytes)
                        except Exception:
                            flat = None

                        render_doc = flat if (flat is not None and flat.page_count >= 1) else src
                        render_page = render_doc.load_page(0)
                        if zero_draw_masks or zero_text_boxes:
                            erase_layer_zero_overlays_fn(
                                render_page,
                                zero_draw_masks,
                                zero_text_boxes,
                                zero_page_area,
                                fitz_module=fitz_module,
                            )

                        seen_dim = set()
                        for bbox in dim_boxes:
                            try:
                                x0, y0, x1, y1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
                            except Exception:
                                continue
                            key = (round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2))
                            if key in seen_dim:
                                continue
                            seen_dim.add(key)
                            draw_dim_mask_fn(render_page, fitz_module.Rect(x0, y0, x1, y1))
                        overlay_red_symbol_dimension_chars_fn(render_page, red_chars, fitz_module=fitz_module)
                        overlay_red_text_runs_fn(render_page, red_dim_text_runs, fitz_module=fitz_module)
                        src_rect = render_page.rect
                        if float(src_rect.width) <= 1e-6 or float(src_rect.height) <= 1e-6:
                            missing_inc += 1
                        else:
                            dst_page = dst.new_page(width=float(src_rect.width), height=float(src_rect.height))
                            try:
                                dst_page.show_pdf_page(
                                    dst_page.rect,
                                    render_doc,
                                    0,
                                    keep_proportion=False,
                                    overlay=True,
                                )
                            except TypeError:
                                dst_page.show_pdf_page(dst_page.rect, render_doc, 0)
                            except Exception:
                                dst.delete_page(dst.page_count - 1)
                                dst.insert_pdf(render_doc, from_page=0, to_page=0)
                                dst_page = dst.load_page(dst.page_count - 1)
                            if dst_page is not None:
                                pages_inc = 1
                            else:
                                missing_inc += 1
                    else:
                        missing_inc += 1
                else:
                    missing_inc += 1
            except Exception as exc:
                status = _mark_apply_failure(res, status, "vector apply failed", exc)
                missing_inc += 1
            finally:
                if src is not None:
                    try:
                        src.close()
                    except Exception:
                        pass
                if flat is not None:
                    try:
                        flat.close()
                    except Exception:
                        pass
        else:
            width = float(res.get("w", 0.0) or 0.0)
            height = float(res.get("h", 0.0) or 0.0)
            img_stream = res.get("img_stream", b"") or b""
            if width > 1e-6 and height > 1e-6:
                dst_page = dst.new_page(width=width, height=height)
                rect = dst_page.rect
                if img_stream:
                    dst_page.insert_image(rect, stream=img_stream, keep_proportion=False, overlay=True)
                pages_inc = 1

        if dst_page is not None and pages_inc > 0:
            rect = dst_page.rect
            text = format_qty_watermark_text_fn(qty, extra)
            margin = 18
            font_size = 24 * watermark_text_scale
            box_h = 46 * watermark_text_scale
            x1 = margin
            y1 = rect.height - margin - box_h
            text_w = fitz_module.get_text_length(text, fontname="helv", fontsize=font_size)
            pad_x = 12
            x2 = x1 + text_w + (2 * pad_x)
            y2 = y1 + box_h
            draw_rounded_stroke_rect_fn(
                dst_page,
                fitz_module.Rect(x1, y1, x2, y2),
                stroke_color=watermark_stroke_color,
                stroke_width=watermark_stroke_width,
                stroke_opacity=watermark_stroke_opacity,
                radius=watermark_radius * watermark_text_scale,
            )
            dst_page.insert_text(
                fitz_module.Point(x1 + pad_x, y1 + box_h * 0.72),
                text,
                fontsize=font_size,
                color=watermark_text_color,
                fontname="helv",
            )

    if emit_progress:
        if progress_cb is not None:
            try:
                progress_cb(progress_done, progress_total, status)
            except Exception:
                pass
        try:
            span.progress(progress_done, progress_total, status)
        except Exception:
            pass
    return missing_inc, pages_inc, elapsed_ms
