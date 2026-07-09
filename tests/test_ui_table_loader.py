from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QHeaderView, QTableView

import rpd_io
import ui_table_loader
from test_support import workspace_temp_dir
from ui_parts_table import PartsModel


RADAN_NS = rpd_io.RADAN_NS


class TableLoaderColumnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_part_column_is_content_sized_and_manually_resizable(self) -> None:
        long_part_name = "F56724-B10-LEFT-SIDE-COMPARTMENT-LONG-BRACKET-WITH-RETURN-FLANGE"
        rpd = f"""<?xml version="1.0" encoding="utf-8"?>
<Project xmlns="{RADAN_NS}">
    <Parts>
        <Part>
            <ID>1</ID>
            <Symbol>C:\\parts\\{long_part_name}.sym</Symbol>
            <Kit></Kit>
            <Priority>9</Priority>
            <Qty>1</Qty>
        </Part>
    </Parts>
</Project>
"""
        with workspace_temp_dir("ui_table_loader_columns") as tmpdir:
            rpd_path = os.path.join(tmpdir, "job.rpd")
            with open(rpd_path, "w", encoding="utf-8") as handle:
                handle.write(rpd)

            table = QTableView()
            table.resize(360, 240)

            try:
                _tree, _parts, _model = ui_table_loader.load_rpd_into_table(
                    rpd_path,
                    table=table,
                    canon_kits=["Kit 1", "Long Kit Name"],
                    kit_to_priority={},
                    sanitize_kit_name_fn=lambda value: str(value or "").strip(),
                    kit_text_for_rpd_fn=lambda _sym, kit: kit,
                    safe_int_1_9_fn=lambda value, default=9: int(value or default),
                    on_model_data_changed=lambda: None,
                    on_selection_changed=lambda: None,
                )

                header = table.horizontalHeader()
                part_col = PartsModel.PART_COL

                self.assertEqual(
                    header.sectionResizeMode(part_col),
                    QHeaderView.Interactive,
                )
                self.assertGreaterEqual(
                    table.columnWidth(part_col),
                    table.sizeHintForColumn(part_col),
                )
            finally:
                table.close()


if __name__ == "__main__":
    unittest.main()
