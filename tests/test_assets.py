from __future__ import annotations

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
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

    def test_resolve_asset_fast_concurrent_access_does_not_corrupt_caches(self) -> None:
        # resolve_asset_fast is called from ThreadPoolExecutor workers in
        # pdf_packet.py and ml_pipeline.py, so the module-level tree/result
        # caches must tolerate concurrent reads and writes without raising
        # or returning inconsistent results.
        with workspace_temp_dir("assets_concurrent") as tmpdir:
            source_root = os.path.join(tmpdir, "source")
            release_root = os.path.join(tmpdir, "release")

            part_names = [f"part{i}" for i in range(25)]
            sym_paths = []
            for name in part_names:
                part_dir = os.path.join(source_root, "F123", "Parts")
                os.makedirs(part_dir, exist_ok=True)
                sym_path = os.path.join(part_dir, f"{name}.sym")
                with open(sym_path, "w", encoding="utf-8") as handle:
                    handle.write("sym")
                sym_paths.append(sym_path)

                release_dir = os.path.join(release_root, "F123", "Parts")
                os.makedirs(release_dir, exist_ok=True)
                with open(os.path.join(release_dir, f"{name}.pdf"), "w", encoding="utf-8") as handle:
                    handle.write("pdf")

            assets.configure_release_mapping(
                w_release_root=release_root,
                eng_release_map=[(source_root, release_root)],
            )

            # Hammer resolve_asset_fast for the same and different sym paths
            # from many threads at once, repeatedly clearing the cache mid
            # flight so the read-check-write path is exercised under
            # contention rather than serving warmed results.
            def worker(idx: int) -> Optional[str]:
                sym_path = sym_paths[idx % len(sym_paths)]
                if idx % 7 == 0:
                    assets._clear_search_cache()
                return assets.resolve_asset_fast(sym_path, ".pdf")

            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(worker, range(200)))

            self.assertTrue(all(r is not None for r in results))
            for result_path, sym_path in zip(results, [sym_paths[i % len(sym_paths)] for i in range(200)]):
                expected_name = os.path.splitext(os.path.basename(sym_path))[0] + ".pdf"
                self.assertEqual(os.path.basename(result_path), expected_name)


if __name__ == "__main__":
    unittest.main()
