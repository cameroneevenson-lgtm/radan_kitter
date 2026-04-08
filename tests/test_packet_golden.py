from __future__ import annotations

import os
import unittest

import fitz

from pdf_packet import PacketBuildEmpty, build_watermarked_packet
from rpd_io import PartRow
from test_support import fixture_path, workspace_temp_dir


class PacketGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source_pdf = fixture_path("layered_sample.pdf")

    def _resolve_asset(self, _sym: str, ext: str) -> str | None:
        if ext == ".pdf":
            return self.source_pdf
        return None

    def _build_packet_text(self, render_mode: str) -> str:
        part = PartRow(
            pid="1",
            sym="fixture.sym",
            kit_text="",
            priority="1",
            qty=2,
            material="",
            thickness="",
            extra=0,
        )
        with workspace_temp_dir(f"packet_{render_mode}") as tmp:
            out_pdf = os.path.join(tmp, f"{render_mode}_packet.pdf")
            pages, missing = build_watermarked_packet(
                [part],
                out_pdf,
                resolve_asset_fn=self._resolve_asset,
                max_workers=1,
                render_mode=render_mode,
            )
            self.assertEqual((pages, missing), (1, 0))
            with fitz.open(out_pdf) as doc:
                self.assertEqual(doc.page_count, 1)
                return doc[0].get_text("text")

    def test_vector_packet_preserves_source_text_and_adds_watermark(self) -> None:
        text = self._build_packet_text("vector")
        self.assertIn("12.50", text)
        self.assertIn("TITLE", text)
        self.assertIn("QTY 2", text)

    def test_raster_packet_adds_watermark(self) -> None:
        text = self._build_packet_text("raster")
        self.assertIn("QTY 2", text)

    def test_packet_empty_reports_last_failure_reason(self) -> None:
        with workspace_temp_dir("packet_bad_pdf") as tmp:
            bad_pdf = os.path.join(tmp, "bad.pdf")
            with open(bad_pdf, "w", encoding="utf-8") as handle:
                handle.write("not a real pdf")

            part = PartRow(
                pid="1",
                sym="fixture.sym",
                kit_text="",
                priority="1",
                qty=1,
                material="",
                thickness="",
                extra=0,
            )

            with self.assertRaises(PacketBuildEmpty) as ctx:
                build_watermarked_packet(
                    [part],
                    os.path.join(tmp, "out.pdf"),
                    resolve_asset_fn=lambda _sym, ext: bad_pdf if ext == ".pdf" else None,
                    max_workers=1,
                    render_mode="vector",
                )

            exc = ctx.exception
            self.assertEqual(exc.pages, 0)
            self.assertEqual(exc.missing, 1)
            self.assertIn("Last failure:", str(exc))
            self.assertIn("open failed", str(exc))


if __name__ == "__main__":
    unittest.main()
