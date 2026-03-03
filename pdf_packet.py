# pdf_packet.py
# PDF packet generation (watermarked kit printout)
#
# Scope:
# - Build a PDF packet by concatenating each part's PDF (single-page expected) and overlaying a QTY mark.
# - Uses only fitz (PyMuPDF) + simple path resolution helpers.
# - QTY mark: bottom-left. No highlight boxes.
#
# Notes:
# - No Qt / UI code belongs in this module.
# - Deterministic: missing PDFs are counted; function returns (pages_written, missing_count).

import os
import re
from typing import Callable, List, Optional, Tuple

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

from rpd_io import PartRow

# Engineering release root (preferred for production)
W_RELEASE_ROOT = r"W:\LASER\For Battleshield Fabrication"

# Map common symbol roots to W: release roots (your existing mapping; extend as needed).
ENG_RELEASE_MAP = [
    # Example:
    # (r"L:\BATTLESHIELD", r"W:\LASER\For Battleshield Fabrication"),
]

# Watermark style
WATERMARK_TEXT_SCALE = 2.4
WATERMARK_FILL_COLOR = (0.25, 1.00, 0.25)  # fluorescent green for QTY badge
WATERMARK_TEXT_COLOR = (0, 0, 0)  # black
WATERMARK_OPACITY = 0.34
DIM_HILITE_COLOR = (0.08, 0.96, 0.08)  # denser fluorescent green outline for dimensions
DIM_HILITE_STROKE_WIDTH = 3.6
DIM_HILITE_PAD_X = 1.8
DIM_HILITE_PAD_Y_TOP = 3.2
DIM_HILITE_PAD_Y_BOTTOM = 1.8
DIM_HILITE_STROKE_OPACITY = 0.90  # 10% transparent
TITLE_GRAYSCALE_COLOR = (0.80, 0.80, 0.80)
TITLE_GRAYSCALE_OPACITY = 0.40


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
    return r >= 0.70 and g <= 0.35 and b <= 0.35


def _is_red_text_color(color: object) -> bool:
    # PyMuPDF text span color is often an int 0xRRGGBB.
    if isinstance(color, int):
        r = (color >> 16) & 255
        g = (color >> 8) & 255
        b = color & 255
        return r >= 180 and g <= 90 and b <= 90
    return _is_red_rgb(color)


def _looks_like_dimension_text(s: str) -> bool:
    t = str(s or "").strip()
    if not t:
        return False
    # Typical dimension tokens: numbers / decimals / fractions with simple symbols.
    return bool(re.fullmatch(r"[0-9.\-+/\"' xX]+", t))


def _highlight_red_target_layers(page) -> None:
    try:
        drawings = page.get_drawings()
    except Exception:
        return
    for d in drawings:
        if not _layer_is_target(d.get("layer")):
            continue
        is_red = _is_red_rgb(d.get("color")) or _is_red_rgb(d.get("fill"))
        if not is_red:
            continue
        r = d.get("rect", None)
        if r is None:
            continue
        _draw_dim_mask(page, fitz.Rect(r.x0, r.y0, r.x1, r.y1))


def _highlight_red_text(page) -> None:
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
        _draw_dim_mask(page, fitz.Rect(g["x0"], g["y0"], g["x1"], g["y1"]))


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
    page.draw_rect(
        fitz.Rect(x0, y0, x1, y1),
        color=DIM_HILITE_COLOR,
        fill=None,
        width=DIM_HILITE_STROKE_WIDTH,
        stroke_opacity=DIM_HILITE_STROKE_OPACITY,
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
      - Prefer W:\LASER\For Battleshield Fabrication\<F-number>\... first
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


def build_watermarked_packet(
    parts: List[PartRow],
    out_pdf_path: str,
    resolve_asset_fn: Optional[Callable[[str, str], Optional[str]]] = None,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> Tuple[int, int]:
    """
    Create a concatenated PDF packet and stamp QTY in bottom-left on each inserted page.
    Returns: (pages_written, missing_pdf_count)
    """
    ensure_dir(out_pdf_path)

    pages = 0
    missing = 0

    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is not available. Install it in your runtime Python.")

    dst = fitz.open()

    resolver = resolve_asset_fn or resolve_asset
    total = len(parts)

    if progress_cb is not None:
        try:
            progress_cb(0, total, "Starting packet build")
        except Exception:
            pass

    for i, p in enumerate(parts, start=1):
        part_name = os.path.splitext(os.path.basename(getattr(p, "sym", "") or ""))[0]
        status = part_name or f"Part {i}"
        pdf = resolver(p.sym, ".pdf")
        if not pdf or not os.path.exists(pdf):
            missing += 1
            status = f"{status} (missing PDF)"
            if progress_cb is not None:
                try:
                    progress_cb(i, total, status)
                except Exception:
                    pass
            continue

        try:
            src = fitz.open(pdf)
        except Exception:
            missing += 1
            status = f"{status} (open failed)"
            if progress_cb is not None:
                try:
                    progress_cb(i, total, status)
                except Exception:
                    pass
            continue

        if src.page_count < 1:
            src.close()
            missing += 1
            status = f"{status} (empty PDF)"
            if progress_cb is not None:
                try:
                    progress_cb(i, total, status)
                except Exception:
                    pass
            continue

        # insert page 0
        dst.insert_pdf(src, from_page=0, to_page=0)
        src.close()

        # watermark the last page inserted
        dst_page = dst.load_page(dst.page_count - 1)
        rect = dst_page.rect

        # Grayscale the Title layer before overlays.
        _grayscale_title_layer(dst_page)
        # Highlight red geometry on target OCG layers.
        _highlight_red_target_layers(dst_page)
        # Also highlight red text spans (dimension values + note text).
        _highlight_red_text(dst_page)

        qty = int(p.qty) if getattr(p, "qty", None) else 1
        text = f"QTY {qty}"

        margin = 18
        font_size = 24 * WATERMARK_TEXT_SCALE
        box_h = 46 * WATERMARK_TEXT_SCALE

        # bottom-left, tightly highlighted around text.
        x1 = margin
        y1 = rect.height - margin - box_h
        text_w = fitz.get_text_length(text, fontname="helv", fontsize=font_size)
        pad_x = 12
        x2 = x1 + text_w + (2 * pad_x)
        y2 = y1 + box_h
        dst_page.draw_rect(
            fitz.Rect(x1, y1, x2, y2),
            color=None,
            fill=WATERMARK_FILL_COLOR,
            fill_opacity=WATERMARK_OPACITY,
        )

        dst_page.insert_text(
            fitz.Point(x1 + pad_x, y1 + box_h * 0.72),
            text,
            fontsize=font_size,
            color=WATERMARK_TEXT_COLOR,
            fontname="helv",
        )

        pages += 1
        status = f"{status} (done)"
        if progress_cb is not None:
            try:
                progress_cb(i, total, status)
            except Exception:
                pass

    dst.save(out_pdf_path, deflate=True)
    dst.close()
    return pages, missing
