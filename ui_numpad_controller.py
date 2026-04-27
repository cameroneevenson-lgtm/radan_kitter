from __future__ import annotations

from time import monotonic
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QAbstractItemView, QTableView, QWidget

from ui_parts_table import PartsModel


class NumpadController:
    NUMPAD_TO_KIT_INDEX: Dict[int, int] = {
        Qt.Key_7: 6, Qt.Key_8: 7, Qt.Key_9: 8,
        Qt.Key_4: 3, Qt.Key_5: 4, Qt.Key_6: 5,
        Qt.Key_1: 0, Qt.Key_2: 1, Qt.Key_3: 2,
    }
    ENTER_LATCH_WINDOW_SEC = 1.0
    ENTER_DEBOUNCE_SEC = 0.18

    def __init__(
        self,
        *,
        table: QTableView,
        get_model: Callable[[], Optional[PartsModel]],
        canon_kits: Sequence[str],
        kit_to_priority: Mapping[str, str],
        sanitize_kit_name_fn: Callable[[str], str],
        preview_current_cb: Callable[[], None],
    ) -> None:
        self._table = table
        self._get_model = get_model
        self._canon_kits = list(canon_kits)
        self._kit_to_priority = dict(kit_to_priority)
        self._sanitize_kit_name = sanitize_kit_name_fn
        self._preview_current = preview_current_cb
        self._shortcuts: List[QShortcut] = []
        self._enter_latched_at = -1.0
        self._last_enter_press_at = -1.0

    def install_shortcuts(self, parent: QWidget) -> None:
        self._shortcuts.clear()

        def add_shortcut(seq: QKeySequence, handler) -> None:
            sc = QShortcut(seq, parent)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(handler)
            self._shortcuts.append(sc)

        for key, kit_idx in self.NUMPAD_TO_KIT_INDEX.items():
            add_shortcut(QKeySequence(key), lambda i=kit_idx: self.on_assign(i))
            add_shortcut(QKeySequence(key | Qt.KeypadModifier), lambda i=kit_idx: self.on_assign(i))

        add_shortcut(QKeySequence(Qt.Key_0), self.on_clear)
        add_shortcut(QKeySequence(Qt.Key_0 | Qt.KeypadModifier), self.on_clear)

        add_shortcut(QKeySequence(Qt.Key_Return), self.on_enter_accept_then_advance)
        add_shortcut(QKeySequence(Qt.Key_Enter), self.on_enter_accept_then_advance)
        add_shortcut(QKeySequence(Qt.Key_Return | Qt.KeypadModifier), self.on_enter_accept_then_advance)
        add_shortcut(QKeySequence(Qt.Key_Enter | Qt.KeypadModifier), self.on_enter_accept_then_advance)

        add_shortcut(QKeySequence(Qt.Key_Minus), lambda: self.on_move(-1))
        add_shortcut(QKeySequence(Qt.Key_Minus | Qt.KeypadModifier), lambda: self.on_move(-1))
        add_shortcut(QKeySequence(Qt.Key_Plus), lambda: self.on_move(+1))
        add_shortcut(QKeySequence(Qt.Key_Plus | Qt.KeypadModifier), lambda: self.on_move(+1))
        add_shortcut(QKeySequence(Qt.Key_Up), lambda: self.on_move(-1))
        add_shortcut(QKeySequence(Qt.Key_Down), lambda: self.on_move(+1))

    def handle_key(self, key: int) -> bool:
        if key in (Qt.Key_Return, Qt.Key_Enter):
            return self.on_enter_accept_then_advance()
        if key == Qt.Key_0:
            return self.on_clear()
        if key in self.NUMPAD_TO_KIT_INDEX:
            return self.on_assign(self.NUMPAD_TO_KIT_INDEX[key])
        if key == Qt.Key_Minus:
            return self.on_move(-1)
        if key == Qt.Key_Plus:
            return self.on_move(+1)
        if key == Qt.Key_Up:
            return self.on_move(-1)
        if key == Qt.Key_Down:
            return self.on_move(+1)
        return False

    def on_enter_accept_then_advance(self) -> bool:
        now = monotonic()

        # Debounce repeated triggers from key auto-repeat / duplicate delivery.
        if self._last_enter_press_at > 0.0 and (now - self._last_enter_press_at) < self.ENTER_DEBOUNCE_SEC:
            return False
        self._last_enter_press_at = now

        # Second Enter within latch window -> advance one row (down).
        if self._enter_latched_at > 0.0 and (now - self._enter_latched_at) <= self.ENTER_LATCH_WINDOW_SEC:
            self._enter_latched_at = -1.0
            return self.on_move(+1)

        # First Enter -> accept RF suggestion.
        accepted = self.on_accept_suggestion()
        if accepted:
            self._enter_latched_at = now
        else:
            self._enter_latched_at = -1.0
        return accepted

    def on_assign(self, kit_idx: int) -> bool:
        model = self._model()
        if model is None or self._table_is_editing():
            return False
        row = self._current_row(model)
        if row < 0:
            return False
        self._assign_kit_by_index(model, row, kit_idx)
        self._preview_current()
        return True

    def on_clear(self) -> bool:
        model = self._model()
        if model is None or self._table_is_editing():
            return False
        row = self._current_row(model)
        if row < 0:
            return False
        kit_idx = model.index(row, PartsModel.KIT_COL)
        pri_idx = model.index(row, PartsModel.PRIORITY_COL)
        model.setData(kit_idx, "", Qt.EditRole)
        model.setData(pri_idx, "5", Qt.EditRole)
        self._preview_current()
        return True

    def on_accept_suggestion(self) -> bool:
        model = self._model()
        if model is None or self._table_is_editing():
            return False
        row = self._current_row(model)
        if row < 0 or row >= len(model.rows):
            return False
        sug = self._sanitize_kit_name(model.rows[row].suggested_kit)
        if not sug:
            return False
        kit_idx = model.index(row, PartsModel.KIT_COL)
        model.setData(kit_idx, sug, Qt.EditRole)
        model.rows[row].approved = True
        model.rows[row].pending_suggest = False
        ok_idx = model.index(row, PartsModel.OK_COL)
        rv_idx = model.index(row, PartsModel.REVIEW_COL)
        tl = model.index(row, 0)
        model.dataChanged.emit(tl, rv_idx)
        self._preview_current()
        return True

    def on_move(self, delta: int) -> bool:
        model = self._model()
        if model is None or self._table_is_editing():
            return False
        try:
            self._table.setFocus(Qt.ShortcutFocusReason)
        except Exception:
            pass
        row = self._current_row(model)
        if row < 0:
            return False
        new_row = max(0, min(model.rowCount() - 1, row + int(delta)))
        col = self._table.currentIndex().column() if self._table.currentIndex().isValid() else 0
        idx = model.index(new_row, max(0, col))
        self._table.setCurrentIndex(idx)
        self._table.scrollTo(idx, QTableView.PositionAtCenter)
        self._preview_current()
        return True

    def _model(self) -> Optional[PartsModel]:
        return self._get_model()

    def _table_is_editing(self) -> bool:
        return self._table.state() == QAbstractItemView.EditingState

    def _current_row(self, model: PartsModel) -> int:
        idx = self._table.currentIndex()
        if idx.isValid():
            return idx.row()
        if model.rowCount() > 0:
            idx0 = model.index(0, 0)
            self._table.setCurrentIndex(idx0)
            return 0
        return -1

    def _assign_kit_by_index(self, model: PartsModel, row: int, kit_index: int) -> None:
        if not self._canon_kits:
            return
        kit_index = max(0, min(len(self._canon_kits) - 1, int(kit_index)))
        kit = self._canon_kits[kit_index]
        kit_idx = model.index(row, PartsModel.KIT_COL)
        pri_idx = model.index(row, PartsModel.PRIORITY_COL)
        model.setData(kit_idx, kit, Qt.EditRole)
        model.setData(pri_idx, self._kit_to_priority.get(kit, "9"), Qt.EditRole)
