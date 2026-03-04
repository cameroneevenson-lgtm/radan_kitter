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
import re
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

from config import ENG_RELEASE_MAP as CFG_ENG_RELEASE_MAP, W_RELEASE_ROOT as CFG_W_RELEASE_ROOT
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
PACKET_LAYER0_KEEP_BR_ENABLED = True
PACKET_LAYER0_KEEP_BR_X_START = 0.72
PACKET_LAYER0_KEEP_BR_Y_START = 0.70
# PyMuPDF page processing can be unstable in threaded mode on some systems.
# Keep stable single-thread default; allow override via env for testing.
PACKET_MAX_WORKERS = max(1, min(8, int(os.environ.get("RK_PACKET_WORKERS", "2"))))
PACKET_INVERT_BW = str(os.environ.get("RK_PACKET_INVERT_BW", "1")).strip().lower() not in ("0", "false", "off", "no")


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


def _norm_layer_name(text: object) -> str:
    return "".join(ch.lower() for ch in str(text or "") if ch.isalnum())


def _is_layer_zero_name(text: object) -> bool:
    raw = str(text or "").strip().lower()
    if raw == "0" or raw.startswith("layer 0"):
        return True
    if raw.startswith("0 (") and raw.endswith(")"):
        return True
    token = re.split(r"[\s()\\[\\]{}\\-_/]+", raw)[0] if raw else ""
    if token == "0":
        return True
    nm = _norm_layer_name(text)
    if nm in ("0", "0ansi", "layer0", "layer0ansi"):
        return True
    if nm.startswith("0"):
        return True
    if nm.startswith("0ansi") or nm.startswith("layer0ansi"):
        return True
    return False


def _ui_cfg_xref(cfg: dict) -> Optional[int]:
    for key in ("xref", "ocg", "oc"):
        try:
            v = cfg.get(key, None)
        except Exception:
            v = None
        try:
            x = int(v)
        except Exception:
            continue
        if x > 0:
            return x
    return None


def _first_toggle_layer_aliases(doc) -> List[str]:
    """
    Return normalized aliases for the first toggleable UI layer entry.
    Exporter-specific fallback: this is expected to be layer 0.
    """
    out: List[str] = []
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
            out.append(_norm_layer_name(raw))
        # first toggleable layer only
        break
    return [x for x in out if x]


def _matches_zero_layer_alias(layer_name: object, aliases: List[str]) -> bool:
    if _is_layer_zero_name(layer_name):
        return True
    nm = _norm_layer_name(layer_name)
    if not nm:
        return False
    return nm in set(aliases or [])


def _suppress_layer_zero(doc) -> None:
    """Force OCG layer named '0' OFF if present."""
    zero_xrefs: List[int] = []
    all_ocg_xrefs: List[int] = []
    matched_by_name = False
    try:
        ocgs = doc.get_ocgs() or {}
    except Exception:
        ocgs = {}
    if isinstance(ocgs, dict):
        for k, v in ocgs.items():
            try:
                xref = int(k)
            except Exception:
                continue
            all_ocg_xrefs.append(xref)
            name = ""
            if isinstance(v, dict):
                name = str(v.get("name") or v.get("text") or "")
            if _is_layer_zero_name(name):
                zero_xrefs.append(xref)
                matched_by_name = True

    # Best-effort explicit OCG state update for default config.
    if zero_xrefs:
        try:
            cur = doc.get_layer(-1) or {}
            on = [int(x) for x in (cur.get("on") or [])]
            off = [int(x) for x in (cur.get("off") or [])]
            new_on = [x for x in on if x not in zero_xrefs]
            new_off = sorted(set(off + zero_xrefs))
            doc.set_layer(-1, on=new_on, off=new_off)
        except Exception:
            try:
                doc.set_layer(-1, off=zero_xrefs)
            except Exception:
                pass

    # Also drive UI config state where names expose layer '0'.
    try:
        ui_cfgs = doc.layer_ui_configs()
    except Exception:
        return
    if not ui_cfgs:
        return
    ui_hit = False
    for cfg in ui_cfgs:
        cfg_name = (
            cfg.get("text", "")
            or cfg.get("name", "")
            or cfg.get("label", "")
        )
        if not _is_layer_zero_name(cfg_name):
            continue
        try:
            num = int(cfg.get("number"))
        except Exception:
            continue
        if num < 0:
            continue
        try:
            doc.set_layer_ui_config(num, 2)  # force OFF
            ui_hit = True
        except Exception:
            pass

    # Exporter fallback: layer 0 is first in layer list.
    # If name matching fails, force OFF the first toggleable layer entry.
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
            # Keep default OCG state aligned where possible via first OCG xref.
            first_xref = None
            if all_ocg_xrefs:
                first_xref = sorted(set(int(x) for x in all_ocg_xrefs if int(x) > 0))[0]
            if first_xref is not None:
                try:
                    doc.set_layer(-1, off=[int(first_xref)])
                except Exception:
                    pass


def _is_packet_target_layer_name(text: object) -> bool:
    nm = _norm_layer_name(text)
    if not nm:
        return False
    return (
        nm == "visible"
        or nm.startswith("visible")
        or nm == "visiblenarrow"
        or nm.startswith("visiblenarrow")
        or nm == "border"
        or nm.startswith("border")
        or nm == "bendcenterline"
        or nm.startswith("bendcenterline")
        or nm == "bendcentreline"
        or nm.startswith("bendcentreline")
        or nm == "title"
        or nm.startswith("title")
        or nm == "symbol"
        or nm.startswith("symbol")
        or nm == "dimension"
        or nm.startswith("dimension")
        or nm == "dim"
        or nm.startswith("dim")
    )


def _apply_packet_layer_policy(doc) -> bool:
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
        _suppress_layer_zero(doc)
        return False

    keep_nums: set[int] = set()
    keep_xrefs_from_ui: set[int] = set()
    force_off_nums: set[int] = set()
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
        ux = _ui_cfg_xref(cfg)
        if first_ui_xref is None:
            first_ui_xref = _ui_cfg_xref(cfg)
        nm = _norm_layer_name(
            cfg.get("text", "")
            or cfg.get("name", "")
            or cfg.get("label", "")
        )
        layer_items.append((num, nm))
        is_hidden = (nm == "hidden" or nm.startswith("hidden"))
        is_layer0 = _is_layer_zero_name(nm)
        if is_hidden or is_layer0:
            force_off_nums.add(num)
            if ux is not None:
                force_off_xrefs_from_ui.add(int(ux))
            continue
        keep_nums.add(num)
        if ux is not None:
            keep_xrefs_from_ui.add(int(ux))

    changed = False
    if keep_nums:
        # Pass 1: OFF all user toggles.
        for num, _nm in layer_items:
            try:
                doc.set_layer_ui_config(num, 2)
                changed = True
            except Exception:
                pass
        # Pass 2: ON all non-suppressed layers.
        for num in sorted(keep_nums):
            try:
                doc.set_layer_ui_config(num, 1)
                changed = True
            except Exception:
                pass
    else:
        _suppress_layer_zero(doc)

    # Persist a default OCG policy when possible so downstream viewers/printers
    # honor the same visibility rules as preview.
    try:
        ocgs = doc.get_ocgs() or {}
    except Exception:
        ocgs = {}
    if isinstance(ocgs, dict) and ocgs:
        all_xrefs: List[int] = []
        on_xrefs: List[int] = []
        zero_xrefs_by_name: List[int] = []
        hidden_xrefs_by_name: List[int] = []
        for k, v in ocgs.items():
            try:
                xref = int(k)
            except Exception:
                continue
            all_xrefs.append(xref)
            nm = ""
            if isinstance(v, dict):
                nm = str(v.get("name") or v.get("text") or "")
            if _is_layer_zero_name(nm):
                zero_xrefs_by_name.append(xref)
                continue
            nm_norm = _norm_layer_name(nm)
            if nm_norm == "hidden" or nm_norm.startswith("hidden"):
                hidden_xrefs_by_name.append(xref)
                continue
            if int(xref) > 0:
                on_xrefs.append(xref)
        all_xrefs = sorted(set(int(x) for x in all_xrefs if int(x) > 0))

        # Force-off candidates for layer 0 + hidden.
        force_off: set[int] = set(int(x) for x in zero_xrefs_by_name if int(x) > 0)
        force_off.update(int(x) for x in hidden_xrefs_by_name if int(x) > 0)
        force_off.update(int(x) for x in force_off_xrefs_from_ui if int(x) > 0)
        if not force_off:
            if first_ui_xref is not None:
                force_off.add(int(first_ui_xref))
            elif all_xrefs:
                # Exporter fallback: first layer in list is layer 0.
                force_off.add(int(all_xrefs[0]))

        # Combine non-suppressed xrefs from OCG names and UI xref mapping.
        on_x = set(int(x) for x in on_xrefs if int(x) > 0)
        on_x.update(int(x) for x in keep_xrefs_from_ui if int(x) > 0)
        on_x.difference_update(force_off)
        on_xrefs = sorted(on_x)

        if on_xrefs:
            off_x = set(all_xrefs)
            off_x.difference_update(on_x)
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
                _suppress_layer_zero(doc)
        else:
            _suppress_layer_zero(doc)
    return changed


def _collect_layer_zero_masks(
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

    if fitz is None or src_page is None:
        return draw_masks, text_boxes, page_area

    try:
        pr = src_page.rect
        page_area = max(1.0, float(pr.width) * float(pr.height))
    except Exception:
        page_area = 1.0

    try:
        drawings = src_page.get_drawings() or []
    except Exception:
        drawings = []
    for d in drawings:
        if not _matches_zero_layer_alias(d.get("layer"), zero_layer_aliases or []):
            continue
        r = d.get("rect", None)
        if r is None:
            continue
        try:
            rect = (float(r.x0), float(r.y0), float(r.x1), float(r.y1))
        except Exception:
            continue
        draw_masks.append(
            {
                "rect": rect,
                "width": float(d.get("width", 1.0) or 1.0),
                "closePath": bool(d.get("closePath", False)),
                "items": d.get("items", []) or [],
            }
        )

    try:
        traces = src_page.get_texttrace() or []
    except Exception:
        traces = []
    for t in traces:
        if not _matches_zero_layer_alias(t.get("layer"), zero_layer_aliases or []):
            continue
        bb = t.get("bbox", None)
        if not bb or len(bb) != 4:
            continue
        try:
            x0, y0, x1, y1 = float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])
        except Exception:
            continue
        text_boxes.append((x0, y0, x1, y1))

    return draw_masks, text_boxes, page_area


def _erase_layer_zero_overlays(
    dst_page,
    draw_masks: List[dict],
    text_boxes: List[Tuple[float, float, float, float]],
    page_area: float,
) -> None:
    """
    Vector-mode fail-safe:
    draw white eraser geometry over source objects tagged as layer 0.
    This is used when downstream consumers ignore OCG visibility state.
    """
    if fitz is None or dst_page is None:
        return

    page_area = max(1.0, float(page_area))

    # 1) Stroke-based eraser for vector drawings on layer 0.
    for d in draw_masks:
        rect = d.get("rect", None)
        if not rect or len(rect) != 4:
            continue
        rx0, ry0, rx1, ry1 = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
        if rx1 <= rx0 or ry1 <= ry0:
            continue
        area = (rx1 - rx0) * (ry1 - ry0)
        # Skip huge fills/bounds to avoid wiping full sheet.
        if area >= 0.92 * page_area:
            continue

        w = float(d.get("width", 1.0) or 1.0)
        eraser_w = max(1.6, w + 1.6)
        items = d.get("items", []) or []
        drew_any = False
        if items:
            sh = dst_page.new_shape()
            for it in items:
                if not isinstance(it, (list, tuple)) or not it:
                    continue
                op = str(it[0])
                try:
                    if op == "l" and len(it) >= 3:
                        sh.draw_line(it[1], it[2])
                        drew_any = True
                    elif op == "c" and len(it) >= 5:
                        sh.draw_bezier(it[1], it[2], it[3], it[4])
                        drew_any = True
                    elif op == "re" and len(it) >= 2:
                        sh.draw_rect(it[1])
                        drew_any = True
                    elif op == "qu" and len(it) >= 2:
                        sh.draw_quad(it[1])
                        drew_any = True
                except Exception:
                    continue
            if drew_any:
                try:
                    sh.finish(
                        width=eraser_w,
                        color=(1, 1, 1),
                        fill=None,
                        lineCap=1,
                        lineJoin=1,
                        closePath=bool(d.get("closePath", False)),
                        stroke_opacity=1.0,
                    )
                    sh.commit()
                except Exception:
                    drew_any = False

        if not drew_any:
            # Fallback: erase using stroke-only bbox.
            try:
                dst_page.draw_rect(
                    fitz.Rect(rx0, ry0, rx1, ry1),
                    color=(1, 1, 1),
                    fill=None,
                    width=max(2.0, eraser_w),
                    stroke_opacity=1.0,
                )
            except Exception:
                pass

    # 2) Text eraser for layer-0 text runs.
    for bb in text_boxes:
        if not bb or len(bb) != 4:
            continue
        x0, y0, x1, y1 = float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])
        if x1 <= x0 or y1 <= y0:
            continue
        area = (x1 - x0) * (y1 - y0)
        if area >= 0.92 * page_area:
            continue
        pad = 0.8
        try:
            dst_page.draw_rect(
                fitz.Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad),
                color=None,
                fill=(1, 1, 1),
                fill_opacity=1.0,
            )
        except Exception:
            pass


def _layer0_keep_bottom_right_rect(page_rect):
    if fitz is None or page_rect is None:
        return None
    try:
        x0 = float(page_rect.x0)
        y0 = float(page_rect.y0)
        x1 = float(page_rect.x1)
        y1 = float(page_rect.y1)
    except Exception:
        return None
    w = max(0.0, x1 - x0)
    h = max(0.0, y1 - y0)
    if w <= 1e-6 or h <= 1e-6:
        return None
    kx = max(0.0, min(1.0, float(PACKET_LAYER0_KEEP_BR_X_START)))
    ky = max(0.0, min(1.0, float(PACKET_LAYER0_KEEP_BR_Y_START)))
    return fitz.Rect(x0 + (w * kx), y0 + (h * ky), x1, y1)


def _rect_center_in(rect_xyxy, keep_rect) -> bool:
    if keep_rect is None:
        return False
    try:
        x0, y0, x1, y1 = float(rect_xyxy[0]), float(rect_xyxy[1]), float(rect_xyxy[2]), float(rect_xyxy[3])
    except Exception:
        return False
    cx = 0.5 * (x0 + x1)
    cy = 0.5 * (y0 + y1)
    return (
        float(keep_rect.x0) <= cx <= float(keep_rect.x1)
        and float(keep_rect.y0) <= cy <= float(keep_rect.y1)
    )


def _is_symbol_or_dimension_layer_name(name: object) -> bool:
    nm = _norm_layer_name(name)
    if not nm:
        return False
    return (
        nm.startswith("symbol")
        or nm.startswith("dimension")
        or nm == "dim"
        or nm.startswith("dim")
    )


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
        r = rgb[:, :, 0].astype(np.uint16)
        g = rgb[:, :, 1].astype(np.uint16)
        b = rgb[:, :, 2].astype(np.uint16)

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


def _layer_is_target(layer_name: object) -> bool:
    s = str(layer_name or "").lower()
    if not s:
        return False
    return "dimension" in s


def _is_title_layer(layer_name: object) -> bool:
    s = str(layer_name or "").lower()
    return "title" in s


def _is_red_rgb(color: object) -> bool:
    if not isinstance(color, tuple) or len(color) < 3:
        return False
    try:
        r, g, b = float(color[0]), float(color[1]), float(color[2])
    except Exception:
        return False
    return r >= 0.28 and r >= (g + 0.05) and r >= (b + 0.05)


def _is_red_text_color(color: object) -> bool:
    # PyMuPDF text span color is often an int 0xRRGGBB.
    if isinstance(color, int):
        r = (color >> 16) & 255
        g = (color >> 8) & 255
        b = color & 255
        return r >= 70 and r >= (g + 8) and r >= (b + 8)
    return _is_red_rgb(color)


def _color_to_rgb(color: object) -> Optional[Tuple[float, float, float]]:
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


def _looks_like_dimension_text(s: str) -> bool:
    t = str(s or "").strip()
    if not t:
        return False
    # Typical dimension tokens: numbers / decimals / fractions with simple symbols.
    return bool(re.fullmatch(r"[0-9.\-+/\"' xX]+", t))


def _highlight_red_target_layers(
    page,
    gate_boxes: Optional[List[Tuple[float, float, float, float]]] = None,
    dim_boxes: Optional[List[Tuple[float, float, float, float]]] = None,
    draw: bool = True,
) -> None:
    try:
        drawings = page.get_drawings()
    except Exception:
        return
    for d in drawings:
        is_red = _is_red_rgb(d.get("color")) or _is_red_rgb(d.get("fill"))
        if not is_red:
            continue
        r = d.get("rect", None)
        if r is None:
            continue
        # Only dimension geometry gets fluorescent highlight boxes.
        if not _layer_is_target(d.get("layer")):
            continue
        bb = (float(r.x0), float(r.y0), float(r.x1), float(r.y1))
        if dim_boxes is not None:
            dim_boxes.append(bb)
        if draw:
            _draw_dim_mask(page, fitz.Rect(r.x0, r.y0, r.x1, r.y1))


def _highlight_red_text(
    page,
    gate_boxes: Optional[List[Tuple[float, float, float, float]]] = None,
    dim_boxes: Optional[List[Tuple[float, float, float, float]]] = None,
    overlay_runs: Optional[List[dict]] = None,
    draw: bool = True,
) -> None:
    # Use texttrace so we can filter by OCG layer and group glyph runs into one mask box.
    try:
        traces = page.get_texttrace()
    except Exception:
        return
    items = []
    for t in traces:
        layer = str(t.get("layer") or "")
        if not _is_red_text_color(t.get("color", None)):
            continue
        chars = t.get("chars", []) or []
        txt = "".join(chr(ch[0]) for ch in chars if isinstance(ch, (list, tuple)) and ch)
        if not txt.strip():
            continue
        bb = t.get("bbox", None)
        if not bb or len(bb) != 4:
            continue
        if gate_boxes is not None and _is_symbol_or_dimension_layer_name(layer):
            gate_boxes.append((float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])))

        layer_ok = ("dimension" in layer.lower())
        # Some exports lose text OCG layer on dimensions; allow numeric-dimension text fallback.
        unlabeled_dim_text = (not layer.strip()) and _looks_like_dimension_text(txt)
        if not _looks_like_dimension_text(txt):
            continue
        if not (layer_ok or unlabeled_dim_text):
            continue

        items.append(
            {
                "layer": layer.lower().strip(),
                "txt": txt,
                "x0": float(bb[0]),
                "y0": float(bb[1]),
                "x1": float(bb[2]),
                "y1": float(bb[3]),
                "size": float(t.get("size", 0.0) or 0.0),
                "color": _color_to_rgb(t.get("color", None)) or (1.0, 0.0, 0.0),
                "opacity": float(t.get("opacity", 1.0) or 1.0),
            }
        )

    if not items:
        return

    items.sort(key=lambda it: (it["layer"], it["y0"], it["x0"]))

    grouped = []
    cur = None
    for it in items:
        if cur is None:
            cur = dict(it)
            continue

        same_layer = (it["layer"] == cur["layer"])
        y_tol = max(1.6, 0.45 * max(cur["size"], it["size"], 1.0))
        same_line = abs(it["y0"] - cur["y0"]) <= y_tol
        gap = it["x0"] - cur["x1"]
        x_tol = max(6.0, 1.5 * max(cur["size"], it["size"], 1.0))
        adjacent = (-1.0 <= gap <= x_tol)

        if same_layer and same_line and adjacent:
            cur["x0"] = min(cur["x0"], it["x0"])
            cur["y0"] = min(cur["y0"], it["y0"])
            cur["x1"] = max(cur["x1"], it["x1"])
            cur["y1"] = max(cur["y1"], it["y1"])
            cur["txt"] += it["txt"]
            cur["size"] = max(cur["size"], it["size"])
        else:
            grouped.append(cur)
            cur = dict(it)

    if cur is not None:
        grouped.append(cur)

    for g in grouped:
        bb = (float(g["x0"]), float(g["y0"]), float(g["x1"]), float(g["y1"]))
        if dim_boxes is not None:
            dim_boxes.append(bb)
        if overlay_runs is not None:
            overlay_runs.append(
                {
                    "txt": str(g.get("txt", "") or ""),
                    "bbox": bb,
                    "size": float(g.get("size", 0.0) or 0.0),
                    "color": g.get("color", (1.0, 0.0, 0.0)),
                    "opacity": float(g.get("opacity", 1.0) or 1.0),
                }
            )
        if draw:
            _draw_dim_mask(page, fitz.Rect(g["x0"], g["y0"], g["x1"], g["y1"]))


def _collect_red_symbol_dimension_chars(page) -> List[dict]:
    out: List[dict] = []
    try:
        traces = page.get_texttrace()
    except Exception:
        return out

    for t in traces:
        layer = str(t.get("layer") or "")
        color_raw = t.get("color", None)
        if not _is_red_text_color(color_raw):
            continue
        chars = t.get("chars", []) or []
        run_txt = "".join(
            chr(ch[0]) for ch in chars
            if isinstance(ch, (list, tuple)) and ch and isinstance(ch[0], int)
        )
        layer_ok = _is_symbol_or_dimension_layer_name(layer)
        unlabeled_dim_text = (not layer.strip()) and _looks_like_dimension_text(run_txt)
        if not (layer_ok or unlabeled_dim_text):
            continue
        rgb = _color_to_rgb(color_raw) or (1.0, 0.0, 0.0)
        try:
            size = float(t.get("size", 0.0) or 0.0)
        except Exception:
            size = 0.0
        if size <= 0.0:
            size = 9.0
        try:
            opacity = float(t.get("opacity", 1.0) or 1.0)
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
                txt = chr(cp)
            except Exception:
                continue
            if not txt.strip():
                continue
            origin = ch[2]
            try:
                ox = float(origin[0])
                oy = float(origin[1])
            except Exception:
                continue
            out.append(
                {
                    "txt": txt,
                    "origin": (ox, oy),
                    "size": size,
                    "color": rgb,
                    "opacity": opacity,
                }
            )
    return out


def _overlay_red_symbol_dimension_chars(page, chars: List[dict]) -> None:
    if not chars:
        return
    for it in chars:
        txt = str(it.get("txt", "") or "")
        if not txt:
            continue
        try:
            x, y = it.get("origin", (0.0, 0.0))
            size = max(4.0, float(it.get("size", 9.0) or 9.0))
            color = it.get("color", (1.0, 0.0, 0.0))
            opacity = max(0.0, min(1.0, float(it.get("opacity", 1.0) or 1.0)))
        except Exception:
            continue
        try:
            x, y = it.get("origin", (0.0, 0.0))
            page.insert_text(
                fitz.Point(float(x), float(y)),
                txt,
                fontsize=size,
                fontname="helv",
                color=color,
                fill_opacity=opacity,
                overlay=True,
            )
        except TypeError:
            try:
                x, y = it.get("origin", (0.0, 0.0))
                page.insert_text(
                    fitz.Point(float(x), float(y)),
                    txt,
                    fontsize=size,
                    fontname="helv",
                    color=color,
                    overlay=True,
                )
            except Exception:
                continue
        except Exception:
            continue


def _overlay_red_text_runs(page, runs: List[dict]) -> None:
    if not runs:
        return
    seen: set[Tuple[str, float, float, float, float]] = set()
    for it in runs:
        txt = str(it.get("txt", "") or "")
        bb = it.get("bbox", None)
        if not txt or not bb or len(bb) != 4:
            continue
        try:
            x0, y0, x1, y1 = float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])
        except Exception:
            continue
        if x1 <= x0 or y1 <= y0:
            continue
        key = (txt, round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2))
        if key in seen:
            continue
        seen.add(key)
        try:
            size = max(4.0, float(it.get("size", 9.0) or 9.0))
            color = it.get("color", (1.0, 0.0, 0.0))
            opacity = max(0.0, min(1.0, float(it.get("opacity", 1.0) or 1.0)))
        except Exception:
            size = 9.0
            color = (1.0, 0.0, 0.0)
            opacity = 1.0
        rect = fitz.Rect(x0, y0, x1, y1)
        drew = False
        try:
            page.insert_textbox(
                rect,
                txt,
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
                    txt,
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


def _grayscale_title_layer(page) -> None:
    try:
        drawings = page.get_drawings()
    except Exception:
        return
    for d in drawings:
        if not _is_title_layer(d.get("layer")):
            continue
        r = d.get("rect", None)
        if r is None:
            continue
        page.draw_rect(
            fitz.Rect(float(r.x0), float(r.y0), float(r.x1), float(r.y1)),
            color=None,
            fill=TITLE_GRAYSCALE_COLOR,
            fill_opacity=TITLE_GRAYSCALE_OPACITY,
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


def ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


def _unique_norm_paths(paths: List[str]) -> List[str]:
    seen = set()
    out = []
    for p in paths:
        if not p:
            continue
        pn = os.path.normpath(p)
        key = pn.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(pn)
    return out


def map_to_eng_release(sym_path: str) -> str:
    """Map a symbol path to W: release path using ENG_RELEASE_MAP if possible."""
    sp = os.path.normpath(sym_path or "")
    if not sp:
        return sp
    sp_low = sp.lower()
    for src, dst in ENG_RELEASE_MAP:
        if sp_low.startswith(os.path.normpath(src).lower()):
            rel = sp[len(os.path.normpath(src)):].lstrip(r"\/")
            return os.path.normpath(os.path.join(dst, rel))
    return sp


def _force_w_candidates(sym_path: str) -> List[str]:
    """
    Generate W:-first candidates for pdf/dxf resolution.
    Logic:
      - Derive part name from symbol basename.
      - Prefer W:\\LASER\\For Battleshield Fabrication\\<F-number>\\... first
      - Handle 'Parts' folder sometimes present.
      - Fallback: same directory as sym (legacy bundles).
    """
    sp = os.path.normpath(sym_path or "")
    base = os.path.splitext(os.path.basename(sp))[0]

    # Try to extract F-number folder (e.g., F54525) from path tokens
    tokens = re.split(r"[\\/]+", sp)
    fnum = ""
    for t in tokens:
        if re.fullmatch(r"F\d{3,}", t, flags=re.IGNORECASE):
            fnum = t.upper()
            break

    cands = []

    # W:\ preferred
    if fnum:
        # common patterns observed
        cands.append(os.path.join(W_RELEASE_ROOT, fnum, "Parts", base))
        cands.append(os.path.join(W_RELEASE_ROOT, fnum, base))

    # mapped path (if sym is under a known root)
    mapped = map_to_eng_release(sp)
    if mapped and mapped != sp:
        cands.append(os.path.join(os.path.dirname(mapped), base))

    # sym dir fallback
    if sp:
        cands.append(os.path.join(os.path.dirname(sp), base))

    # Normalize and return unique base paths (no extension yet)
    return _unique_norm_paths(cands)


def _process_packet_part(
    i: int,
    p: PartRow,
    *,
    resolver: Callable[[str, str], Optional[str]],
    render_mode: str,
) -> dict:
    t0 = time.perf_counter()
    t_open0 = t0
    part_name = os.path.splitext(os.path.basename(getattr(p, "sym", "") or ""))[0]
    status = part_name or f"Part {i}"
    qty = int(getattr(p, "qty", None) or 1)
    pdf = resolver(p.sym, ".pdf")
    if not pdf or not os.path.exists(pdf):
        return {
            "idx": i,
            "status": f"{status} (missing PDF)",
            "missing": 1,
            "skip": True,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000.0),
        }
    try:
        src = fitz.open(pdf)
    except Exception:
        return {
            "idx": i,
            "status": f"{status} (open failed)",
            "missing": 1,
            "skip": True,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000.0),
        }
    try:
        if src.page_count < 1:
            return {
                "idx": i,
                "status": f"{status} (empty PDF)",
                "missing": 1,
                "skip": True,
                "elapsed_ms": int((time.perf_counter() - t0) * 1000.0),
            }

        mode = str(render_mode or "raster").strip().lower()
        if mode == "vector":
            return {
                "idx": i,
                "status": status,
                "missing": 0,
                "skip": False,
                "mode": "vector",
                "pdf_path": pdf,
                "qty": qty,
                "elapsed_ms": int((time.perf_counter() - t0) * 1000.0),
            }

        open_ms = int((time.perf_counter() - t_open0) * 1000.0)
        _apply_packet_layer_policy(src)

        src_page = src.load_page(0)
        src_rect = src_page.rect

        t_gate0 = time.perf_counter()
        gate_boxes: List[Tuple[float, float, float, float]] = []
        _highlight_red_target_layers(src_page)
        _highlight_red_text(src_page, gate_boxes=gate_boxes)
        gate_ms = int((time.perf_counter() - t_gate0) * 1000.0)

        t_render0 = time.perf_counter()
        pix = _render_page_pixmap(src_page, dpi=PACKET_RASTER_DPI)
        red_gate_mask = None
        if pix is not None and gate_boxes:
            red_gate_mask = _gate_mask_from_boxes(
                src_page,
                int(getattr(pix, "width", 0)),
                int(getattr(pix, "height", 0)),
                gate_boxes,
            )
        pix = _grayscale_preserve_red_and_highlight(pix, red_gate_mask=red_gate_mask)
        render_ms = int((time.perf_counter() - t_render0) * 1000.0)

        t_encode0 = time.perf_counter()
        stream = _pix_to_image_stream(pix)
        encode_ms = int((time.perf_counter() - t_encode0) * 1000.0)
        return {
            "idx": i,
            "status": status,
            "missing": 0,
            "skip": False,
            "mode": "raster",
            "w": float(src_rect.width),
            "h": float(src_rect.height),
            "qty": qty,
            "img_stream": stream,
            "open_ms": open_ms,
            "gate_ms": gate_ms,
            "render_ms": render_ms,
            "encode_ms": encode_ms,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000.0),
        }
    except Exception:
        return {
            "idx": i,
            "status": f"{status} (process failed)",
            "missing": 1,
            "skip": True,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000.0),
        }
    finally:
        try:
            src.close()
        except Exception:
            pass


def resolve_asset(sym_path: str, ext: str) -> str:
    """
    Resolve an asset path (PDF/DXF) from a symbol path.
    ext should be '.pdf' or '.dxf'.
    """
    ext = ext.lower().strip()
    if not ext.startswith("."):
        ext = "." + ext

    for base in _force_w_candidates(sym_path):
        p = base + ext
        if os.path.exists(p):
            return p

    # final fallback: direct replace if sym_path exists and shares basename
    sp = os.path.normpath(sym_path or "")
    if sp:
        p = os.path.join(os.path.dirname(sp), os.path.splitext(os.path.basename(sp))[0] + ext)
        if os.path.exists(p):
            return p

    return ""


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
    """
    Apply one worker result into destination PDF.
    Returns tuple: (missing_inc, pages_inc, elapsed_ms)
    """
    i = int(res.get("idx", 0) or 0)
    status = str(res.get("status") or f"Part {i}")
    missing_inc = int(res.get("missing", 0) or 0)
    pages_inc = 0
    elapsed_ms = int(res.get("elapsed_ms", 0) or 0)

    if not bool(res.get("skip", False)):
        mode = str(res.get("mode", "raster") or "raster").strip().lower()
        qty = int(res.get("qty", 1) or 1)
        dst_page = None
        if mode == "vector":
            pdf_path = str(res.get("pdf_path") or "").strip()
            src = None
            flat = None
            try:
                if pdf_path and os.path.exists(pdf_path):
                    src = fitz.open(pdf_path)
                    if src.page_count >= 1:
                        src_page = src.load_page(0)
                        # Collect layer-0 primitives before visibility/recolor edits.
                        zero_aliases = _first_toggle_layer_aliases(src)
                        zero_draw_masks, zero_text_boxes, zero_page_area = _collect_layer_zero_masks(
                            src_page,
                            zero_layer_aliases=zero_aliases,
                        )
                        keep_rect = None
                        if PACKET_LAYER0_KEEP_BR_ENABLED:
                            keep_rect = _layer0_keep_bottom_right_rect(src_page.rect)
                        # Apply layer policy + red-dimension highlighting on source page.
                        # Then flatten to a layerless PDF that preserves only visible result.
                        _apply_packet_layer_policy(src)
                        red_chars = _collect_red_symbol_dimension_chars(src_page)
                        red_dim_text_runs: List[dict] = []
                        dim_boxes: List[Tuple[float, float, float, float]] = []
                        _highlight_red_target_layers(src_page, dim_boxes=dim_boxes, draw=False)
                        _highlight_red_text(
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
                            flat = fitz.open("pdf", flat_bytes)
                        except Exception:
                            flat = None

                        render_doc = flat if (flat is not None and flat.page_count >= 1) else src
                        render_page = render_doc.load_page(0)
                        # Erase layer 0 content except the bottom-right keep zone (logo area).
                        erase_draw = zero_draw_masks
                        erase_text = zero_text_boxes
                        if keep_rect is not None:
                            erase_draw = [
                                d for d in zero_draw_masks
                                if not _rect_center_in(d.get("rect", (0, 0, 0, 0)), keep_rect)
                            ]
                            erase_text = [bb for bb in zero_text_boxes if not _rect_center_in(bb, keep_rect)]
                        if erase_draw or erase_text:
                            _erase_layer_zero_overlays(
                                render_page,
                                erase_draw,
                                erase_text,
                                zero_page_area,
                            )

                        # Apply colored overlays after flattening so they do not get recolored.
                        seen_dim = set()
                        for bb in dim_boxes:
                            try:
                                x0, y0, x1, y1 = float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])
                            except Exception:
                                continue
                            key = (round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2))
                            if key in seen_dim:
                                continue
                            seen_dim.add(key)
                            _draw_dim_mask(render_page, fitz.Rect(x0, y0, x1, y1))
                        _overlay_red_symbol_dimension_chars(render_page, red_chars)
                        _overlay_red_text_runs(render_page, red_dim_text_runs)
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
                                # Older PyMuPDF compatibility.
                                dst_page.show_pdf_page(dst_page.rect, render_doc, 0)
                            except Exception:
                                # Fallback if show_pdf_page fails for this source.
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
            except Exception:
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
            w = float(res.get("w", 0.0) or 0.0)
            h = float(res.get("h", 0.0) or 0.0)
            img_stream = res.get("img_stream", b"") or b""
            if w > 1e-6 and h > 1e-6:
                dst_page = dst.new_page(width=w, height=h)
                rect = dst_page.rect
                if img_stream:
                    dst_page.insert_image(rect, stream=img_stream, keep_proportion=False, overlay=True)
                pages_inc = 1

        if dst_page is not None and pages_inc > 0:
            rect = dst_page.rect
            text = f"QTY {qty}"
            margin = 18
            font_size = 24 * WATERMARK_TEXT_SCALE
            box_h = 46 * WATERMARK_TEXT_SCALE
            x1 = margin
            y1 = rect.height - margin - box_h
            text_w = fitz.get_text_length(text, fontname="helv", fontsize=font_size)
            pad_x = 12
            x2 = x1 + text_w + (2 * pad_x)
            y2 = y1 + box_h
            _draw_rounded_stroke_rect(
                dst_page,
                fitz.Rect(x1, y1, x2, y2),
                stroke_color=WATERMARK_STROKE_COLOR,
                stroke_width=WATERMARK_STROKE_WIDTH,
                stroke_opacity=WATERMARK_STROKE_OPACITY,
                radius=WATERMARK_RADIUS * WATERMARK_TEXT_SCALE,
            )
            dst_page.insert_text(
                fitz.Point(x1 + pad_x, y1 + box_h * 0.72),
                text,
                fontsize=font_size,
                color=WATERMARK_TEXT_COLOR,
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
    ensure_dir(out_pdf_path)

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
                        except Exception:
                            res = {
                                "idx": idx,
                                "status": f"Part {idx} (worker failed)",
                                "missing": 1,
                                "skip": True,
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

        if progress_cb is not None:
            try:
                progress_cb(progress_total, progress_total, "Writing packet file")
            except Exception:
                pass

        _apply_packet_layer_policy(dst)

        dst.save(
            out_pdf_path,
            deflate=False,
            deflate_images=False,
            garbage=0,
        )
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
