from __future__ import annotations

import os
import shutil
import unittest
from unittest.mock import patch

import kit_service
from rpd_io import PartRow
import sym_io
from test_support import workspace_temp_dir


def _write_minimal_donor(path: str) -> None:
    placeholder = r"C:\Donor\PLACEHOLDER.sym"
    donor_text = (
        '<Symbol name="PLACEHOLDER" count="1">\n'
        '<Info num="0" name="Number of Loops" value="1">\n'
        "U,,,,2\n"
        "F,example$/PLACEHOLDER\n"
        "C,$\n"
        f"U,,{placeholder}$\n"
        "U,$\n"
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(donor_text)


def _make_part(sym_path: str, kit_label: str = "Walls") -> PartRow:
    part = PartRow(
        pid="1",
        sym=sym_path,
        kit_text="",
        priority="9",
        qty=1,
        material="",
        thickness="",
    )
    part.kit_label = kit_label
    return part


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

    def test_prepare_kits_defaults_to_writing_part_comments(self) -> None:
        with workspace_temp_dir("kit_prepare_default_comments") as tmpdir:
            donor_path = os.path.join(tmpdir, "Donor.sym")
            _write_minimal_donor(donor_path)

            part_sym_path = os.path.join(tmpdir, "Part A.sym")
            with open(part_sym_path, "w", encoding="utf-8") as handle:
                handle.write("part source")
            part = _make_part(part_sym_path, "Walls")

            comment_calls: list[tuple[str, str]] = []

            def set_part_comment(sym_path: str, comment: str) -> bool:
                comment_calls.append((sym_path, comment))
                return True

            with patch("kit_service.sym_io.set_part_comment", side_effect=set_part_comment):
                count = kit_service.prepare_kits(
                    [part],
                    rpd_path=os.path.join(tmpdir, "Demo.rpd"),
                    donor_template_path=donor_path,
                    bak_dirname="_bak",
                    kits_dirname="_kits",
                    kit_to_priority={"Walls": "8"},
                )

            self.assertEqual(count, 1)
            self.assertEqual(comment_calls, [(part_sym_path, "Walls")])

    def test_prepare_kits_true_writes_part_comments_and_part_backups(self) -> None:
        with workspace_temp_dir("kit_prepare_true_comments") as tmpdir:
            donor_path = os.path.join(tmpdir, "Donor.sym")
            _write_minimal_donor(donor_path)

            part_sym_path = os.path.join(tmpdir, "Part A.sym")
            with open(part_sym_path, "w", encoding="utf-8") as handle:
                handle.write("part source")
            part = _make_part(part_sym_path, "Walls")

            comment_calls: list[tuple[str, str]] = []

            def set_part_comment(sym_path: str, comment: str) -> bool:
                comment_calls.append((sym_path, comment))
                return True

            with patch("kit_service.sym_io.set_part_comment", side_effect=set_part_comment):
                count = kit_service.prepare_kits(
                    [part],
                    rpd_path=os.path.join(tmpdir, "Demo.rpd"),
                    donor_template_path=donor_path,
                    bak_dirname="_bak",
                    kits_dirname="_kits",
                    kit_to_priority={"Walls": "8"},
                    write_part_kit_comments=True,
                )

            parts_backup_dir = os.path.join(tmpdir, "_bak", "parts")
            self.assertEqual(count, 1)
            self.assertEqual(comment_calls, [(part_sym_path, "Walls")])
            self.assertTrue(os.path.isdir(parts_backup_dir))
            self.assertEqual(len(os.listdir(parts_backup_dir)), 1)
            self.assertTrue(os.listdir(parts_backup_dir)[0].startswith("Part A.sym."))

    def test_prepare_kits_false_skips_part_comments_and_comment_backups(self) -> None:
        with workspace_temp_dir("kit_prepare_false_comments") as tmpdir:
            donor_path = os.path.join(tmpdir, "Donor.sym")
            _write_minimal_donor(donor_path)

            part_sym_path = os.path.join(tmpdir, "Part A.sym")
            with open(part_sym_path, "w", encoding="utf-8") as handle:
                handle.write("part source")
            part = _make_part(part_sym_path, "Walls")
            refresh_calls: list[str] = []

            with patch(
                "kit_service.sym_io.set_part_comment",
                side_effect=AssertionError("part comments should not be written"),
            ):
                count = kit_service.prepare_kits(
                    [part],
                    rpd_path=os.path.join(tmpdir, "Demo.rpd"),
                    donor_template_path=donor_path,
                    bak_dirname="_bak",
                    kits_dirname="_kits",
                    kit_to_priority={"Walls": "8"},
                    refresh_kit_fn=refresh_calls.append,
                    write_part_kit_comments=False,
                )

            expected_kit_path = os.path.join(tmpdir, "_kits", "Walls.sym")
            self.assertEqual(count, 1)
            self.assertEqual(part.kit_label, "Walls")
            self.assertEqual(part.priority, "8")
            self.assertEqual(part.kit_text, expected_kit_path)
            self.assertTrue(os.path.exists(expected_kit_path))
            self.assertEqual(refresh_calls, [expected_kit_path])
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "_bak", "parts")))

    def test_prepare_kits_donor_validation_runs_in_both_comment_modes(self) -> None:
        for write_part_kit_comments in (True, False):
            with self.subTest(write_part_kit_comments=write_part_kit_comments):
                with workspace_temp_dir("kit_prepare_missing_donor") as tmpdir:
                    part_sym_path = os.path.join(tmpdir, "Part A.sym")
                    with open(part_sym_path, "w", encoding="utf-8") as handle:
                        handle.write("part source")
                    part = _make_part(part_sym_path, "Walls")

                    with self.assertRaisesRegex(RuntimeError, "Donor not found"):
                        kit_service.prepare_kits(
                            [part],
                            rpd_path=os.path.join(tmpdir, "Demo.rpd"),
                            donor_template_path=os.path.join(tmpdir, "Missing.sym"),
                            bak_dirname="_bak",
                            kits_dirname="_kits",
                            kit_to_priority={"Walls": "8"},
                            write_part_kit_comments=write_part_kit_comments,
                        )

    def test_prepare_kits_refreshes_generated_kits_in_both_comment_modes(self) -> None:
        for write_part_kit_comments in (True, False):
            with self.subTest(write_part_kit_comments=write_part_kit_comments):
                with workspace_temp_dir("kit_prepare_refresh_modes") as tmpdir:
                    donor_path = os.path.join(tmpdir, "Donor.sym")
                    _write_minimal_donor(donor_path)

                    part_sym_path = os.path.join(tmpdir, "Part A.sym")
                    with open(part_sym_path, "w", encoding="utf-8") as handle:
                        handle.write("part source")
                    part = _make_part(part_sym_path, "Walls")
                    refresh_calls: list[str] = []

                    with patch("kit_service.sym_io.set_part_comment", return_value=True):
                        count = kit_service.prepare_kits(
                            [part],
                            rpd_path=os.path.join(tmpdir, "Demo.rpd"),
                            donor_template_path=donor_path,
                            bak_dirname="_bak",
                            kits_dirname="_kits",
                            kit_to_priority={"Walls": "8"},
                            refresh_kit_fn=refresh_calls.append,
                            write_part_kit_comments=write_part_kit_comments,
                        )

                    expected_kit_path = os.path.join(tmpdir, "_kits", "Walls.sym")
                    self.assertEqual(count, 1)
                    self.assertEqual(refresh_calls, [expected_kit_path])
                    self.assertTrue(os.path.exists(expected_kit_path))

    def test_prepare_kits_progress_is_coherent_in_both_comment_modes(self) -> None:
        for write_part_kit_comments in (True, False):
            with self.subTest(write_part_kit_comments=write_part_kit_comments):
                with workspace_temp_dir("kit_prepare_progress_modes") as tmpdir:
                    donor_path = os.path.join(tmpdir, "Donor.sym")
                    _write_minimal_donor(donor_path)

                    part_sym_path = os.path.join(tmpdir, "Part A.sym")
                    with open(part_sym_path, "w", encoding="utf-8") as handle:
                        handle.write("part source")
                    part = _make_part(part_sym_path, "Walls")
                    progress: list[tuple[int, int, str]] = []

                    with patch("kit_service.sym_io.set_part_comment", return_value=True):
                        count = kit_service.prepare_kits(
                            [part],
                            rpd_path=os.path.join(tmpdir, "Demo.rpd"),
                            donor_template_path=donor_path,
                            bak_dirname="_bak",
                            kits_dirname="_kits",
                            kit_to_priority={"Walls": "8"},
                            progress_cb=lambda done, total, status: progress.append((done, total, status)),
                            write_part_kit_comments=write_part_kit_comments,
                        )

                    self.assertEqual(count, 1)
                    self.assertTrue(progress)
                    totals = {total for _done, total, _status in progress}
                    self.assertEqual(len(totals), 1)
                    expected_total = 2 if write_part_kit_comments else 1
                    self.assertEqual(totals, {expected_total})
                    self.assertEqual(progress[0][0], 0)
                    self.assertEqual(progress[-1][0], expected_total)
                    self.assertEqual(progress[-1][1], expected_total)
                    self.assertEqual(
                        [done for done, _total, _status in progress],
                        sorted(done for done, _total, _status in progress),
                    )

    def test_build_kit_sym_from_donor_allows_digit_starting_part_names(self) -> None:
        with workspace_temp_dir("kit_sym_digit_names") as tmpdir:
            donor_path = os.path.join(tmpdir, "Donor.sym")
            out_path = os.path.join(tmpdir, "Kit.sym")
            member_path = os.path.join(tmpdir, "18_SHORT.sym")
            placeholder = r"C:\Donor\PLACEHOLDER.sym"
            donor_text = (
                '<Symbol name="PLACEHOLDER" count="1">\n'
                '<Info num="0" name="Number of Loops" value="1">\n'
                "U,,,,2\n"
                "F,example$/PLACEHOLDER\n"
                "C,$\n"
                f"U,,{placeholder}$\n"
                "U,$\n"
            )
            with open(donor_path, "w", encoding="utf-8") as handle:
                handle.write(donor_text)

            sym_io.build_kit_sym_from_donor(donor_path, [member_path], out_path)

            generated = open(out_path, encoding="utf-8").read()
            self.assertIn("$/18_SHORT", generated)
            self.assertIn('Symbol name="18_SHORT" count="1"', generated)
            self.assertIn(member_path, generated)


if __name__ == "__main__":
    unittest.main()
