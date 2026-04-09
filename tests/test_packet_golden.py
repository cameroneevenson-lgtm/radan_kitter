from __future__ import annotations

import os
import unittest

import fitz

from pdf_packet import PacketBuildEmpty, build_watermarked_packet
import packet_service
from rpd_io import PartRow
from test_support import fixture_path, workspace_temp_dir


class PacketGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source_pdf = fixture_path("layered_sample.pdf")
        cls.layer_zero_pdf = fixture_path("layer_zero_sample.pdf")

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

    def test_vector_packet_suppresses_layer_zero_content(self) -> None:
        part = PartRow(
            pid="1",
            sym="layer-zero.sym",
            kit_text="",
            priority="1",
            qty=1,
            material="",
            thickness="",
            extra=0,
        )
        with workspace_temp_dir("packet_layer_zero") as tmp:
            out_pdf = os.path.join(tmp, "layer_zero_packet.pdf")
            pages, missing = build_watermarked_packet(
                [part],
                out_pdf,
                resolve_asset_fn=lambda _sym, ext: self.layer_zero_pdf if ext == ".pdf" else None,
                max_workers=1,
                render_mode="vector",
            )
            self.assertEqual((pages, missing), (1, 0))
            with fitz.open(out_pdf) as doc:
                text = doc[0].get_text("text")
            self.assertIn("VISIBLE BODY", text)
            self.assertNotIn("ZERO HIDDEN TEXT", text)

    def test_packet_counts_mixed_present_and_missing_assets(self) -> None:
        parts = [
            PartRow(pid="1", sym="good.sym", kit_text="", priority="1", qty=1, material="", thickness="", extra=0),
            PartRow(pid="2", sym="missing.sym", kit_text="", priority="1", qty=1, material="", thickness="", extra=0),
        ]
        with workspace_temp_dir("packet_mixed_assets") as tmp:
            out_pdf = os.path.join(tmp, "mixed_packet.pdf")

            def resolver(sym: str, ext: str) -> str | None:
                if ext != ".pdf":
                    return None
                if os.path.basename(sym).lower() == "good.sym":
                    return self.source_pdf
                return None

            pages, missing = build_watermarked_packet(
                parts,
                out_pdf,
                resolve_asset_fn=resolver,
                max_workers=1,
                render_mode="vector",
            )

            self.assertEqual((pages, missing), (1, 1))
            with fitz.open(out_pdf) as doc:
                self.assertEqual(doc.page_count, 1)

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

    def test_packet_service_build_packet_preserves_windows_order_end_to_end(self) -> None:
        fixture_map = {
            "part1.sym": fixture_path("order_1.pdf"),
            "part2.sym": fixture_path("order_2.pdf"),
            "part10.sym": fixture_path("order_10.pdf"),
        }
        parts = [
            PartRow(pid="10", sym=r"C:\parts\part10.sym", kit_text="", priority="1", qty=1, material="", thickness="", extra=0),
            PartRow(pid="2", sym=r"C:\parts\part2.sym", kit_text="", priority="1", qty=1, material="", thickness="", extra=0),
            PartRow(pid="1", sym=r"C:\parts\part1.sym", kit_text="", priority="1", material="", thickness="", qty=1, extra=0),
        ]
        with workspace_temp_dir("packet_order_e2e") as tmp:
            rpd_dir = os.path.join(tmp, "job")
            os.makedirs(rpd_dir, exist_ok=True)
            rpd_path = os.path.join(rpd_dir, "job.rpd")
            with open(rpd_path, "w", encoding="utf-8") as handle:
                handle.write("fixture")

            packet_path, pages, missing = packet_service.build_packet(
                parts,
                rpd_path=rpd_path,
                out_dirname="_out",
                resolve_asset_fn=lambda sym, ext: fixture_map.get(os.path.basename(sym).lower()) if ext == ".pdf" else None,
                max_workers=1,
                render_mode="vector",
            )

            self.assertEqual((pages, missing), (3, 0))
            with fitz.open(packet_path) as doc:
                page_texts = [doc[index].get_text("text") for index in range(doc.page_count)]

            self.assertIn("ORDER 1", page_texts[0])
            self.assertIn("ORDER 2", page_texts[1])
            self.assertIn("ORDER 10", page_texts[2])


if __name__ == "__main__":
    unittest.main()
