from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import assets
import ui_actions
from rpd_io import PartRow

ROOT = Path(__file__).resolve().parents[1]


def _make_fixture_root() -> Path:
    base = ROOT / "_runtime" / "test_tmp" / uuid4().hex
    base.mkdir(parents=True, exist_ok=True)
    return base


def _part(pid: str, sym: str) -> PartRow:
    return PartRow(
        pid=pid,
        sym=sym,
        kit_text="",
        priority="1",
        qty=1,
        material="",
        thickness="",
    )


class LoadTruckNestExplorerPacketBuildServiceTests(unittest.TestCase):
    def test_loads_the_real_sibling_module(self) -> None:
        module = ui_actions._load_truck_nest_explorer_packet_build_service()
        self.assertIsNotNone(module)
        for name in (
            "collect_unused_tabloid_pdfs",
            "scan_assembly_bom_context",
            "apply_assembly_notes_to_parts",
            "apply_assembly_context_to_sym_comments",
        ):
            self.assertTrue(hasattr(module, name))

    def test_does_not_corrupt_radan_kitters_own_modules(self) -> None:
        import config

        before = config
        ui_actions._load_truck_nest_explorer_packet_build_service()
        import config as config_after

        self.assertIs(before, config_after)
        self.assertIn("radan_kitter", str(config_after.__file__).replace("/", "\\"))


class ScanAndStampAssemblyContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _make_fixture_root()
        self._original_override = assets.W_RELEASE_ROOT

    def tearDown(self) -> None:
        assets.set_asset_root_override(self._original_override, persist=False, source="test-restore")
        shutil.rmtree(self.root, ignore_errors=True)

    def test_no_op_when_truck_nest_explorer_is_unavailable(self) -> None:
        parts = [_part("1", str(self.root / "F55334-B-1001.sym"))]
        with patch.object(ui_actions, "_load_truck_nest_explorer_packet_build_service", return_value=None):
            ui_actions._scan_and_stamp_assembly_context(parts, str(self.root / "F55334 PAINT PACK.rpd"))
        self.assertEqual(parts[0].assembly_note, "")

    def test_no_op_for_non_paint_pack_rpd(self) -> None:
        parts = [_part("1", str(self.root / "F59487-1.sym"))]
        fake_tne = SimpleNamespace(
            collect_unused_tabloid_pdfs=lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called"))
        )
        with patch.object(ui_actions, "_load_truck_nest_explorer_packet_build_service", return_value=fake_tne):
            ui_actions._scan_and_stamp_assembly_context(parts, str(self.root / "F59487.rpd"))
        self.assertEqual(parts[0].assembly_note, "")

    def test_no_op_when_there_are_no_existing_search_roots(self) -> None:
        # Force both candidate roots (rpd folder, asset-root override) to
        # definitely-nonexistent paths - resetting the override to the app
        # default isn't hermetic here, since the real W_RELEASE_ROOT network
        # path may actually be mapped and existing wherever this test runs.
        parts = [_part("1", str(self.root / "F55334-B-1001.sym"))]
        assets.set_asset_root_override(str(self.root / "does_not_exist_override"), persist=False, source="test")
        fake_tne = SimpleNamespace(
            collect_unused_tabloid_pdfs=lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called"))
        )
        with patch.object(ui_actions, "_load_truck_nest_explorer_packet_build_service", return_value=fake_tne):
            ui_actions._scan_and_stamp_assembly_context(
                parts,
                str(self.root / "missing_dir" / "F55334 PAINT PACK.rpd"),
            )
        self.assertEqual(parts[0].assembly_note, "")

    def test_search_roots_include_rpd_folder_and_asset_root_override(self) -> None:
        override_dir = self.root / "override_root"
        override_dir.mkdir()
        assets.set_asset_root_override(str(override_dir), persist=False, source="test")

        parts = [_part("1", str(self.root / "F55334-B-1001.sym"))]
        seen_roots: list[list[str]] = []

        def _fake_collect(parts_arg, *, search_roots, resolve_asset_fn):
            seen_roots.append(sorted(str(r) for r in search_roots))
            return ()

        fake_tne = SimpleNamespace(collect_unused_tabloid_pdfs=_fake_collect)
        with patch.object(ui_actions, "_load_truck_nest_explorer_packet_build_service", return_value=fake_tne):
            ui_actions._scan_and_stamp_assembly_context(parts, str(self.root / "F55334 PAINT PACK.rpd"))

        self.assertEqual(len(seen_roots), 1)
        expected = sorted([str(self.root), str(override_dir)])
        self.assertEqual(seen_roots[0], expected)

    def test_applies_notes_and_writes_sym_comments_when_pdfs_found(self) -> None:
        parts = [_part("1", str(self.root / "F55334-B-1001.sym"))]
        assets.set_asset_root_override(None, persist=False, source="test")

        assembly_context = SimpleNamespace(references=())
        applied_notes_calls = []
        sym_comment_calls = []

        fake_tne = SimpleNamespace(
            collect_unused_tabloid_pdfs=lambda *a, **k: (Path("F55334-BODY.pdf"),),
            scan_assembly_bom_context=lambda **k: assembly_context,
            apply_assembly_notes_to_parts=lambda p, r: applied_notes_calls.append((p, r)),
            apply_assembly_context_to_sym_comments=lambda **k: sym_comment_calls.append(k) or SimpleNamespace(updated_count=0),
        )
        with patch.object(ui_actions, "_load_truck_nest_explorer_packet_build_service", return_value=fake_tne):
            ui_actions._scan_and_stamp_assembly_context(parts, str(self.root / "F55334 PAINT PACK.rpd"))

        self.assertEqual(len(applied_notes_calls), 1)
        self.assertEqual(applied_notes_calls[0], (parts, assembly_context))
        self.assertEqual(len(sym_comment_calls), 1)
        self.assertEqual(sym_comment_calls[0]["parts"], parts)
        self.assertEqual(sym_comment_calls[0]["result"], assembly_context)
        self.assertEqual(sym_comment_calls[0]["backup_dir"], self.root / "_bak" / "assembly_comments")

    def test_a_scan_failure_does_not_raise(self) -> None:
        parts = [_part("1", str(self.root / "F55334-B-1001.sym"))]
        assets.set_asset_root_override(None, persist=False, source="test")
        fake_tne = SimpleNamespace(
            collect_unused_tabloid_pdfs=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        with patch.object(ui_actions, "_load_truck_nest_explorer_packet_build_service", return_value=fake_tne):
            ui_actions._scan_and_stamp_assembly_context(parts, str(self.root / "F55334 PAINT PACK.rpd"))
        self.assertEqual(parts[0].assembly_note, "")


if __name__ == "__main__":
    unittest.main()
