from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Callable, Dict, List, Tuple

from PySide6.QtWidgets import QHeaderView, QTableView

import rpd_io
import runtime_trace as rt
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
    load_stage = rt.stage("load_rpd_path", "parse_rpd", min_elapsed_ms=10, rpd_path=path)
    try:
        tree, parts, debug = rpd_io.load_rpd(path)
    except Exception as exc:
        load_stage.fail(exc)
        raise
    else:
        load_stage.success(
            part_count=len(parts),
            sample_child_tags=str(debug.get("sample_child_tags", "")),
        )

    model_stage = rt.stage("load_rpd_path", "build_parts_model", min_elapsed_ms=10, part_count=len(parts))
    try:
        model = PartsModel(
            parts,
            sanitize_kit_name_fn=sanitize_kit_name_fn,
            kit_text_for_rpd_fn=kit_text_for_rpd_fn,
            safe_int_1_9_fn=safe_int_1_9_fn,
            kit_to_priority=kit_to_priority,
        )
    except Exception as exc:
        model_stage.fail(exc)
        raise
    else:
        model_stage.success(column_count=len(PartsModel.HEADERS))

    with rt.stage("load_rpd_path", "bind_table_model", min_elapsed_ms=10, part_count=len(parts)):
        table.setModel(model)
        model.dataChanged.connect(lambda *_: on_model_data_changed())
        hook_selection_model(table, on_selection_changed)

    header = table.horizontalHeader()
    with rt.stage("load_rpd_path", "resize_table_columns", min_elapsed_ms=10, part_count=len(parts)):
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        table.resizeColumnsToContents()
        # Fill the table pane horizontally by stretching visible columns.
        header.setSectionResizeMode(QHeaderView.Stretch)

    try:
        kit_col = PartsModel.KIT_COL
        fm = table.fontMetrics()
        maxw = max(fm.horizontalAdvance(k) for k in canon_kits) + 28
        table.setColumnWidth(kit_col, max(table.columnWidth(kit_col), maxw))
    except Exception:
        pass

    # Keep priority in the model/workflows, but hide it from table view.
    try:
        pri_col = next(
            i for i, h in enumerate(PartsModel.HEADERS)
            if str(h or "").strip().lower() == "priority"
        )
        table.setColumnHidden(int(pri_col), True)
    except Exception:
        pass

    try:
        header.setSectionResizeMode(PartsModel.QTY_COL, QHeaderView.ResizeToContents)
        table.resizeColumnToContents(PartsModel.QTY_COL)
    except Exception:
        pass

    if model.rowCount() > 0 and not table.currentIndex().isValid():
        with rt.stage("load_rpd_path", "select_first_row", min_elapsed_ms=5, part_count=len(parts)):
            table.setCurrentIndex(model.index(0, 0))
    return tree, parts, model
