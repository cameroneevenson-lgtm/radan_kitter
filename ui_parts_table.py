from __future__ import annotations

from typing import Callable, Dict, List, Tuple

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QComboBox, QSpinBox, QStyledItemDelegate

from app_utils import windows_natural_sort_key
from rpd_io import PartRow


class KitComboDelegate(QStyledItemDelegate):
    def __init__(self, canon_kits: List[str], balance_kit: str, parent=None):
        super().__init__(parent)
        self._canon_kits = list(canon_kits)
        self._balance_kit = str(balance_kit)

    def createEditor(self, parent, option, index):
        cb = QComboBox(parent)
        cb.setEditable(True)
        cb.addItem("")
        for k in self._canon_kits:
            cb.addItem(k)
        cb.addItem(self._balance_kit)
        cb.setInsertPolicy(QComboBox.NoInsert)
        return cb

    def setEditorData(self, editor, index):
        editor.setCurrentText(str(index.data(Qt.EditRole) or ""))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.EditRole)


class PrioritySpinDelegate(QStyledItemDelegate):
    def __init__(self, safe_int_1_9_fn: Callable[[str, int], int], parent=None):
        super().__init__(parent)
        self._safe_int_1_9 = safe_int_1_9_fn

    def createEditor(self, parent, option, index):
        sp = QSpinBox(parent)
        sp.setRange(1, 9)
        return sp

    def setEditorData(self, editor, index):
        editor.setValue(self._safe_int_1_9(index.data(Qt.EditRole) or "9", default=9))

    def setModelData(self, editor, model, index):
        model.setData(index, str(editor.value()), Qt.EditRole)


class PartsModel(QAbstractTableModel):
    PART_COL = 0
    QTY_COL = 1
    KIT_COL = 2
    PRIORITY_COL = 3
    SUGGEST_COL = 4
    CONF_COL = 5
    OK_COL = 6
    REVIEW_COL = 7
    HEADERS = ["Part", "Qty", "Kit", "Priority", "Suggest", "Conf", "OK", "Review"]

    def __init__(
        self,
        rows: List[PartRow],
        sanitize_kit_name_fn: Callable[[str], str],
        kit_text_for_rpd_fn: Callable[[str, str], str],
        safe_int_1_9_fn: Callable[[str, int], int],
        kit_to_priority: Dict[str, str],
    ):
        super().__init__()
        self.rows = rows
        self._sanitize_kit_name = sanitize_kit_name_fn
        self._kit_text_for_rpd = kit_text_for_rpd_fn
        self._safe_int_1_9 = safe_int_1_9_fn
        self._kit_to_priority = dict(kit_to_priority)

    @staticmethod
    def _qty(r: PartRow) -> int:
        try:
            return max(1, int(getattr(r, "qty", 1) or 1))
        except Exception:
            return 1

    def rowCount(self, _=None):
        return len(self.rows)

    def columnCount(self, _=None):
        return len(self.HEADERS)

    def headerData(self, i, o, r):
        if o == Qt.Horizontal and r == Qt.DisplayRole:
            return self.HEADERS[i]
        return None

    def _compute_review(self, r: PartRow) -> bool:
        sug = (r.suggested_kit or "").strip()
        kit = (r.kit_label or "").strip()
        needs = False
        if sug:
            if not r.approved:
                needs = True
            elif kit and kit != sug:
                needs = True
        r.needs_review = needs
        return needs

    def data(self, idx: QModelIndex, role):
        if not idx.isValid():
            return None
        r = self.rows[idx.row()]
        c = idx.column()

        if role == Qt.BackgroundRole:
            if bool(getattr(r, "pending_suggest", False)):
                return QColor("#fff2a8")
            return None

        if role in (Qt.DisplayRole, Qt.EditRole):
            if c == self.PART_COL:
                return r.part
            if c == self.QTY_COL:
                return self._qty(r)
            if c == self.KIT_COL:
                return r.kit_label
            if c == self.PRIORITY_COL:
                return int(self._safe_int_1_9(r.priority, default=9))
            if c == self.SUGGEST_COL:
                return r.suggested_kit
            if c == self.CONF_COL:
                return f"{r.suggested_conf:.2f}" if r.suggested_kit else ""
            if c == self.OK_COL:
                return "Y" if r.approved else ""
            if c == self.REVIEW_COL:
                return "!" if self._compute_review(r) else ""
        return None

    def flags(self, idx: QModelIndex):
        if not idx.isValid():
            return Qt.ItemIsEnabled
        f = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if idx.column() in (self.KIT_COL, self.PRIORITY_COL):
            f |= Qt.ItemIsEditable
        return f

    def setData(self, idx: QModelIndex, val, role):
        if not idx.isValid() or role != Qt.EditRole:
            return False
        r = self.rows[idx.row()]
        c = idx.column()

        if c == self.KIT_COL:
            kit_label = self._sanitize_kit_name(str(val or ""))
            r.kit_label = kit_label
            r.kit_text = self._kit_text_for_rpd(r.sym, kit_label) if kit_label else ""
            if kit_label in self._kit_to_priority:
                r.priority = self._kit_to_priority[kit_label]
            r.approved = False
            r.pending_suggest = False
            self.dataChanged.emit(idx, idx)
            pri_idx = self.index(idx.row(), self.PRIORITY_COL)
            self.dataChanged.emit(pri_idx, pri_idx)
            ok_idx = self.index(idx.row(), self.OK_COL)
            rv_idx = self.index(idx.row(), self.REVIEW_COL)
            self.dataChanged.emit(ok_idx, rv_idx)
            return True

        if c == self.PRIORITY_COL:
            r.priority = str(self._safe_int_1_9(val, default=9))
            self.dataChanged.emit(idx, idx)
            return True

        return False

    def set_predictions(self, preds: List[Tuple[str, float]]):
        for row, (k, conf) in zip(self.rows, preds):
            row.suggested_kit = k or ""
            row.suggested_conf = float(conf or 0.0)
            row.pending_suggest = False
        if self.rowCount():
            tl = self.index(0, 0)
            br = self.index(self.rowCount() - 1, self.REVIEW_COL)
            self.dataChanged.emit(tl, br)

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder) -> None:
        reverse = order == Qt.DescendingOrder

        def key(p: PartRow):
            if column == self.PART_COL:
                return windows_natural_sort_key(p.part)
            if column == self.QTY_COL:
                return self._qty(p)
            if column == self.KIT_COL:
                return (p.kit_label or "").upper()
            if column == self.PRIORITY_COL:
                return int(self._safe_int_1_9(p.priority, default=9))
            if column == self.SUGGEST_COL:
                return (p.suggested_kit or "").upper()
            if column == self.CONF_COL:
                return float(p.suggested_conf or 0.0)
            if column == self.OK_COL:
                return 1 if p.approved else 0
            if column == self.REVIEW_COL:
                sug = (p.suggested_kit or "").strip()
                kit = (p.kit_label or "").strip()
                needs = False
                if sug:
                    if not p.approved:
                        needs = True
                    elif kit and kit != sug:
                        needs = True
                return 1 if needs else 0
            return ""

        self.layoutAboutToBeChanged.emit()
        self.rows.sort(key=key, reverse=reverse)
        self.layoutChanged.emit()
