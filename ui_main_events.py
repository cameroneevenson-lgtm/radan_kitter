from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QTableView

import runtime_trace as rt
from ui_numpad_controller import NumpadController
from ui_parts_table import PartsModel


def dispatch_numpad_legend_action(
    href: str,
    *,
    on_assign: Callable[[int], bool],
    on_clear: Callable[[], bool],
    on_accept: Callable[[], bool],
    on_move: Callable[[int], bool],
) -> None:
    action = str(href or "").strip().lower()
    if not action:
        return
    if action.startswith("assign:"):
        try:
            idx = int(action.split(":", 1)[1])
        except Exception:
            return
        on_assign(idx)
        return
    if action == "clear":
        on_clear()
        return
    if action == "accept":
        on_accept()
        return
    if action == "move_up":
        on_move(-1)
        return
    if action == "move_down":
        on_move(+1)
        return


def clear_selected_kits(
    *,
    table: QTableView,
    model: Optional[PartsModel],
    preview_current_cb: Callable[[], None],
) -> None:
    span = rt.begin("clear_selected_kits")
    if model is None:
        span.skip(reason="no_model")
        return
    sel = table.selectionModel().selectedRows()
    if not sel:
        idx = table.currentIndex()
        if idx.isValid():
            sel = [model.index(idx.row(), 0)]
    rows = sorted({i.row() for i in sel if i.isValid()})
    for r in rows:
        kit_idx = model.index(r, 1)
        pri_idx = model.index(r, 2)
        model.setData(kit_idx, "", Qt.EditRole)
        model.setData(pri_idx, "5", Qt.EditRole)
    preview_current_cb()
    span.success(rows_cleared=int(len(rows)))


def handle_main_keypress(e: QKeyEvent, *, numpad_controller: NumpadController) -> bool:
    if numpad_controller.handle_key(e.key()):
        e.accept()
        return True
    return False


def handle_space_event_filter(
    obj,
    event,
    *,
    table: QTableView,
    numpad_controller: NumpadController,
) -> bool:
    if (
        obj in (table, table.viewport())
        and event.type() == QEvent.KeyPress
        and isinstance(event, QKeyEvent)
        and event.key() == Qt.Key_Space
    ):
        if numpad_controller.on_move(+1):
            event.accept()
            return True
    return False
