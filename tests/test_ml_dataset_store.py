from __future__ import annotations

import os
import unittest

import pandas as pd

from ml_dataset_store import (
    append_labeled_row,
    ensure_dataset_exists,
    load_dataset_df,
    make_run_name,
    part_keys_from_df,
)
from test_support import workspace_temp_dir


class MlDatasetStoreTests(unittest.TestCase):
    def test_append_labeled_row_upserts_by_part_name(self) -> None:
        with workspace_temp_dir("ml_dataset_append") as tmpdir:
            dataset_path = os.path.join(tmpdir, "ml_dataset.csv")
            all_cols = [
                "timestamp_utc",
                "rpd_token",
                "part_name",
                "kit_label",
                "pdf_path",
                "dxf_path",
                "sig_a",
            ]

            def fake_compute(pdf_path: str, dxf_path: str) -> dict[str, float]:
                return {"sig_a": float(len(pdf_path) + len(dxf_path))}

            append_labeled_row(
                "PART-001",
                "Sides",
                "a.pdf",
                "a.dxf",
                dataset_path=dataset_path,
                all_cols=all_cols,
                compute_signals_fn=fake_compute,
                nan_fn=lambda: float("nan"),
                utc_now_iso_fn=lambda: "2026-04-08T00:00:00+00:00",
                rpd_token="run-a",
            )
            append_labeled_row(
                "PART-001",
                "Tops",
                "b.pdf",
                "b.dxf",
                dataset_path=dataset_path,
                all_cols=all_cols,
                compute_signals_fn=fake_compute,
                nan_fn=lambda: float("nan"),
                utc_now_iso_fn=lambda: "2026-04-08T00:05:00+00:00",
                rpd_token="run-b",
            )

            df = pd.read_csv(dataset_path)
            self.assertEqual(len(df), 1)
            self.assertEqual(df.iloc[0]["kit_label"], "Tops")
            self.assertEqual(df.iloc[0]["rpd_token"], "run-b")

    def test_load_dataset_df_backfills_missing_columns(self) -> None:
        with workspace_temp_dir("ml_dataset_load") as tmpdir:
            dataset_path = os.path.join(tmpdir, "ml_dataset.csv")
            pd.DataFrame([{"part_name": "PART-001"}]).to_csv(dataset_path, index=False)
            df = load_dataset_df(
                dataset_path,
                ["part_name", "kit_label", "sig_a"],
                lambda: float("nan"),
            )
            self.assertEqual(list(df.columns), ["part_name", "kit_label", "sig_a"])
            self.assertEqual(df.iloc[0]["part_name"], "PART-001")

    def test_part_keys_from_df_normalizes_case(self) -> None:
        df = pd.DataFrame([{"part_name": "Part-001"}, {"part_name": "part-002"}])
        self.assertEqual(part_keys_from_df(df), {"PART-001", "PART-002"})

    def test_make_run_name_sanitizes_job_name(self) -> None:
        run_name = make_run_name(
            r"C:\jobs\F123 Paint Pack.rpd",
            stamp_fn=lambda: "20260408_120000",
        )
        self.assertEqual(run_name, "MLRun_F123_Paint_Pack_20260408_120000")

    def test_ensure_dataset_exists_creates_empty_csv(self) -> None:
        with workspace_temp_dir("ml_dataset_exists") as tmpdir:
            dataset_path = os.path.join(tmpdir, "ml_dataset.csv")
            ensure_dataset_exists(dataset_path, ["part_name", "kit_label"])
            self.assertTrue(os.path.exists(dataset_path))


if __name__ == "__main__":
    unittest.main()
