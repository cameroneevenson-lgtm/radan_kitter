from __future__ import annotations

import os
import unittest

from packet_paths import force_w_candidates, map_to_eng_release, resolve_asset
from test_support import workspace_temp_dir


class PacketPathsTests(unittest.TestCase):
    def test_map_to_eng_release_rewrites_known_root(self) -> None:
        mapped = map_to_eng_release(
            r"L:\BATTLESHIELD\F123\Parts\abc.sym",
            eng_release_map=[(r"L:\BATTLESHIELD", r"W:\Release")],
        )
        self.assertEqual(mapped, os.path.normpath(r"W:\Release\F123\Parts\abc.sym"))

    def test_force_w_candidates_prefers_f_number_release_paths(self) -> None:
        candidates = force_w_candidates(
            r"L:\BATTLESHIELD\F123\Parts\abc.sym",
            w_release_root=r"W:\Release",
            eng_release_map=[(r"L:\BATTLESHIELD", r"W:\Release")],
        )
        self.assertEqual(candidates[0], os.path.normpath(r"W:\Release\F123\Parts\abc"))
        self.assertIn(os.path.normpath(r"W:\Release\F123\abc"), candidates)

    def test_resolve_asset_uses_release_path_before_local_fallback(self) -> None:
        with workspace_temp_dir("packet_paths") as tmpdir:
            source_root = os.path.join(tmpdir, "source")
            release_root = os.path.join(tmpdir, "release")
            sym_path = os.path.join(source_root, "F123", "Parts", "abc.sym")
            release_pdf = os.path.join(release_root, "F123", "Parts", "abc.pdf")
            local_pdf = os.path.join(source_root, "F123", "Parts", "abc.pdf")

            os.makedirs(os.path.dirname(sym_path), exist_ok=True)
            os.makedirs(os.path.dirname(release_pdf), exist_ok=True)
            with open(sym_path, "w", encoding="utf-8") as f:
                f.write("sym")
            with open(local_pdf, "w", encoding="utf-8") as f:
                f.write("local")
            with open(release_pdf, "w", encoding="utf-8") as f:
                f.write("release")

            resolved = resolve_asset(
                sym_path,
                ".pdf",
                w_release_root=release_root,
                eng_release_map=[(source_root, release_root)],
            )
            self.assertEqual(resolved, os.path.normpath(release_pdf))


if __name__ == "__main__":
    unittest.main()
