# pdf_preview.py
from __future__ import annotations

import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional, Tuple

# PyMuPDF
import fitz  # pip: pymupdf

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView
import runtime_trace as rt

PDF_PREVIEW_MIN_W = 420
PDF_PREVIEW_DPI = 500
PDF_PREVIEW_CACHE_MB = 8192
PDF_PREVIEW_RENDER_OVERSAMPLE = 2.0
PDF_PREVIEW_RESIZE_RERENDER_MS = 120
PDF_PREVIEW_CACHE_BUCKET_PX = 160
PDF_PREVIEW_INVERT_COLORS = False
PDF_PREVIEW_INVERT_GRAYSCALE_ONLY = True
PDF_PREVIEW_RENDER_TRACE_MIN_MS = 40


@dataclass
class _CacheEntry:
    pixmap: QPixmap
    dpi: int
    width: int
    height: int
    bytes_estimate: int


def _render_pdf_page_to_qimage(
    pdf_path: str,
    dpi: int = 300,
    target_px: Optional[Tuple[int, int]] = None,
    oversample: float = PDF_PREVIEW_RENDER_OVERSAMPLE,
) -> QImage:
    """
    Render page 0 at ~dpi into a QImage (cached by caller).
    """
    doc = fitz.open(pdf_path)
    try:
        if doc.page_count < 1:
            raise RuntimeError(f"PDF has no pages: {pdf_path}")
        page = doc.load_page(0)

        _apply_preferred_layers(doc)

        # 72 points per inch in PDF space.
        # Prefer viewport-aware scale, capped by DPI, to reduce heavy post-scale losses.
        scale = dpi / 72.0
        if target_px and target_px[0] > 0 and target_px[1] > 0:
            rect = page.rect
            if rect.width > 0 and rect.height > 0:
                fit_scale = min(float(target_px[0]) / rect.width, float(target_px[1]) / rect.height)
                scale = max(0.2, fit_scale * max(1.0, float(oversample)))
                scale = min(scale, max(72.0, float(dpi)) / 72.0)
        mat = fitz.Matrix(scale, scale)
        # Improve visibility of very thin CAD lines in preview rendering.
        try:
            fitz.TOOLS.set_graphics_min_line_width(1.0)
        except Exception:
            pass
        pix = page.get_pixmap(matrix=mat, alpha=False)

        # Create QImage (copy) from pixmap samples
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
        if PDF_PREVIEW_INVERT_COLORS:
            try:
                if PDF_PREVIEW_INVERT_GRAYSCALE_ONLY:
                    g = img.convertToFormat(QImage.Format_Grayscale8)
                    g.invertPixels()
                    img = g.convertToFormat(QImage.Format_RGB888)
                else:
                    img.invertPixels()
            except Exception:
                pass
        return img
    finally:
        doc.close()


def _norm_layer_name(text: str) -> str:
    return "".join(ch.lower() for ch in str(text) if ch.isalnum())


def _apply_preferred_layers(doc: fitz.Document) -> bool:
    """
    Layer policy:
    1) Turn all user-togglable OCG layers OFF.
    2) Turn ON only:
       - visible
       - visible narrow
       - bend centerline
       - title
    If none of the target layers exist, fall back to hiding known non-preview layers.
    """
    try:
        ui_cfgs = doc.layer_ui_configs()
    except Exception:
        return False

    if not ui_cfgs:
        return False

    target_names = {
        "visible",
        "visiblenarrow",
        "bendcenterline",
        "title",
        "titleblock",
        "dimension",
        "dim",
    }
    name_to_nums = {}
    for cfg in ui_cfgs:
        nm = _norm_layer_name(cfg.get("text", ""))
        try:
            num = int(cfg.get("number"))
        except Exception:
            continue
        if num < 0:
            continue
        name_to_nums.setdefault(nm, []).append(num)

    target_nums = set()
    for nm in target_names:
        target_nums.update(name_to_nums.get(nm, []))

    changed = False
    if target_nums:
        # Pass 1: force everything OFF.
        for cfg in ui_cfgs:
            try:
                num = int(cfg.get("number"))
            except Exception:
                continue
            if num < 0:
                continue
            is_on = int(cfg.get("on", 0)) != 0
            if is_on:
                try:
                    doc.set_layer_ui_config(num, 2)  # force OFF
                    changed = True
                except Exception:
                    pass
        # Pass 2: turn target production layers back ON.
        for num in sorted(target_nums):
            try:
                doc.set_layer_ui_config(num, 1)  # force ON
                changed = True
            except Exception:
                pass
        return changed

    # Fallback: no targets found. Hide requested non-preview layers by name.
    off_exact = {"0", "hidden", "border", "symbol"}
    for cfg in ui_cfgs:
        nm = _norm_layer_name(cfg.get("text", ""))
        if not (
            nm in off_exact
            or nm.startswith("symbol")
            or nm.startswith("border")
            or nm.startswith("hidden")
        ):
            continue
        try:
            num = int(cfg.get("number"))
        except Exception:
            continue
        if num < 0:
            continue
        is_on = int(cfg.get("on", 0)) != 0
        if is_on:
            try:
                doc.set_layer_ui_config(num, 2)  # force OFF
                changed = True
            except Exception:
                pass
    return changed


class PdfPreviewView(QGraphicsView):
    """
    Rendering-only PDF preview:
    - caches rendered QImage/QPixmap per (pdf_path, viewport bucket)
    - wheel zoom (cursor-centered) without re-rendering
    - fit baseline recomputed on resize, preserving user zoom multiplier
    """
    render_count_changed = Signal(int)

    def __init__(
        self,
        parent=None,
        dpi: int = PDF_PREVIEW_DPI,
        cache_limit_mb: int = PDF_PREVIEW_CACHE_MB,
        min_width: int = PDF_PREVIEW_MIN_W,
    ):
        super().__init__(parent)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self._pix_item = QGraphicsPixmapItem()
        self._pix_item.setTransformationMode(Qt.FastTransformation)
        self._scene.addItem(self._pix_item)

        self._cache: "OrderedDict[Tuple[str, int, int, int], _CacheEntry]" = OrderedDict()
        self._cache_bytes = 0
        self._cache_limit_bytes = 0
        self._pdf_path: Optional[str] = None
        self._last_render_key: Optional[Tuple[str, int, int, int]] = None
        self._viewport_background_tile = QPixmap()
        self._viewport_background_fill = QColor("#000000")

        self._dpi = PDF_PREVIEW_DPI
        self._render_count = 0

        # Baseline fit scale (depends on viewport) + user zoom multiplier.
        self._baseline_scale = 1.0
        self._user_zoom = 1.0

        # View behavior
        hints = QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform
        # Prefer preserving line contrast on transformed technical drawings.
        if hasattr(QPainter, "LosslessImageRendering"):
            hints |= QPainter.LosslessImageRendering
        self.setRenderHints(hints)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        # Zoom under cursor; resize remains centered.
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)

        # Optional: nicer feel
        self.setDragMode(QGraphicsView.ScrollHandDrag)

        # Re-render shortly after resize so rendered resolution tracks pane size.
        self._resize_rerender_timer = QTimer(self)
        self._resize_rerender_timer.setSingleShot(True)
        self._resize_rerender_timer.timeout.connect(self._rerender_current_for_viewport)

        # Default preview config now lives in this module.
        self.setMinimumWidth(max(240, int(min_width)))
        self.set_cache_limit_mb(int(cache_limit_mb))
        self.set_dpi(int(dpi))

    # ---------- Public API ----------

    def set_pdf(self, pdf_path: Optional[str], force_render: bool = False) -> None:
        """
        Set the PDF to display. If the path changes, render once and cache.
        """
        if not pdf_path:
            self._pdf_path = None
            self._last_render_key = None
            self._pix_item.setPixmap(QPixmap())
            self._scene.setSceneRect(0, 0, 1, 1)
            self._baseline_scale = 1.0
            self._user_zoom = 1.0
            self.resetTransform()
            return

        pdf_path = os.path.normpath(pdf_path)
        prev_path = self._pdf_path
        cache_key = self._cache_key_for_viewport(pdf_path)

        if (not force_render) and self._pdf_path == pdf_path and self._last_render_key == cache_key:
            # Same doc: no re-render. Just ensure fit baseline is sane.
            self._recompute_baseline_and_apply()
            return

        if not os.path.exists(pdf_path):
            # Show blank, do not crash.
            rt.event("pdf_preview", "skip", reason="missing_pdf", pdf_path=pdf_path)
            self._pdf_path = pdf_path
            self._last_render_key = None
            self._pix_item.setPixmap(QPixmap())
            self._scene.setSceneRect(0, 0, 1, 1)
            self._baseline_scale = 1.0
            self._user_zoom = 1.0
            self.resetTransform()
            return

        # Render (cached)
        entry = self._cache.get(cache_key)
        if entry is None:
            render_stage = rt.stage(
                "pdf_preview",
                "render_page",
                min_elapsed_ms=PDF_PREVIEW_RENDER_TRACE_MIN_MS,
                pdf_path=pdf_path,
                dpi=self._dpi,
                viewport_w=cache_key[1],
                viewport_h=cache_key[2],
            )
            try:
                qimg = _render_pdf_page_to_qimage(
                    pdf_path,
                    dpi=self._dpi,
                    target_px=(cache_key[1], cache_key[2]),
                    oversample=PDF_PREVIEW_RENDER_OVERSAMPLE,
                )
            except Exception as exc:
                render_stage.fail(exc)
                raise
            else:
                render_stage.success(image_w=qimg.width(), image_h=qimg.height())
            pm = QPixmap.fromImage(qimg)
            bytes_estimate = int(max(1, qimg.sizeInBytes()) + max(1, pm.width() * pm.height() * 4))
            entry = _CacheEntry(
                pixmap=pm,
                dpi=self._dpi,
                width=qimg.width(),
                height=qimg.height(),
                bytes_estimate=bytes_estimate,
            )
            self._cache[cache_key] = entry
            self._cache.move_to_end(cache_key)
            self._cache_bytes += bytes_estimate
            self._evict_cache_if_needed()
            self._render_count += 1
            self.render_count_changed.emit(self._render_count)
        else:
            self._cache.move_to_end(cache_key)

        self._pdf_path = pdf_path
        self._last_render_key = cache_key
        self._pix_item.setPixmap(entry.pixmap)
        self._scene.setSceneRect(0, 0, entry.width, entry.height)

        # New document -> reset zoom to fit. Viewport-only re-renders preserve zoom.
        if prev_path != pdf_path:
            self._user_zoom = 1.0
        self._recompute_baseline_and_apply()

    def set_dpi(self, dpi: int) -> None:
        """
        Changes DPI and clears cached renders so next preview is regenerated.
        """
        dpi = int(dpi)
        if dpi < 72:
            dpi = 72
        if dpi == self._dpi:
            return
        self._dpi = dpi
        current = self._pdf_path
        self._cache.clear()
        self._cache_bytes = 0
        self._pdf_path = None
        self._last_render_key = None
        if current:
            self.set_pdf(current)

    def set_cache_limit_mb(self, cache_mb: int) -> None:
        """
        Limit total cached preview images to roughly cache_mb megabytes.
        """
        cache_mb = int(cache_mb)
        if cache_mb < 128:
            cache_mb = 128
        self._cache_limit_bytes = cache_mb * 1024 * 1024
        self._evict_cache_if_needed()

    def set_viewport_background(
        self,
        *,
        tile: Optional[QPixmap] = None,
        fill_color: Optional[QColor] = None,
    ) -> None:
        if fill_color is not None:
            self._viewport_background_fill = QColor(fill_color)
        if tile is None or tile.isNull():
            self._viewport_background_tile = QPixmap()
        else:
            self._viewport_background_tile = QPixmap(tile)
        self.viewport().update()

    def reset_to_fit(self) -> None:
        """
        Reset zoom to fit-to-view baseline.
        """
        self._user_zoom = 1.0
        self._recompute_baseline_and_apply()

    def zoom_in(self, factor: float = 1.15) -> None:
        self._user_zoom *= max(1.01, float(factor))
        self._recompute_baseline_and_apply()

    def zoom_out(self, factor: float = 1.15) -> None:
        self._user_zoom /= max(1.01, float(factor))
        self._recompute_baseline_and_apply()

    # ---------- Internals ----------

    def _apply_current_zoom(self) -> None:
        """
        Apply baseline * user zoom via view transform. No re-render.
        """
        self._user_zoom = max(0.08, min(30.0, self._user_zoom))
        target = max(0.02, self._baseline_scale * self._user_zoom)
        # Smooth transform gives better readability while zooming.
        self._pix_item.setTransformationMode(Qt.SmoothTransformation)
        self.resetTransform()
        self.scale(target, target)

    def _recompute_baseline_and_apply(self) -> None:
        """
        Recompute baseline "fit to pane" scale based on viewport size,
        then apply baseline * user zoom. No re-render.
        """
        pm = self._pix_item.pixmap()
        if pm.isNull():
            self._baseline_scale = 1.0
            self._apply_current_zoom()
            return

        vw = max(1, self.viewport().width())
        vh = max(1, self.viewport().height())
        iw = max(1, pm.width())
        ih = max(1, pm.height())

        # Fit-to-view baseline (keep aspect)
        sx = vw / iw
        sy = vh / ih
        self._baseline_scale = min(sx, sy)

        self._apply_current_zoom()

    def _evict_cache_if_needed(self) -> None:
        """
        Drop least-recently-used previews when memory budget is exceeded.
        Keep at least one cached page.
        """
        if self._cache_limit_bytes <= 0:
            return
        while self._cache_bytes > self._cache_limit_bytes and len(self._cache) > 1:
            evicted = False
            for old_key in list(self._cache.keys()):
                if old_key == self._last_render_key:
                    continue
                old_entry = self._cache.pop(old_key)
                self._cache_bytes = max(0, self._cache_bytes - old_entry.bytes_estimate)
                evicted = True
                break
            if not evicted:
                break

    def _cache_key_for_viewport(self, pdf_path: str) -> Tuple[str, int, int, int]:
        vw = max(1, int(self.viewport().width()))
        vh = max(1, int(self.viewport().height()))
        if vw < 16 or vh < 16:
            vw = max(vw, 960)
            vh = max(vh, 720)
        bucket = max(32, int(PDF_PREVIEW_CACHE_BUCKET_PX))
        rw = ((vw + bucket - 1) // bucket) * bucket
        rh = ((vh + bucket - 1) // bucket) * bucket
        return (pdf_path, rw, rh, int(self._dpi))

    def _rerender_current_for_viewport(self) -> None:
        if not self._pdf_path:
            return
        # Re-check viewport bucket; re-render only if needed.
        self.set_pdf(self._pdf_path, force_render=False)

    # ---------- Events ----------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Keep image responsive immediately, then refresh render for new viewport bucket.
        self._recompute_baseline_and_apply()
        if self._pdf_path:
            self._resize_rerender_timer.start(PDF_PREVIEW_RESIZE_RERENDER_MS)

    def drawBackground(self, painter: QPainter, rect) -> None:
        del rect
        painter.save()
        painter.resetTransform()
        viewport_rect = self.viewport().rect()
        painter.fillRect(viewport_rect, self._viewport_background_fill)
        if not self._viewport_background_tile.isNull():
            painter.drawTiledPixmap(viewport_rect, self._viewport_background_tile)
        painter.restore()

    def wheelEvent(self, event: QWheelEvent) -> None:
        # Cursor-centered zoom.
        delta = event.angleDelta().y()
        if delta == 0:
            event.accept()
            return
        if delta > 0:
            self.zoom_in(1.12)
        else:
            self.zoom_out(1.12)
        event.accept()
        return

    def mousePressEvent(self, event: QMouseEvent) -> None:
        # Middle click resets zoom to fit.
        if event.button() == Qt.MiddleButton:
            self.reset_to_fit()
            event.accept()
            return
        super().mousePressEvent(event)

    def viewportEvent(self, event: QEvent) -> bool:
        # Some systems deliver wheel-button clicks directly to the viewport.
        if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
            if isinstance(event, QMouseEvent) and event.button() == Qt.MiddleButton:
                self.reset_to_fit()
                event.accept()
                return True
        return super().viewportEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        # Double-click resets to fit baseline
        if event.button() == Qt.LeftButton:
            self.reset_to_fit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
