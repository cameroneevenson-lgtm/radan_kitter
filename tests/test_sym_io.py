from __future__ import annotations

import os
import unittest

import sym_io
from test_support import workspace_temp_dir


class SymIoTests(unittest.TestCase):
    def test_set_part_comment_updates_radan_attribute_109_storage(self) -> None:
        with workspace_temp_dir("sym_part_comment") as tmpdir:
            sym_path = os.path.join(tmpdir, "Part A.sym")
            with open(sym_path, "w", encoding="utf-8") as handle:
                handle.write('<Symbol><Attr num="109" name="Comments" value="old"></Attr></Symbol>')

            self.assertTrue(sym_io.set_part_comment(sym_path, "Walls"))

            updated = open(sym_path, encoding="utf-8").read()
            self.assertIn('num="109"', updated)
            self.assertIn('value="Walls"', updated)

            updated_text, found = sym_io.set_part_comment_text(updated, "Walls & Doors")
            self.assertTrue(found)
            self.assertIn('value="Walls &amp; Doors"', updated_text)
            self.assertEqual(sym_io.part_comment_from_text(updated_text), "Walls & Doors")


if __name__ == "__main__":
    unittest.main()
