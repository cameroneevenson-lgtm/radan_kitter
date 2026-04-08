from __future__ import annotations

import math
import os
from typing import Any, Callable, Dict, List, Optional, Tuple


def _format_error(exc: Exception) -> str:
    name = type(exc).__name__
    text = str(exc or "").strip()
    return f"{name}: {text}" if text else name


def _page_area(page) -> float:
    rect = page.rect
    return max(1e-9, float(rect.width) * float(rect.height))


def _layer_matches(layer: str, *tokens: str) -> bool:
    value = str(layer or "").lower()
    return any(token in value for token in tokens)


def _to_rgb255(color_val: Any) -> Optional[Tuple[float, float, float]]:
    if color_val is None:
        return None
    if isinstance(color_val, int):
        iv = int(color_val) & 0xFFFFFF
        return (float((iv >> 16) & 0xFF), float((iv >> 8) & 0xFF), float(iv & 0xFF))
    if isinstance(color_val, (list, tuple)) and len(color_val) >= 3:
        try:
            r = float(color_val[0])
            g = float(color_val[1])
            b = float(color_val[2])
        except Exception:
            return None
        if max(r, g, b) <= 1.2:
            return (255.0 * r, 255.0 * g, 255.0 * b)
        return (r, g, b)
    return None


def _is_red(color_val: Any) -> bool:
    rgb = _to_rgb255(color_val)
    if rgb is None:
        return False
    r, g, b = rgb
    return bool(r >= 150.0 and g <= 125.0 and b <= 125.0 and r >= (g + 24.0) and r >= (b + 24.0))


def compute_pdf_features_vector(
    pdf_path: str,
    *,
    pdf_signal_cols: List[str],
    nan_fn: Callable[[], float],
    safe_float_fn: Callable[[object], float],
    fitz_module,
) -> Dict[str, float]:
    out = {key: nan_fn() for key in pdf_signal_cols}

    if not pdf_path or not os.path.exists(pdf_path) or fitz_module is None:
        return out

    try:
        doc = fitz_module.open(pdf_path)
    except Exception as exc:
        raise RuntimeError(f"Failed to open PDF '{pdf_path}': {_format_error(exc)}") from exc

    try:
        if doc.page_count < 1:
            return out
        page = doc.load_page(0)
        page_area = _page_area(page)

        try:
            drawings = page.get_drawings()
        except Exception:
            drawings = []

        total_geom = 0
        dim_geom = 0
        red_dim_geom = 0
        bend_geom = 0
        bend_geom_area = 0.0

        for drawing in drawings:
            items = drawing.get("items", []) or []
            geom_count = max(1, len(items))
            total_geom += geom_count
            layer = str(drawing.get("layer") or "")
            if _layer_matches(layer, "dimension", "dim"):
                dim_geom += geom_count
                if _is_red(drawing.get("color")) or _is_red(drawing.get("fill")):
                    red_dim_geom += geom_count
            if _layer_matches(layer, "bend", "centerline", "centreline"):
                bend_geom += geom_count
                rect = drawing.get("rect", None)
                if rect is not None:
                    try:
                        bx0 = float(rect.x0)
                        by0 = float(rect.y0)
                        bx1 = float(rect.x1)
                        by1 = float(rect.y1)
                        bw = max(0.0, bx1 - bx0)
                        bh = max(0.0, by1 - by0)
                        bend_geom_area += (bw * bh)
                    except Exception:
                        pass

        dim_chars = 0
        red_dim_chars = 0
        try:
            traces = page.get_texttrace()
        except Exception:
            traces = []
        for trace in traces:
            chars = trace.get("chars", []) or []
            char_count = 0
            for ch in chars:
                if isinstance(ch, (list, tuple)) and ch:
                    char_count += 1
            if char_count <= 0:
                continue
            layer = str(trace.get("layer") or "")
            if _layer_matches(layer, "dimension", "dim"):
                dim_chars += char_count
                if _is_red(trace.get("color")):
                    red_dim_chars += char_count

        out["pdf_bendline_score"] = safe_float_fn(bend_geom / max(1.0, float(total_geom)))
        bend_norm_area = float(bend_geom_area) if bend_geom_area > 1e-9 else float(page_area)
        bend_density_raw = float(bend_geom) / max(1.0, bend_norm_area / 1000.0)
        out["pdf_bendline_entity_density"] = safe_float_fn(math.log1p(max(0.0, bend_density_raw)))

        dim_weight = float(dim_geom) + float(dim_chars)
        out["pdf_dim_density"] = safe_float_fn(dim_weight / max(1.0, page_area / 1000.0))
        red_dim_weight = float(red_dim_geom) + float(red_dim_chars)
        out["pdf_red_dim_density"] = safe_float_fn(red_dim_weight / max(1.0, page_area / 1000.0))
    finally:
        doc.close()

    return out
