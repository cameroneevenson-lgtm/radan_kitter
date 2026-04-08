from __future__ import annotations

import os
import unittest
from unittest import mock

import packet_service
from rpd_io import PartRow
from test_support import workspace_temp_dir


class PacketServiceTests(unittest.TestCase):
    def test_build_packet_sorts_parts_in_windows_order(self) -> None:
        captured = {}

        def fake_build(parts, out_pdf_path, **kwargs):
            captured["parts"] = [os.path.basename(p.sym) for p in parts]
            captured["out_pdf_path"] = out_pdf_path
            return (len(parts), 0)

        parts = [
            PartRow(pid="1", sym=r"C:\parts\part10.sym", kit_text="", priority="1", qty=1, material="", thickness=""),
            PartRow(pid="2", sym=r"C:\parts\part2.sym", kit_text="", priority="1", qty=1, material="", thickness=""),
            PartRow(pid="3", sym=r"C:\parts\part1.sym", kit_text="", priority="1", qty=1, material="", thickness=""),
        ]

        with workspace_temp_dir("packet_service") as tmp:
            rpd_dir = os.path.join(tmp, "job")
            os.makedirs(rpd_dir, exist_ok=True)
            rpd_path = os.path.join(rpd_dir, "job.rpd")
            with open(rpd_path, "w", encoding="utf-8") as handle:
                handle.write("fixture")

            with mock.patch.object(packet_service.pdf_packet, "build_watermarked_packet", side_effect=fake_build):
                packet_path, pages, missing = packet_service.build_packet(
                    parts,
                    rpd_path=rpd_path,
                    out_dirname="_out",
                    resolve_asset_fn=lambda *_args, **_kwargs: None,
                    max_workers=1,
                    render_mode="vector",
                )

        self.assertEqual(captured["parts"], ["part1.sym", "part2.sym", "part10.sym"])
        self.assertTrue(packet_path.endswith(".pdf"))
        self.assertEqual((pages, missing), (3, 0))


if __name__ == "__main__":
    unittest.main()
