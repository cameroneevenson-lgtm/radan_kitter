from __future__ import annotations

import os
import unittest
from unittest import mock

import assets
from test_support import workspace_temp_dir


class AssetLookupTests(unittest.TestCase):
    def tearDown(self) -> None:
        assets._clear_search_cache()
        assets.configure_release_mapping(
            w_release_root=assets.DEFAULT_W_RELEASE_ROOT,
            eng_release_map=list(assets.DEFAULT_ENG_RELEASE_MAP),
        )

    def test_resolve_asset_caches_missing_results(self) -> None:
        with workspace_temp_dir("assets_cache") as tmpdir:
            source_root = os.path.join(tmpdir, "source")
            release_root = os.path.join(tmpdir, "release")
            sym_path = os.path.join(source_root, "F123", "Parts", "missing.sym")
            os.makedirs(os.path.dirname(sym_path), exist_ok=True)
            os.makedirs(release_root, exist_ok=True)
            with open(sym_path, "w", encoding="utf-8") as handle:
                handle.write("sym")

            assets.configure_release_mapping(
                w_release_root=release_root,
                eng_release_map=[(source_root, release_root)],
            )

            listdir_calls = {"count": 0}
            real_listdir = os.listdir

            def counting_listdir(path: str):
                listdir_calls["count"] += 1
                return real_listdir(path)

            with mock.patch("assets.os.listdir", side_effect=counting_listdir):
                self.assertIsNone(assets.resolve_asset(sym_path, ".pdf"))
                first_call_count = listdir_calls["count"]
                self.assertIsNone(assets.resolve_asset(sym_path, ".pdf"))

            self.assertGreater(first_call_count, 0)
            self.assertEqual(listdir_calls["count"], first_call_count)

    def test_resolve_asset_skips_broad_root_listing_for_missing_symbol(self) -> None:
        with workspace_temp_dir("assets_broad_root") as tmpdir:
            source_root = os.path.join(tmpdir, "source")
            release_root = os.path.join(tmpdir, "release")
            sym_path = os.path.join(source_root, "F123", "Parts", "missing.sym")
            os.makedirs(os.path.dirname(sym_path), exist_ok=True)
            os.makedirs(release_root, exist_ok=True)
            os.makedirs(os.path.join(release_root, "Parts"), exist_ok=True)
            with open(sym_path, "w", encoding="utf-8") as handle:
                handle.write("sym")

            assets.configure_release_mapping(
                w_release_root=release_root,
                eng_release_map=[(source_root, release_root)],
            )

            touched_paths: list[str] = []
            real_listdir = os.listdir

            def tracking_listdir(path: str):
                touched_paths.append(os.path.normpath(path))
                return real_listdir(path)

            with mock.patch("assets.os.listdir", side_effect=tracking_listdir):
                self.assertIsNone(assets.resolve_asset(sym_path, ".pdf"))

            self.assertNotIn(os.path.normpath(release_root), touched_paths)
            self.assertNotIn(os.path.normpath(os.path.join(release_root, "Parts")), touched_paths)

    def test_resolve_asset_fast_never_walks_subtrees(self) -> None:
        with workspace_temp_dir("assets_fast") as tmpdir:
            source_root = os.path.join(tmpdir, "source")
            release_root = os.path.join(tmpdir, "release")
            sym_path = os.path.join(source_root, "F123", "Parts", "missing.sym")
            os.makedirs(os.path.dirname(sym_path), exist_ok=True)
            os.makedirs(os.path.join(release_root, "F123"), exist_ok=True)
            with open(sym_path, "w", encoding="utf-8") as handle:
                handle.write("sym")

            assets.configure_release_mapping(
                w_release_root=release_root,
                eng_release_map=[(source_root, release_root)],
            )

            with mock.patch("assets.os.walk", side_effect=AssertionError("preview lookup should not walk")):
                self.assertIsNone(assets.resolve_asset_fast(sym_path, ".pdf"))


if __name__ == "__main__":
    unittest.main()
