# pdf_preview.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional

# PyMuPDF
import fitz  # pip: pymupdf

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap, QWheelEvent, QMouseEvent
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView


@dataclass
class _CacheEntry:
    qimage: QImage
    pixmap: QPixmap
    dpi: int
    width: int
    height: int


def _render_pdf_page_to_qimage(pdf_path: str, dpi: int = 300) -> QImage:
    """
    Render page 0 at ~dpi into a QImage (cached by caller).
    """
    doc = fitz.open(pdf_path)
    try:
        if doc.page_count < 1:
            raise RuntimeError(f"PDF has no pages: {pdf_path}")
        page = doc.load_page(0)

        # 72 points per inch in PDF space
        scale = dpi / 72.0
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)

        # Create QImage (copy) from pixmap samples
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
        return img.copy()
    finally:
        doc.close()


class PdfPreviewView(QGraphicsView):
    """
    Rendering-only PDF preview:
    - caches rendered QImage/QPixmap per pdf_path
    - zoom via view transform (no re-render)
    - fit-to-view baseline recomputed on resize, preserves user zoom multiplier
    """
    render_count_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self._pix_item = QGraphicsPixmapItem()
        self._scene.addItem(self._pix_item)

        self._cache: Dict[str, _CacheEntry] = {}
        self._pdf_path: Optional[str] = None

        self._dpi = 300
        self._render_count = 0

        # Baseline fit scale (depends on viewport), and user zoom multiplier
        self._baseline_scale = 1.0
        self._user_zoom = 1.0

        # View behavior
        self.setRenderHints(self.renderHints())
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        # For cursor-centered zoom
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)

        # Optional: nicer feel
        self.setDragMode(QGraphicsView.ScrollHandDrag)

    # ---------- Public API ----------

    def set_pdf(self, pdf_path: Optional[str]) -> None:
        """
        Set the PDF to display. If the path changes, render once and cache.
        """
        if not pdf_path:
            self._pdf_path = None
            self._pix_item.setPixmap(QPixmap())
            self._scene.setSceneRect(0, 0, 1, 1)
            self._baseline_scale = 1.0
            self._user_zoom = 1.0
            self.resetTransform()
            return

        pdf_path = os.path.normpath(pdf_path)

        if self._pdf_path == pdf_path:
            # Same doc: no re-render. Just ensure fit baseline is sane.
            self._recompute_baseline_and_apply()
            return

        if not os.path.exists(pdf_path):
            # Show blank, do not crash.
            self._pdf_path = pdf_path
            self._pix_item.setPixmap(QPixmap())
            self._scene.setSceneRect(0, 0, 1, 1)
            self._baseline_scale = 1.0
            self._user_zoom = 1.0
            self.resetTransform()
            return

        # Render (cached)
        entry = self._cache.get(pdf_path)
        if entry is None:
            qimg = _render_pdf_page_to_qimage(pdf_path, dpi=self._dpi)
            pm = QPixmap.fromImage(qimg)
            entry = _CacheEntry(
                qimage=qimg,
                pixmap=pm,
                dpi=self._dpi,
                width=qimg.width(),
                height=qimg.height(),
            )
            self._cache[pdf_path] = entry
            self._render_count += 1
            self.render_count_changed.emit(self._render_count)

        self._pdf_path = pdf_path
        self._pix_item.setPixmap(entry.pixmap)
        self._scene.setSceneRect(0, 0, entry.width, entry.height)

        # New doc -> reset zoom to fit baseline
        self._user_zoom = 1.0
        self._recompute_baseline_and_apply()

    def set_dpi(self, dpi: int) -> None:
        """
        Changes DPI for future renders. Existing cached pages are not re-rendered.
        """
        dpi = int(dpi)
        if dpi < 72:
            dpi = 72
        self._dpi = dpi

    def reset_to_fit(self) -> None:
        """
        Reset zoom to fit-to-view baseline.
        """
        self._user_zoom = 1.0
        self._recompute_baseline_and_apply()

    def zoom_in(self, factor: float = 1.15) -> None:
        self._user_zoom *= float(factor)
        self._apply_current_zoom()

    def zoom_out(self, factor: float = 1.15) -> None:
        self._user_zoom /= float(factor)
        self._apply_current_zoom()

    # ---------- Internals ----------

    def _apply_current_zoom(self) -> None:
        """
        Apply baseline * user zoom via view transform. No re-render.
        """
        # Clamp user zoom so it doesn't go insane
        self._user_zoom = max(0.05, min(20.0, self._user_zoom))

        target = self._baseline_scale * self._user_zoom
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

    # ---------- Events ----------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Resizing should only rescale cached image (no re-render)
        self._recompute_baseline_and_apply()

    def wheelEvent(self, event: QWheelEvent) -> None:
        # Wheel always zooms preview under cursor.
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        if delta > 0:
            self._user_zoom *= 1.15
        else:
            self._user_zoom /= 1.15
        self._apply_current_zoom()
        event.accept()
        return

    def mousePressEvent(self, event: QMouseEvent) -> None:
        # Middle click resets zoom to fit.
        if event.button() == Qt.MiddleButton:
            self.reset_to_fit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        # Double-click resets to fit baseline
        if event.button() == Qt.LeftButton:
            self.reset_to_fit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
