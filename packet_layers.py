from __future__ import annotations

import re
from typing import List, Optional, Tuple


def norm_layer_name(text: object) -> str:
    return "".join(ch.lower() for ch in str(text or "") if ch.isalnum())


def is_layer_zero_name(text: object) -> bool:
    raw = str(text or "").strip().lower()
    if raw == "0" or raw.startswith("layer 0"):
        return True
    if raw.startswith("0 (") and raw.endswith(")"):
        return True
    token = re.split(r"[\s()\[\]{}\-_/]+", raw)[0] if raw else ""
    if token == "0":
        return True
    normalized = norm_layer_name(text)
    if normalized in ("0", "0ansi", "layer0", "layer0ansi"):
        return True
    if normalized.startswith("0"):
        return True
    if normalized.startswith("0ansi") or normalized.startswith("layer0ansi"):
        return True
    return False


def ui_cfg_xref(cfg: dict) -> Optional[int]:
    for key in ("xref", "ocg", "oc"):
        try:
            value = cfg.get(key, None)
        except Exception:
            value = None
        try:
            xref = int(value)
        except Exception:
            continue
        if xref > 0:
            return xref
    return None


def first_toggle_layer_aliases(doc) -> List[str]:
    """
    Return normalized aliases for the first toggleable UI layer entry.
    Exporter-specific fallback: this is expected to be layer 0.
    """
    aliases: List[str] = []
    try:
        ui_cfgs = doc.layer_ui_configs() or []
    except Exception:
        ui_cfgs = []
    for cfg in ui_cfgs:
        try:
            num = int(cfg.get("number"))
        except Exception:
            continue
        if num < 0:
            continue
        raw = str(cfg.get("text", "") or cfg.get("name", "") or cfg.get("label", "")).strip()
        if raw:
            aliases.append(norm_layer_name(raw))
        break
    return [alias for alias in aliases if alias]


def matches_zero_layer_alias(layer_name: object, aliases: List[str]) -> bool:
    if is_layer_zero_name(layer_name):
        return True
    normalized = norm_layer_name(layer_name)
    if not normalized:
        return False
    return normalized in set(aliases or [])


def suppress_layer_zero(doc) -> None:
    """Force OCG layer named '0' OFF if present."""
    zero_xrefs: List[int] = []
    all_ocg_xrefs: List[int] = []
    matched_by_name = False
    try:
        ocgs = doc.get_ocgs() or {}
    except Exception:
        ocgs = {}
    if isinstance(ocgs, dict):
        for key, value in ocgs.items():
            try:
                xref = int(key)
            except Exception:
                continue
            all_ocg_xrefs.append(xref)
            name = ""
            if isinstance(value, dict):
                name = str(value.get("name") or value.get("text") or "")
            if is_layer_zero_name(name):
                zero_xrefs.append(xref)
                matched_by_name = True

    if zero_xrefs:
        try:
            current = doc.get_layer(-1) or {}
            on = [int(x) for x in (current.get("on") or [])]
            off = [int(x) for x in (current.get("off") or [])]
            new_on = [x for x in on if x not in zero_xrefs]
            new_off = sorted(set(off + zero_xrefs))
            doc.set_layer(-1, on=new_on, off=new_off)
        except Exception:
            try:
                doc.set_layer(-1, off=zero_xrefs)
            except Exception:
                pass

    try:
        ui_cfgs = doc.layer_ui_configs()
    except Exception:
        return
    if not ui_cfgs:
        return

    ui_hit = False
    for cfg in ui_cfgs:
        cfg_name = cfg.get("text", "") or cfg.get("name", "") or cfg.get("label", "")
        if not is_layer_zero_name(cfg_name):
            continue
        try:
            num = int(cfg.get("number"))
        except Exception:
            continue
        if num < 0:
            continue
        try:
            doc.set_layer_ui_config(num, 2)
            ui_hit = True
        except Exception:
            pass

    if not ui_hit and not matched_by_name:
        first_ui_num = None
        for cfg in ui_cfgs:
            try:
                num = int(cfg.get("number"))
            except Exception:
                continue
            if num < 0:
                continue
            try:
                doc.set_layer_ui_config(num, 2)
                first_ui_num = num
            except Exception:
                continue
            break
        if first_ui_num is not None:
            first_xref = None
            if all_ocg_xrefs:
                first_xref = sorted(set(int(x) for x in all_ocg_xrefs if int(x) > 0))[0]
            if first_xref is not None:
                try:
                    doc.set_layer(-1, off=[int(first_xref)])
                except Exception:
                    pass


def is_packet_target_layer_name(text: object) -> bool:
    normalized = norm_layer_name(text)
    if not normalized:
        return False
    return (
        normalized == "visible"
        or normalized.startswith("visible")
        or normalized == "visiblenarrow"
        or normalized.startswith("visiblenarrow")
        or normalized == "border"
        or normalized.startswith("border")
        or normalized == "bendcenterline"
        or normalized.startswith("bendcenterline")
        or normalized == "bendcentreline"
        or normalized.startswith("bendcentreline")
        or normalized == "title"
        or normalized.startswith("title")
        or normalized == "symbol"
        or normalized.startswith("symbol")
        or normalized == "dimension"
        or normalized.startswith("dimension")
        or normalized == "dim"
        or normalized.startswith("dim")
    )


def apply_packet_layer_policy(doc) -> bool:
    """
    Packet layer policy (source docs):
    - Keep all toggleable layers ON except:
      - layer 0 variants
      - hidden variants
    - Fallback: at minimum suppress layer '0' / '0 (ANSI)'.
    """
    try:
        ui_cfgs = doc.layer_ui_configs()
    except Exception:
        ui_cfgs = None
    if not ui_cfgs:
        suppress_layer_zero(doc)
        return False

    keep_nums: set[int] = set()
    keep_xrefs_from_ui: set[int] = set()
    force_off_xrefs_from_ui: set[int] = set()
    first_ui_xref: Optional[int] = None
    layer_items: List[Tuple[int, str]] = []
    for cfg in ui_cfgs:
        try:
            num = int(cfg.get("number"))
        except Exception:
            continue
        if num < 0:
            continue
        xref = ui_cfg_xref(cfg)
        if first_ui_xref is None:
            first_ui_xref = xref
        normalized = norm_layer_name(
            cfg.get("text", "") or cfg.get("name", "") or cfg.get("label", "")
        )
        layer_items.append((num, normalized))
        is_hidden = normalized == "hidden" or normalized.startswith("hidden")
        is_layer0 = is_layer_zero_name(normalized)
        if is_hidden or is_layer0:
            if xref is not None:
                force_off_xrefs_from_ui.add(int(xref))
            continue
        keep_nums.add(num)
        if xref is not None:
            keep_xrefs_from_ui.add(int(xref))

    changed = False
    if keep_nums:
        for num, _ in layer_items:
            try:
                doc.set_layer_ui_config(num, 2)
                changed = True
            except Exception:
                pass
        for num in sorted(keep_nums):
            try:
                doc.set_layer_ui_config(num, 1)
                changed = True
            except Exception:
                pass
    else:
        suppress_layer_zero(doc)

    try:
        ocgs = doc.get_ocgs() or {}
    except Exception:
        ocgs = {}
    if isinstance(ocgs, dict) and ocgs:
        all_xrefs: List[int] = []
        on_xrefs: List[int] = []
        zero_xrefs_by_name: List[int] = []
        hidden_xrefs_by_name: List[int] = []
        for key, value in ocgs.items():
            try:
                xref = int(key)
            except Exception:
                continue
            all_xrefs.append(xref)
            name = ""
            if isinstance(value, dict):
                name = str(value.get("name") or value.get("text") or "")
            if is_layer_zero_name(name):
                zero_xrefs_by_name.append(xref)
                continue
            normalized = norm_layer_name(name)
            if normalized == "hidden" or normalized.startswith("hidden"):
                hidden_xrefs_by_name.append(xref)
                continue
            if xref > 0:
                on_xrefs.append(xref)
        all_xrefs = sorted(set(int(x) for x in all_xrefs if int(x) > 0))

        force_off: set[int] = set(int(x) for x in zero_xrefs_by_name if int(x) > 0)
        force_off.update(int(x) for x in hidden_xrefs_by_name if int(x) > 0)
        force_off.update(int(x) for x in force_off_xrefs_from_ui if int(x) > 0)
        if not force_off:
            if first_ui_xref is not None:
                force_off.add(int(first_ui_xref))
            elif all_xrefs:
                force_off.add(int(all_xrefs[0]))

        on_set = set(int(x) for x in on_xrefs if int(x) > 0)
        on_set.update(int(x) for x in keep_xrefs_from_ui if int(x) > 0)
        on_set.difference_update(force_off)
        on_xrefs = sorted(on_set)

        if on_xrefs:
            off_x = set(all_xrefs)
            off_x.difference_update(on_set)
            off_x.update(force_off)
            off_xrefs = sorted(int(x) for x in off_x if int(x) > 0)
        else:
            off_xrefs = sorted(int(x) for x in force_off if int(x) > 0)
        if on_xrefs:
            try:
                doc.set_layer(-1, basestate="OFF", on=on_xrefs, off=off_xrefs)
                changed = True
            except TypeError:
                try:
                    doc.set_layer(-1, on=on_xrefs, off=off_xrefs)
                    changed = True
                except Exception:
                    pass
        elif off_xrefs:
            try:
                doc.set_layer(-1, off=off_xrefs)
                changed = True
            except Exception:
                suppress_layer_zero(doc)
        else:
            suppress_layer_zero(doc)
    return changed


def collect_layer_zero_masks(
    src_page,
    zero_layer_aliases: Optional[List[str]] = None,
) -> Tuple[List[dict], List[Tuple[float, float, float, float]], float]:
    """
    Collect layer-0 drawing/text masks from the original source page before any
    layer visibility changes. Returns (draw_masks, text_boxes, page_area).
    """
    draw_masks: List[dict] = []
    text_boxes: List[Tuple[float, float, float, float]] = []
    page_area = 1.0

    if src_page is None:
        return draw_masks, text_boxes, page_area

    try:
        rect = src_page.rect
        page_area = max(1.0, float(rect.width) * float(rect.height))
    except Exception:
        page_area = 1.0

    try:
        drawings = src_page.get_drawings() or []
    except Exception:
        drawings = []
    for drawing in drawings:
        if not matches_zero_layer_alias(drawing.get("layer"), zero_layer_aliases or []):
            continue
        rect = drawing.get("rect", None)
        if rect is None:
            continue
        try:
            bbox = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
        except Exception:
            continue
        draw_masks.append(
            {
                "rect": bbox,
                "width": float(drawing.get("width", 1.0) or 1.0),
                "closePath": bool(drawing.get("closePath", False)),
                "items": drawing.get("items", []) or [],
            }
        )

    try:
        traces = src_page.get_texttrace() or []
    except Exception:
        traces = []
    for trace in traces:
        if not matches_zero_layer_alias(trace.get("layer"), zero_layer_aliases or []):
            continue
        bbox = trace.get("bbox", None)
        if not bbox or len(bbox) != 4:
            continue
        try:
            x0, y0, x1, y1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
        except Exception:
            continue
        text_boxes.append((x0, y0, x1, y1))

    return draw_masks, text_boxes, page_area


def erase_layer_zero_overlays(
    dst_page,
    draw_masks: List[dict],
    text_boxes: List[Tuple[float, float, float, float]],
    page_area: float,
    *,
    fitz_module,
) -> None:
    """
    Vector-mode fail-safe:
    draw white eraser geometry over source objects tagged as layer 0.
    This is used when downstream consumers ignore OCG visibility state.
    """
    if fitz_module is None or dst_page is None:
        return

    page_area = max(1.0, float(page_area))

    for drawing in draw_masks:
        rect = drawing.get("rect", None)
        if not rect or len(rect) != 4:
            continue
        rx0, ry0, rx1, ry1 = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
        if rx1 <= rx0 or ry1 <= ry0:
            continue
        area = (rx1 - rx0) * (ry1 - ry0)
        if area >= 0.92 * page_area:
            continue

        width = float(drawing.get("width", 1.0) or 1.0)
        eraser_width = max(1.6, width + 1.6)
        items = drawing.get("items", []) or []
        drew_any = False
        if items:
            shape = dst_page.new_shape()
            for item in items:
                if not isinstance(item, (list, tuple)) or not item:
                    continue
                op = str(item[0])
                try:
                    if op == "l" and len(item) >= 3:
                        shape.draw_line(item[1], item[2])
                        drew_any = True
                    elif op == "c" and len(item) >= 5:
                        shape.draw_bezier(item[1], item[2], item[3], item[4])
                        drew_any = True
                    elif op == "re" and len(item) >= 2:
                        shape.draw_rect(item[1])
                        drew_any = True
                    elif op == "qu" and len(item) >= 2:
                        shape.draw_quad(item[1])
                        drew_any = True
                except Exception:
                    continue
            if drew_any:
                try:
                    shape.finish(
                        width=eraser_width,
                        color=(1, 1, 1),
                        fill=None,
                        lineCap=1,
                        lineJoin=1,
                        closePath=bool(drawing.get("closePath", False)),
                        stroke_opacity=1.0,
                    )
                    shape.commit()
                except Exception:
                    drew_any = False

        if not drew_any:
            try:
                dst_page.draw_rect(
                    fitz_module.Rect(rx0, ry0, rx1, ry1),
                    color=(1, 1, 1),
                    fill=None,
                    width=max(2.0, eraser_width),
                    stroke_opacity=1.0,
                )
            except Exception:
                pass

    for bbox in text_boxes:
        if not bbox or len(bbox) != 4:
            continue
        x0, y0, x1, y1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
        if x1 <= x0 or y1 <= y0:
            continue
        area = (x1 - x0) * (y1 - y0)
        if area >= 0.92 * page_area:
            continue
        pad = 0.8
        try:
            dst_page.draw_rect(
                fitz_module.Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad),
                color=None,
                fill=(1, 1, 1),
                fill_opacity=1.0,
            )
        except Exception:
            pass


def set_layer0_only(doc) -> None:
    if doc is None:
        return
    try:
        ui_cfgs = doc.layer_ui_configs() or []
    except Exception:
        ui_cfgs = []
    on_nums: List[int] = []
    off_nums: List[int] = []
    toggle_nums: List[int] = []
    for cfg in ui_cfgs:
        try:
            num = int(cfg.get("number"))
        except Exception:
            continue
        if num < 0:
            continue
        toggle_nums.append(num)
        name = str(cfg.get("text", "") or cfg.get("name", "") or cfg.get("label", ""))
        if is_layer_zero_name(name):
            on_nums.append(num)
        else:
            off_nums.append(num)
    if not on_nums and toggle_nums:
        first_num = int(toggle_nums[0])
        on_nums = [first_num]
        off_nums = [num for num in off_nums if int(num) != first_num]
    for num in off_nums:
        try:
            doc.set_layer_ui_config(num, 2)
        except Exception:
            pass
    for num in on_nums:
        try:
            doc.set_layer_ui_config(num, 1)
        except Exception:
            pass

    try:
        ocgs = doc.get_ocgs() or {}
    except Exception:
        ocgs = {}
    if isinstance(ocgs, dict) and ocgs:
        on_xrefs: List[int] = []
        off_xrefs: List[int] = []
        all_xrefs: List[int] = []
        for key, value in ocgs.items():
            try:
                xref = int(key)
            except Exception:
                continue
            if xref <= 0:
                continue
            all_xrefs.append(xref)
            name = ""
            if isinstance(value, dict):
                name = str(value.get("name") or value.get("text") or "")
            if is_layer_zero_name(name):
                on_xrefs.append(xref)
            else:
                off_xrefs.append(xref)
        if not on_xrefs and all_xrefs:
            first_xref = sorted(set(int(x) for x in all_xrefs if int(x) > 0))[0]
            on_xrefs = [first_xref]
            off_xrefs = [int(x) for x in off_xrefs if int(x) != first_xref]
        if on_xrefs or off_xrefs:
            on_xrefs = sorted(set(on_xrefs))
            off_xrefs = sorted(set(off_xrefs))
            try:
                doc.set_layer(-1, basestate="OFF", on=on_xrefs, off=off_xrefs)
            except TypeError:
                try:
                    doc.set_layer(-1, on=on_xrefs, off=off_xrefs)
                except Exception:
                    pass
            except Exception:
                pass


def is_symbol_or_dimension_layer_name(name: object) -> bool:
    normalized = norm_layer_name(name)
    if not normalized:
        return False
    return (
        normalized.startswith("symbol")
        or normalized.startswith("dimension")
        or normalized == "dim"
        or normalized.startswith("dim")
    )


def layer_is_target(layer_name: object) -> bool:
    lowered = str(layer_name or "").lower()
    return bool(lowered) and "dimension" in lowered


def is_title_layer(layer_name: object) -> bool:
    return "title" in str(layer_name or "").lower()
