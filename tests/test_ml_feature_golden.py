from __future__ import annotations

import json
import os
import unittest

import pandas as pd

import ml_pipeline
from test_support import fixture_path, workspace_temp_dir


class MlFeatureGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pdf_path = fixture_path("layered_sample.pdf")
        cls.dxf_path = fixture_path("profile_sample.dxf")
        with open(fixture_path("golden_features.json"), "r", encoding="utf-8") as handle:
            cls.expected = json.load(handle)

    def assertGoldenSignals(self, actual: dict) -> None:
        for key, expected in self.expected.items():
            self.assertIn(key, actual)
            self.assertAlmostEqual(float(actual[key]), float(expected), places=9, msg=key)

    def test_compute_phase2_signals_matches_golden_fixture(self) -> None:
        actual = ml_pipeline.compute_phase2_signals(self.pdf_path, self.dxf_path)
        self.assertGoldenSignals(actual)

    def test_recompute_dataset_signals_updates_fixture_row(self) -> None:
        with workspace_temp_dir("ml_golden") as tmp:
            dataset_path = os.path.join(tmp, "dataset.csv")
            row = {column: "" for column in ml_pipeline.ALL_COLS}
            row.update(
                {
                    "timestamp_utc": "2026-04-08T00:00:00+00:00",
                    "rpd_token": "fixture.rpd",
                    "part_name": "FIXTURE",
                    "kit_label": "KIT-A",
                    "pdf_path": self.pdf_path,
                    "dxf_path": self.dxf_path,
                }
            )
            pd.DataFrame([row], columns=ml_pipeline.ALL_COLS).to_csv(dataset_path, index=False)

            summary = ml_pipeline.recompute_dataset_signals(dataset_path=dataset_path, max_workers=1)
            self.assertEqual(summary["total_rows"], 1)
            self.assertEqual(summary["processed_rows"], 1)
            self.assertEqual(summary["updated_rows"], 1)
            self.assertEqual(summary["error_rows"], 0)
            self.assertEqual(summary["missing_pdf_rows"], 0)
            self.assertEqual(summary["missing_dxf_rows"], 0)

            df = pd.read_csv(dataset_path)
            self.assertEqual(len(df), 1)
            self.assertGoldenSignals(df.iloc[0].to_dict())

    def test_recompute_dataset_signals_reports_partial_feature_errors(self) -> None:
        with workspace_temp_dir("ml_bad_dxf") as tmp:
            dataset_path = os.path.join(tmp, "dataset.csv")
            bad_dxf_path = os.path.join(tmp, "bad_profile.dxf")
            with open(bad_dxf_path, "w", encoding="utf-8") as handle:
                handle.write("not a dxf")

            row = {column: "" for column in ml_pipeline.ALL_COLS}
            row.update(
                {
                    "timestamp_utc": "2026-04-08T00:00:00+00:00",
                    "rpd_token": "fixture.rpd",
                    "part_name": "BAD-FIXTURE",
                    "kit_label": "KIT-A",
                    "pdf_path": self.pdf_path,
                    "dxf_path": bad_dxf_path,
                }
            )
            pd.DataFrame([row], columns=ml_pipeline.ALL_COLS).to_csv(dataset_path, index=False)

            summary = ml_pipeline.recompute_dataset_signals(dataset_path=dataset_path, max_workers=1)
            self.assertEqual(summary["total_rows"], 1)
            self.assertEqual(summary["processed_rows"], 1)
            self.assertEqual(summary["updated_rows"], 1)
            self.assertEqual(summary["error_rows"], 1)
            self.assertEqual(summary["feature_error_rows"], 1)
            self.assertEqual(summary["dxf_feature_error_rows"], 1)
            self.assertEqual(summary["pdf_feature_error_rows"], 0)

            df = pd.read_csv(dataset_path)
            self.assertEqual(len(df), 1)
            self.assertTrue(pd.isna(df.at[0, "dxf_entity_count"]))
            self.assertAlmostEqual(float(df.at[0, "pdf_dim_density"]), 0.1, places=9)


if __name__ == "__main__":
    unittest.main()
