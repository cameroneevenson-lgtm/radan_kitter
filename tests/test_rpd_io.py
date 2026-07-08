from __future__ import annotations

import os
import unittest

import rpd_io
from test_support import workspace_temp_dir

RADAN_NS = rpd_io.RADAN_NS

RPD_WITH_COMMENT = f"""<?xml version="1.0" encoding="utf-8"?>
<Project xmlns="{RADAN_NS}">
    <!-- operator note: do not delete -->
    <Parts>
        <Part>
            <ID>1</ID>
            <Symbol>L:\\BATTLESHIELD\\F-LARGE FLEET\\Job\\Part A.sym</Symbol>
            <Kit></Kit>
            <Priority>9</Priority>
            <Qty>2</Qty>
        </Part>
        <Part>
            <ID>2</ID>
            <Symbol>L:\\BATTLESHIELD\\F-LARGE FLEET\\Job\\Part B.sym</Symbol>
            <Kit></Kit>
            <Priority>9</Priority>
            <Qty>1</Qty>
        </Part>
    </Parts>
</Project>
"""

RPD_NO_COMMENT = f"""<?xml version="1.0" encoding="utf-8"?>
<Project xmlns="{RADAN_NS}">
    <Parts>
        <Part>
            <ID>1</ID>
            <Symbol>L:\\BATTLESHIELD\\F-LARGE FLEET\\Job\\Part A.sym</Symbol>
            <Kit></Kit>
            <Priority>9</Priority>
            <Qty>1</Qty>
        </Part>
    </Parts>
</Project>
"""


class RpdIoTests(unittest.TestCase):
    def test_load_rpd_parses_parts_correctly_alongside_a_comment(self) -> None:
        with workspace_temp_dir("rpd_io_load") as tmpdir:
            rpd_path = os.path.join(tmpdir, "job.rpd")
            with open(rpd_path, "w", encoding="utf-8") as handle:
                handle.write(RPD_WITH_COMMENT)

            _tree, parts, _debug = rpd_io.load_rpd(rpd_path)

            self.assertEqual(len(parts), 2)
            self.assertEqual(parts[0].pid, "1")
            self.assertTrue(parts[0].sym.endswith("Part A.sym"))
            self.assertEqual(parts[0].qty, 2)
            self.assertEqual(parts[1].pid, "2")
            self.assertEqual(parts[1].qty, 1)

    def test_write_rpd_in_place_preserves_source_comment(self) -> None:
        with workspace_temp_dir("rpd_io_comment") as tmpdir:
            rpd_path = os.path.join(tmpdir, "job.rpd")
            with open(rpd_path, "w", encoding="utf-8") as handle:
                handle.write(RPD_WITH_COMMENT)

            tree, parts, _debug = rpd_io.load_rpd(rpd_path)
            parts[0].kit_text = "Bottoms"
            parts[0].priority = "3"
            rpd_io.write_rpd_in_place(tree, parts, rpd_path)

            written = open(rpd_path, encoding="utf-8").read()
            self.assertIn("operator note: do not delete", written)

    def test_write_rpd_in_place_updates_matched_id_and_ignores_unmatched(self) -> None:
        with workspace_temp_dir("rpd_io_write") as tmpdir:
            rpd_path = os.path.join(tmpdir, "job.rpd")
            with open(rpd_path, "w", encoding="utf-8") as handle:
                handle.write(RPD_WITH_COMMENT)

            tree, parts, _debug = rpd_io.load_rpd(rpd_path)
            # Only keep/update part "1"; part "2" is not in the write list.
            updated = [p for p in parts if p.pid == "1"]
            updated[0].kit_text = "Bottoms"
            updated[0].priority = "3"
            rpd_io.write_rpd_in_place(tree, updated, rpd_path)

            _tree2, reloaded, _debug2 = rpd_io.load_rpd(rpd_path)
            by_id = {p.pid: p for p in reloaded}
            self.assertEqual(by_id["1"].kit_text, "Bottoms")
            self.assertEqual(by_id["1"].priority, "3")
            # Part "2" was never passed to write_rpd_in_place, so it is untouched.
            self.assertEqual(by_id["2"].kit_text, "")
            self.assertEqual(by_id["2"].priority, "9")

    def test_load_then_write_round_trip_with_no_comment_still_works(self) -> None:
        with workspace_temp_dir("rpd_io_roundtrip") as tmpdir:
            rpd_path = os.path.join(tmpdir, "job.rpd")
            with open(rpd_path, "w", encoding="utf-8") as handle:
                handle.write(RPD_NO_COMMENT)

            tree, parts, _debug = rpd_io.load_rpd(rpd_path)
            parts[0].kit_text = "Sides"
            parts[0].priority = "2"
            rpd_io.write_rpd_in_place(tree, parts, rpd_path)

            _tree2, reloaded, _debug2 = rpd_io.load_rpd(rpd_path)
            self.assertEqual(reloaded[0].kit_text, "Sides")
            self.assertEqual(reloaded[0].priority, "2")


if __name__ == "__main__":
    unittest.main()
