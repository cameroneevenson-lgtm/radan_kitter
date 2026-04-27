from __future__ import annotations

import unittest

from PySide6.QtCore import Qt

from rpd_io import PartRow
from ui_parts_table import PartsModel


def _model_for(rows):
    return PartsModel(
        rows,
        sanitize_kit_name_fn=lambda value: str(value or "").strip(),
        kit_text_for_rpd_fn=lambda _sym, kit: kit,
        safe_int_1_9_fn=lambda value, default=9: int(value or default),
        kit_to_priority={},
    )


class PartsModelTests(unittest.TestCase):
    def test_model_exposes_qty_column_without_changing_part_text(self) -> None:
        part = PartRow(
            pid="1",
            sym=r"C:\parts\Repeat.sym",
            kit_text="",
            priority="1",
            qty=3,
            material="",
            thickness="",
        )
        model = _model_for([part])
        part_idx = model.index(0, PartsModel.PART_COL)
        qty_idx = model.index(0, PartsModel.QTY_COL)

        self.assertIn("Qty", PartsModel.HEADERS)
        self.assertEqual(model.data(part_idx, Qt.DisplayRole), "Repeat")
        self.assertEqual(model.data(part_idx, Qt.EditRole), "Repeat")
        self.assertEqual(model.data(qty_idx, Qt.DisplayRole), 3)
        self.assertEqual(model.data(qty_idx, Qt.EditRole), 3)

    def test_qty_column_shows_single_qty_too(self) -> None:
        part = PartRow(
            pid="1",
            sym=r"C:\parts\Single.sym",
            kit_text="",
            priority="1",
            qty=1,
            material="",
            thickness="",
        )
        model = _model_for([part])
        part_idx = model.index(0, PartsModel.PART_COL)
        qty_idx = model.index(0, PartsModel.QTY_COL)

        self.assertEqual(model.data(part_idx, Qt.DisplayRole), "Single")
        self.assertEqual(model.data(qty_idx, Qt.DisplayRole), 1)


if __name__ == "__main__":
    unittest.main()
