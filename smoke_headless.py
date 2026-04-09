from __future__ import annotations

import argparse
import cProfile
import io
import json
import math
import os
import pstats
import shutil
import time
import uuid

import fitz
import pandas as pd

import ml_pipeline
import packet_service
from config import GLOBAL_RUNTIME_DIR
from pdf_packet import build_watermarked_packet
from rpd_io import PartRow


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
FIXTURE_DIR = os.path.join(REPO_ROOT, "tests", "fixtures")


def _fixture_path(name: str) -> str:
    return os.path.join(FIXTURE_DIR, name)


def _default_profile_dir() -> str:
    return os.path.join(GLOBAL_RUNTIME_DIR, "profiles")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the radan_kitter headless smoke test.")
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Write a cProfile dump and text summary under _runtime/profiles.",
    )
    parser.add_argument(
        "--profile-dir",
        default="",
        help="Override where deep profile outputs are written.",
    )
    parser.add_argument(
        "--profile-sort",
        default="cumulative",
        help="pstats sort key for the text summary.",
    )
    parser.add_argument(
        "--profile-limit",
        type=int,
        default=40,
        help="How many functions to include in the text summary.",
    )
    return parser


def _profile_output_paths(profile_dir: str) -> tuple[str, str]:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = os.path.join(profile_dir, f"smoke_headless_{stamp}")
    return base + ".prof", base + "_stats.txt"


def run_smoke() -> dict:
    tmpdir = os.path.join(REPO_ROOT, f"_smoke_{uuid.uuid4().hex}")
    os.makedirs(tmpdir, exist_ok=False)
    try:
        fixture_map = {
            "part1.sym": _fixture_path("order_1.pdf"),
            "part2.sym": _fixture_path("order_2.pdf"),
            "part10.sym": _fixture_path("order_10.pdf"),
        }
        parts = [
            PartRow(pid="10", sym=r"C:\parts\part10.sym", kit_text="", priority="1", qty=1, material="", thickness="", extra=0),
            PartRow(pid="2", sym=r"C:\parts\part2.sym", kit_text="", priority="1", qty=1, material="", thickness="", extra=0),
            PartRow(pid="1", sym=r"C:\parts\part1.sym", kit_text="", priority="1", qty=1, material="", thickness="", extra=0),
        ]

        rpd_dir = os.path.join(tmpdir, "job")
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
        assert (pages, missing) == (3, 0)
        with fitz.open(packet_path) as doc:
            page_texts = [doc[index].get_text("text") for index in range(doc.page_count)]
        assert "ORDER 1" in page_texts[0]
        assert "ORDER 2" in page_texts[1]
        assert "ORDER 10" in page_texts[2]

        layer_zero_out = os.path.join(tmpdir, "layer_zero.pdf")
        layer_part = PartRow(pid="lz", sym="layer-zero.sym", kit_text="", priority="1", qty=1, material="", thickness="", extra=0)
        build_watermarked_packet(
            [layer_part],
            layer_zero_out,
            resolve_asset_fn=lambda _sym, ext: _fixture_path("layer_zero_sample.pdf") if ext == ".pdf" else None,
            max_workers=1,
            render_mode="vector",
        )
        with fitz.open(layer_zero_out) as doc:
            layer_zero_text = doc[0].get_text("text")
        assert "VISIBLE BODY" in layer_zero_text
        assert "ZERO HIDDEN TEXT" not in layer_zero_text

        feats = ml_pipeline.compute_phase2_signals(
            _fixture_path("layered_sample.pdf"),
            _fixture_path("profile_sample.dxf"),
        )
        assert math.isfinite(float(feats["dxf_entity_count"]))
        assert math.isfinite(float(feats["pdf_dim_density"]))

        dataset_path = os.path.join(tmpdir, "dataset.csv")
        row = {column: "" for column in ml_pipeline.ALL_COLS}
        row.update(
            {
                "timestamp_utc": "2026-04-08T00:00:00+00:00",
                "rpd_token": "fixture.rpd",
                "part_name": "SMOKE",
                "kit_label": "KIT-A",
                "pdf_path": _fixture_path("layered_sample.pdf"),
                "dxf_path": _fixture_path("profile_sample.dxf"),
            }
        )
        pd.DataFrame([row], columns=ml_pipeline.ALL_COLS).to_csv(dataset_path, index=False)
        summary = ml_pipeline.recompute_dataset_signals(dataset_path=dataset_path, max_workers=1)
        assert summary["updated_rows"] == 1
        assert summary["error_rows"] == 0

        return {
            "packet_pages": pages,
            "packet_missing": missing,
            "ordered_pages": [text.strip().splitlines()[0] for text in page_texts],
            "layer_zero_ok": True,
            "ml_updated_rows": int(summary["updated_rows"]),
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _run_with_profile(args: argparse.Namespace) -> dict:
    profile_dir = os.path.normpath(str(args.profile_dir or "").strip() or _default_profile_dir())
    os.makedirs(profile_dir, exist_ok=True)
    prof_path, stats_path = _profile_output_paths(profile_dir)
    profiler = cProfile.Profile()
    t0 = time.perf_counter()
    result = profiler.runcall(run_smoke)
    elapsed_ms = int((time.perf_counter() - t0) * 1000.0)
    profiler.dump_stats(prof_path)

    sort_key = str(args.profile_sort or "cumulative").strip() or "cumulative"
    limit = max(1, int(args.profile_limit or 40))
    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream).strip_dirs()
    try:
        stats.sort_stats(sort_key)
    except Exception:
        sort_key = "cumulative"
        stats = pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats(sort_key)
    stats.print_stats(limit)
    with open(stats_path, "w", encoding="utf-8") as handle:
        handle.write(stream.getvalue())

    payload = dict(result)
    payload.update(
        {
            "profile_elapsed_ms": elapsed_ms,
            "profile_prof_path": prof_path,
            "profile_stats_path": stats_path,
            "profile_sort": sort_key,
            "profile_limit": limit,
        }
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    payload = _run_with_profile(args) if args.profile else run_smoke()
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
