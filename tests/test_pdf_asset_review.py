from __future__ import annotations

import os
import unittest

import fitz

import pdf_asset_review
from rpd_io import PartRow
from test_support import workspace_temp_dir


def _write_pdf(path: str, text: str) -> None:
    doc = fitz.open()
    try:
        page = doc.new_page(width=792, height=612)
        page.insert_text((72, 72), text)
        doc.save(path)
    finally:
        doc.close()


class PdfAssetReviewTests(unittest.TestCase):
    def _part(self, name: str) -> PartRow:
        return PartRow(
            pid=name,
            sym=f"{name}.sym",
            kit_text="",
            priority="1",
            qty=1,
            material="",
            thickness="",
            extra=0,
        )

    def test_scan_warns_when_pdf_title_references_another_project_part(self) -> None:
        with workspace_temp_dir("pdf_asset_review_mismatch") as tmp:
            bad_pdf = os.path.join(tmp, "F58561-B-54.pdf")
            _write_pdf(bad_pdf, "Title\nF58561-B-57\n")
            parts = [self._part("F58561-B-54"), self._part("F58561-B-57")]

            result = pdf_asset_review.scan_pdf_asset_titles(
                parts,
                resolve_asset_fn=lambda sym, ext: bad_pdf if sym == "F58561-B-54.sym" and ext == ".pdf" else None,
            )

            self.assertEqual(len(result.warnings), 1)
            warning = result.warnings[0]
            self.assertEqual(warning.part_name, "F58561-B-54")
            self.assertEqual(warning.found_part_name, "F58561-B-57")
            self.assertEqual(warning.scope, "PDF Title text")

    def test_scan_allows_matching_pdf_title(self) -> None:
        with workspace_temp_dir("pdf_asset_review_match") as tmp:
            good_pdf = os.path.join(tmp, "F58561-B-54.pdf")
            _write_pdf(good_pdf, "Title\nF58561-B-54\n")
            parts = [self._part("F58561-B-54"), self._part("F58561-B-57")]

            result = pdf_asset_review.scan_pdf_asset_titles(
                parts,
                resolve_asset_fn=lambda sym, ext: good_pdf if sym == "F58561-B-54.sym" and ext == ".pdf" else None,
            )

            self.assertEqual(result.warnings, ())


if __name__ == "__main__":
    unittest.main()
