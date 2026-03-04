from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Callable, Dict, List, Tuple

from PySide6.QtWidgets import QHeaderView, QTableView

import rpd_io
from rpd_io import PartRow
from ui_parts_table import PartsModel


def hook_selection_model(table: QTableView, on_current_changed: Callable[[], None]) -> None:
    sm = table.selectionModel()
    if sm is None:
        return
    try:
        sm.currentChanged.disconnect()
    except Exception:
        pass
    def _on_current_changed(current, _previous) -> None:
        try:
            if current is not None and current.isValid():
                table.scrollTo(current, QTableView.PositionAtCenter)
        except Exception:
            pass
        on_current_changed()

    sm.currentChanged.connect(_on_current_changed)


def load_rpd_into_table(
    path: str,
    *,
    table: QTableView,
    canon_kits: List[str],
    kit_to_priority: Dict[str, str],
    sanitize_kit_name_fn: Callable[[str], str],
    kit_text_for_rpd_fn: Callable[[str, str], str],
    safe_int_1_9_fn: Callable[[str, int], int],
    on_model_data_changed: Callable[[], None],
    on_selection_changed: Callable[[], None],
) -> Tuple[ET.ElementTree, List[PartRow], PartsModel]:
    tree, parts, _debug = rpd_io.load_rpd(path)
    model = PartsModel(
        parts,
        sanitize_kit_name_fn=sanitize_kit_name_fn,
        kit_text_for_rpd_fn=kit_text_for_rpd_fn,
        safe_int_1_9_fn=safe_int_1_9_fn,
        kit_to_priority=kit_to_priority,
    )
    table.setModel(model)
    model.dataChanged.connect(lambda *_: on_model_data_changed())
    hook_selection_model(table, on_selection_changed)

    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.resizeColumnsToContents()
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)

    try:
        kit_col = 1
        fm = table.fontMetrics()
        maxw = max(fm.horizontalAdvance(k) for k in canon_kits) + 28
        table.setColumnWidth(kit_col, max(table.columnWidth(kit_col), maxw))
    except Exception:
        pass

    if model.rowCount() > 0 and not table.currentIndex().isValid():
        table.setCurrentIndex(model.index(0, 0))
    return tree, parts, model
