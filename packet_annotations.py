from __future__ import annotations

import re
from typing import Any, Callable, List, Optional, Tuple


def is_red_rgb(color: object) -> bool:
    if not isinstance(color, tuple) or len(color) < 3:
        return False
    try:
        r, g, b = float(color[0]), float(color[1]), float(color[2])
    except Exception:
        return False
    return r >= 0.28 and r >= (g + 0.05) and r >= (b + 0.05)


def is_red_text_color(color: object) -> bool:
    if isinstance(color, int):
        r = (color >> 16) & 255
        g = (color >> 8) & 255
        b = color & 255
        return r >= 70 and r >= (g + 8) and r >= (b + 8)
    return is_red_rgb(color)


def color_to_rgb(color: object) -> Optional[Tuple[float, float, float]]:
    if isinstance(color, int):
        r = ((color >> 16) & 255) / 255.0
        g = ((color >> 8) & 255) / 255.0
        b = (color & 255) / 255.0
        return (float(r), float(g), float(b))
    if isinstance(color, tuple) and len(color) >= 3:
        try:
            return (float(color[0]), float(color[1]), float(color[2]))
        except Exception:
            return None
    return None


def looks_like_dimension_text(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    return bool(re.fullmatch(r"[0-9.\-+/\"' xX]+", value))


def highlight_red_target_layers(
    page,
    gate_boxes: Optional[List[Tuple[float, float, float, float]]] = None,
    dim_boxes: Optional[List[Tuple[float, float, float, float]]] = None,
    draw: bool = True,
    *,
    fitz_module,
    layer_is_target_fn: Callable[[object], bool],
    draw_dim_mask_fn: Callable[[object, object], None],
) -> None:
    try:
        drawings = page.get_drawings()
    except Exception:
        return
    for drawing in drawings:
        is_red = is_red_rgb(drawing.get("color")) or is_red_rgb(drawing.get("fill"))
        if not is_red:
            continue
        rect = drawing.get("rect", None)
        if rect is None:
            continue
        if not layer_is_target_fn(drawing.get("layer")):
            continue
        bbox = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
        if gate_boxes is not None:
            gate_boxes.append(bbox)
        if dim_boxes is not None:
            dim_boxes.append(bbox)
        if draw:
            draw_dim_mask_fn(page, fitz_module.Rect(rect.x0, rect.y0, rect.x1, rect.y1))


def highlight_red_text(
    page,
    gate_boxes: Optional[List[Tuple[float, float, float, float]]] = None,
    dim_boxes: Optional[List[Tuple[float, float, float, float]]] = None,
    overlay_runs: Optional[List[dict]] = None,
    draw: bool = True,
    *,
    fitz_module,
    is_symbol_or_dimension_layer_name_fn: Callable[[object], bool],
    draw_dim_mask_fn: Callable[[object, object], None],
) -> None:
    try:
        traces = page.get_texttrace()
    except Exception:
        return
    items = []
    for trace in traces:
        layer = str(trace.get("layer") or "")
        if not is_red_text_color(trace.get("color", None)):
            continue
        chars = trace.get("chars", []) or []
        text = "".join(chr(ch[0]) for ch in chars if isinstance(ch, (list, tuple)) and ch)
        if not text.strip():
            continue
        bbox = trace.get("bbox", None)
        if not bbox or len(bbox) != 4:
            continue
        if gate_boxes is not None and is_symbol_or_dimension_layer_name_fn(layer):
            gate_boxes.append((float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])))

        layer_ok = "dimension" in layer.lower()
        unlabeled_dim_text = (not layer.strip()) and looks_like_dimension_text(text)
        if not looks_like_dimension_text(text):
            continue
        if not (layer_ok or unlabeled_dim_text):
            continue

        items.append(
            {
                "layer": layer.lower().strip(),
                "txt": text,
                "x0": float(bbox[0]),
                "y0": float(bbox[1]),
                "x1": float(bbox[2]),
                "y1": float(bbox[3]),
                "size": float(trace.get("size", 0.0) or 0.0),
                "color": color_to_rgb(trace.get("color", None)) or (1.0, 0.0, 0.0),
                "opacity": float(trace.get("opacity", 1.0) or 1.0),
            }
        )

    if not items:
        return

    items.sort(key=lambda item: (item["layer"], item["y0"], item["x0"]))
    grouped = []
    current = None
    for item in items:
        if current is None:
            current = dict(item)
            continue

        same_layer = item["layer"] == current["layer"]
        y_tol = max(1.6, 0.45 * max(current["size"], item["size"], 1.0))
        same_line = abs(item["y0"] - current["y0"]) <= y_tol
        gap = item["x0"] - current["x1"]
        x_tol = max(6.0, 1.5 * max(current["size"], item["size"], 1.0))
        adjacent = -1.0 <= gap <= x_tol

        if same_layer and same_line and adjacent:
            current["x0"] = min(current["x0"], item["x0"])
            current["y0"] = min(current["y0"], item["y0"])
            current["x1"] = max(current["x1"], item["x1"])
            current["y1"] = max(current["y1"], item["y1"])
            current["txt"] += item["txt"]
            current["size"] = max(current["size"], item["size"])
        else:
            grouped.append(current)
            current = dict(item)

    if current is not None:
        grouped.append(current)

    for item in grouped:
        bbox = (float(item["x0"]), float(item["y0"]), float(item["x1"]), float(item["y1"]))
        if dim_boxes is not None:
            dim_boxes.append(bbox)
        if overlay_runs is not None:
            overlay_runs.append(
                {
                    "txt": str(item.get("txt", "") or ""),
                    "bbox": bbox,
                    "size": float(item.get("size", 0.0) or 0.0),
                    "color": item.get("color", (1.0, 0.0, 0.0)),
                    "opacity": float(item.get("opacity", 1.0) or 1.0),
                }
            )
        if draw:
            draw_dim_mask_fn(page, fitz_module.Rect(item["x0"], item["y0"], item["x1"], item["y1"]))


def collect_red_symbol_dimension_chars(
    page,
    *,
    is_symbol_or_dimension_layer_name_fn: Callable[[object], bool],
) -> List[dict]:
    out: List[dict] = []
    try:
        traces = page.get_texttrace()
    except Exception:
        return out

    for trace in traces:
        layer = str(trace.get("layer") or "")
        color_raw = trace.get("color", None)
        if not is_red_text_color(color_raw):
            continue
        chars = trace.get("chars", []) or []
        run_text = "".join(
            chr(ch[0]) for ch in chars
            if isinstance(ch, (list, tuple)) and ch and isinstance(ch[0], int)
        )
        layer_ok = is_symbol_or_dimension_layer_name_fn(layer)
        unlabeled_dim_text = (not layer.strip()) and looks_like_dimension_text(run_text)
        if not (layer_ok or unlabeled_dim_text):
            continue
        rgb = color_to_rgb(color_raw) or (1.0, 0.0, 0.0)
        try:
            size = float(trace.get("size", 0.0) or 0.0)
        except Exception:
            size = 0.0
        if size <= 0.0:
            size = 9.0
        try:
            opacity = float(trace.get("opacity", 1.0) or 1.0)
        except Exception:
            opacity = 1.0
        for ch in chars:
            if not isinstance(ch, (list, tuple)) or len(ch) < 3:
                continue
            try:
                cp = int(ch[0])
            except Exception:
                continue
            if cp <= 0:
                continue
            try:
                text = chr(cp)
            except Exception:
                continue
            if not text.strip():
                continue
            origin = ch[2]
            try:
                ox = float(origin[0])
                oy = float(origin[1])
            except Exception:
                continue
            out.append(
                {
                    "txt": text,
                    "origin": (ox, oy),
                    "size": size,
                    "color": rgb,
                    "opacity": opacity,
                }
            )
    return out


def overlay_red_symbol_dimension_chars(page, chars: List[dict], *, fitz_module) -> None:
    if not chars:
        return
    for item in chars:
        text = str(item.get("txt", "") or "")
        if not text:
            continue
        try:
            x, y = item.get("origin", (0.0, 0.0))
            size = max(4.0, float(item.get("size", 9.0) or 9.0))
            color = item.get("color", (1.0, 0.0, 0.0))
            opacity = max(0.0, min(1.0, float(item.get("opacity", 1.0) or 1.0)))
        except Exception:
            continue
        try:
            page.insert_text(
                fitz_module.Point(float(x), float(y)),
                text,
                fontsize=size,
                fontname="helv",
                color=color,
                fill_opacity=opacity,
                overlay=True,
            )
        except TypeError:
            try:
                page.insert_text(
                    fitz_module.Point(float(x), float(y)),
                    text,
                    fontsize=size,
                    fontname="helv",
                    color=color,
                    overlay=True,
                )
            except Exception:
                continue
        except Exception:
            continue


def overlay_red_text_runs(page, runs: List[dict], *, fitz_module) -> None:
    if not runs:
        return
    seen: set[Tuple[str, float, float, float, float]] = set()
    for item in runs:
        text = str(item.get("txt", "") or "")
        bbox = item.get("bbox", None)
        if not text or not bbox or len(bbox) != 4:
            continue
        try:
            x0, y0, x1, y1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
        except Exception:
            continue
        if x1 <= x0 or y1 <= y0:
            continue
        key = (text, round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2))
        if key in seen:
            continue
        seen.add(key)
        try:
            size = max(4.0, float(item.get("size", 9.0) or 9.0))
            color = item.get("color", (1.0, 0.0, 0.0))
            opacity = max(0.0, min(1.0, float(item.get("opacity", 1.0) or 1.0)))
        except Exception:
            size = 9.0
            color = (1.0, 0.0, 0.0)
            opacity = 1.0
        rect = fitz_module.Rect(x0, y0, x1, y1)
        drew = False
        try:
            page.insert_textbox(
                rect,
                text,
                fontsize=size,
                fontname="helv",
                color=color,
                align=0,
                fill_opacity=opacity,
                overlay=True,
            )
            drew = True
        except TypeError:
            try:
                page.insert_textbox(
                    rect,
                    text,
                    fontsize=size,
                    fontname="helv",
                    color=color,
                    align=0,
                    overlay=True,
                )
                drew = True
            except Exception:
                drew = False
        except Exception:
            drew = False

        if not drew:
            try:
                page.draw_rect(
                    rect,
                    color=None,
                    fill=(1.0, 0.0, 0.0),
                    fill_opacity=min(0.28, max(0.12, opacity * 0.22)),
                    overlay=True,
                )
            except Exception:
                pass


def grayscale_title_layer(
    page,
    *,
    fitz_module,
    is_title_layer_fn: Callable[[object], bool],
    title_grayscale_color,
    title_grayscale_opacity: float,
) -> None:
    try:
        drawings = page.get_drawings()
    except Exception:
        return
    for drawing in drawings:
        if not is_title_layer_fn(drawing.get("layer")):
            continue
        rect = drawing.get("rect", None)
        if rect is None:
            continue
        page.draw_rect(
            fitz_module.Rect(float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)),
            color=None,
            fill=title_grayscale_color,
            fill_opacity=title_grayscale_opacity,
        )
