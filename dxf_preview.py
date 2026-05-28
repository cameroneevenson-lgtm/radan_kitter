from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

import ezdxf
from ezdxf.path import Path as EzdxfPath
from ezdxf.path import from_hatch, make_path

from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

import runtime_trace as rt

DXF_PREVIEW_MIN_W = 260
DXF_PREVIEW_CACHE_LIMIT = 256
DXF_PREVIEW_FLATTEN_DISTANCE = 0.05
DXF_PREVIEW_FLATTEN_SEGMENTS = 12
DXF_PREVIEW_PATH_SEGMENTS = 8
DXF_PREVIEW_SPLINE_LEVEL = 4
DXF_PREVIEW_RENDER_TRACE_MIN_MS = 40


@dataclass
class DxfRenderGeometry:
    path: QPainterPath
    bounds: QRectF
    entity_count: int
    unsupported_count: int


def _point_xy(point) -> Tuple[float, float]:
    return (float(point.x), float(point.y))


def _map_xy(x: float, y: float) -> Tuple[float, float]:
    return (float(x), -float(y))


def _iter_virtual_entities(entity) -> Iterable:
    try:
        yield from entity.virtual_entities()
    except Exception:
        return


def _iter_renderable_entities(doc) -> Iterable:
    for entity in doc.modelspace():
        if entity.dxftype() == "INSERT":
            yield from _iter_virtual_entities(entity)
            continue
        yield entity


def _iter_paths_for_entity(entity) -> Iterable[EzdxfPath]:
    if entity.dxftype() == "HATCH":
        for hatch_path in from_hatch(entity):
            yield hatch_path
        return

    dxf_path = make_path(
        entity,
        segments=DXF_PREVIEW_PATH_SEGMENTS,
        level=DXF_PREVIEW_SPLINE_LEVEL,
    )
    if dxf_path.has_sub_paths:
        yield from dxf_path.sub_paths()
    else:
        yield dxf_path


def _append_flattened_path(target: QPainterPath, dxf_path: EzdxfPath) -> bool:
    points = list(
        dxf_path.flattening(
            distance=DXF_PREVIEW_FLATTEN_DISTANCE,
            segments=DXF_PREVIEW_FLATTEN_SEGMENTS,
        )
    )
    if not points:
        return False

    x0, y0 = _point_xy(points[0])
    sx, sy = _map_xy(x0, y0)
    target.moveTo(sx, sy)
    appended = False
    last = (sx, sy)
    for point in points[1:]:
        x, y = _point_xy(point)
        mx, my = _map_xy(x, y)
        if abs(mx - last[0]) <= 1e-9 and abs(my - last[1]) <= 1e-9:
            continue
        target.lineTo(mx, my)
        last = (mx, my)
        appended = True

    if dxf_path.is_closed:
        target.closeSubpath()
        appended = True
    return appended


def build_dxf_render_geometry(dxf_path: str) -> DxfRenderGeometry:
    doc = ezdxf.readfile(dxf_path)
    path = QPainterPath()
    entity_count = 0
    unsupported_count = 0

    for entity in _iter_renderable_entities(doc):
        entity_count += 1
        try:
            appended = False
            for dxf_entity_path in _iter_paths_for_entity(entity):
                appended = _append_flattened_path(path, dxf_entity_path) or appended
            if not appended:
                unsupported_count += 1
        except TypeError:
            unsupported_count += 1
        except Exception:
            unsupported_count += 1

    bounds = path.boundingRect()
    return DxfRenderGeometry(
        path=path,
        bounds=bounds,
        entity_count=entity_count,
        unsupported_count=unsupported_count,
    )


class DxfPreviewView(QGraphicsView):
    render_count_changed = Signal(int)

    def __init__(
        self,
        parent=None,
        min_width: int = DXF_PREVIEW_MIN_W,
    ) -> None:
        super().__init__(parent)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self._path_item = QGraphicsPathItem()
        pen = QPen(QColor("#f8fafc"))
        pen.setWidth(0)
        pen.setCosmetic(True)
        self._path_item.setPen(pen)
        self._path_item.setBrush(Qt.NoBrush)
        self._scene.addItem(self._path_item)

        self._message_item = QGraphicsSimpleTextItem()
        self._message_item.setBrush(QColor("#94a3b8"))
        self._message_item.setVisible(False)
        self._scene.addItem(self._message_item)

        self._cache: "OrderedDict[str, DxfRenderGeometry]" = OrderedDict()
        self._dxf_path: Optional[str] = None
        self._baseline_scale = 1.0
        self._user_zoom = 1.0
        self._render_count = 0
        self._background_fill = QColor("#05070a")

        hints = QPainter.Antialiasing | QPainter.TextAntialiasing
        if hasattr(QPainter, "LosslessImageRendering"):
            hints |= QPainter.LosslessImageRendering
        self.setRenderHints(hints)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setMinimumWidth(max(180, int(min_width)))

    def set_dxf(self, dxf_path: Optional[str], *, message: str = "") -> None:
        if not dxf_path:
            self._dxf_path = None
            self._path_item.setPath(QPainterPath())
            self._show_message(message)
            self._user_zoom = 1.0
            self._recompute_baseline_and_apply()
            return

        dxf_path = os.path.normpath(dxf_path)
        if self._dxf_path == dxf_path and not self._path_item.path().isEmpty():
            self._recompute_baseline_and_apply()
            return

        if not os.path.exists(dxf_path):
            rt.event("dxf_preview", "skip", reason="missing_dxf", dxf_path=dxf_path)
            self._dxf_path = dxf_path
            self._path_item.setPath(QPainterPath())
            self._show_message(message or "DXF missing")
            self._user_zoom = 1.0
            self._recompute_baseline_and_apply()
            return

        geometry = self._cache.get(dxf_path)
        if geometry is None:
            render_stage = rt.stage(
                "dxf_preview",
                "render_dxf",
                min_elapsed_ms=DXF_PREVIEW_RENDER_TRACE_MIN_MS,
                dxf_path=dxf_path,
            )
            try:
                geometry = build_dxf_render_geometry(dxf_path)
            except Exception as exc:
                render_stage.fail(exc)
                self._dxf_path = dxf_path
                self._path_item.setPath(QPainterPath())
                self._show_message(f"Could not render DXF: {type(exc).__name__}")
                self._user_zoom = 1.0
                self._recompute_baseline_and_apply()
                return
            else:
                render_stage.success(
                    entity_count=geometry.entity_count,
                    unsupported_count=geometry.unsupported_count,
                    bounds_w=round(float(geometry.bounds.width()), 3),
                    bounds_h=round(float(geometry.bounds.height()), 3),
                )
            self._cache[dxf_path] = geometry
            self._cache.move_to_end(dxf_path)
            while len(self._cache) > DXF_PREVIEW_CACHE_LIMIT:
                self._cache.popitem(last=False)
            self._render_count += 1
            self.render_count_changed.emit(self._render_count)
        else:
            self._cache.move_to_end(dxf_path)

        prev_path = self._dxf_path
        self._dxf_path = dxf_path
        self._message_item.setVisible(False)
        self._path_item.setPath(QPainterPath(geometry.path))
        self._set_scene_rect_for_bounds(geometry.bounds)
        if prev_path != dxf_path:
            self._user_zoom = 1.0
        self._recompute_baseline_and_apply()

    def reset_to_fit(self) -> None:
        self._user_zoom = 1.0
        self._recompute_baseline_and_apply()

    def zoom_in(self, factor: float = 1.15) -> None:
        self._user_zoom *= max(1.01, float(factor))
        self._recompute_baseline_and_apply()

    def zoom_out(self, factor: float = 1.15) -> None:
        self._user_zoom /= max(1.01, float(factor))
        self._recompute_baseline_and_apply()

    def _show_message(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            self._message_item.setVisible(False)
            self._scene.setSceneRect(0, 0, 1, 1)
            return
        self._message_item.setText(text)
        self._message_item.setVisible(True)
        rect = self._message_item.boundingRect().adjusted(-24, -18, 24, 18)
        self._scene.setSceneRect(rect)
        self._message_item.setPos(rect.left() + 24, rect.top() + 18)

    def _set_scene_rect_for_bounds(self, bounds: QRectF) -> None:
        if bounds.isNull() or bounds.isEmpty():
            self._scene.setSceneRect(0, 0, 1, 1)
            return
        pad = max(bounds.width(), bounds.height(), 1.0) * 0.06
        self._scene.setSceneRect(bounds.adjusted(-pad, -pad, pad, pad))

    def _content_rect(self) -> QRectF:
        if self._message_item.isVisible():
            return self._message_item.sceneBoundingRect()
        rect = self._path_item.path().boundingRect()
        if rect.isNull() or rect.isEmpty():
            return QRectF(0, 0, 1, 1)
        return rect

    def _apply_current_zoom(self) -> None:
        self._user_zoom = max(0.08, min(30.0, self._user_zoom))
        target = max(0.02, self._baseline_scale * self._user_zoom)
        self.resetTransform()
        self.scale(target, target)
        self.centerOn(self._content_rect().center())

    def _recompute_baseline_and_apply(self) -> None:
        rect = self._scene.sceneRect()
        if rect.isNull() or rect.isEmpty():
            rect = QRectF(0, 0, 1, 1)
        vw = max(1, self.viewport().width())
        vh = max(1, self.viewport().height())
        sx = vw / max(1e-9, rect.width())
        sy = vh / max(1e-9, rect.height())
        self._baseline_scale = min(sx, sy)
        self._apply_current_zoom()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._recompute_baseline_and_apply()

    def drawBackground(self, painter: QPainter, rect) -> None:
        del rect
        painter.save()
        painter.resetTransform()
        painter.fillRect(self.viewport().rect(), self._background_fill)
        painter.restore()

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            event.accept()
            return
        if delta > 0:
            self.zoom_in(1.12)
        else:
            self.zoom_out(1.12)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MiddleButton:
            self.reset_to_fit()
            event.accept()
            return
        super().mousePressEvent(event)

    def viewportEvent(self, event: QEvent) -> bool:
        if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
            if isinstance(event, QMouseEvent) and event.button() == Qt.MiddleButton:
                self.reset_to_fit()
                event.accept()
                return True
        return super().viewportEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.reset_to_fit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
