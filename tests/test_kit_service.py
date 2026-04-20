from __future__ import annotations

import os
import shutil
import unittest

import kit_service
from rpd_io import PartRow
from test_support import workspace_temp_dir


class KitServiceTests(unittest.TestCase):
    def test_prepare_kits_can_refresh_generated_kit_syms(self) -> None:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        donor_source = os.path.join(repo_root, "KitDonor-100Instances.sym")

        with workspace_temp_dir("kit_prepare_refresh") as tmpdir:
            donor_path = os.path.join(tmpdir, "KitDonor-100Instances.sym")
            shutil.copyfile(donor_source, donor_path)

            part_sym_path = os.path.join(tmpdir, "Part A.sym")
            shutil.copyfile(donor_source, part_sym_path)

            part = PartRow(
                pid="1",
                sym=part_sym_path,
                kit_text="",
                priority="9",
                qty=1,
                material="",
                thickness="",
            )
            part.kit_label = "Walls"

            refresh_calls: list[str] = []
            count = kit_service.prepare_kits(
                [part],
                rpd_path=os.path.join(tmpdir, "Demo.rpd"),
                donor_template_path=donor_path,
                bak_dirname="_bak",
                kits_dirname="_kits",
                kit_to_priority={"Walls": "8"},
                refresh_kit_fn=refresh_calls.append,
            )

            self.assertEqual(count, 1)
            self.assertEqual(len(refresh_calls), 1)
            self.assertTrue(refresh_calls[0].endswith(os.path.join("_kits", "Walls.sym")))
            self.assertTrue(os.path.exists(refresh_calls[0]))


if __name__ == "__main__":
    unittest.main()
