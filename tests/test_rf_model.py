from __future__ import annotations

import json
import os
import tempfile
import unittest

import rf_model


class RfModelTests(unittest.TestCase):
    def test_model_meta_matches_current_dataset_and_features(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            meta_path = os.path.join(tmpdir, "rf.meta.json")
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump({"dataset_mtime": 123.5, "requested_features": ["a", "b"]}, f)

            self.assertTrue(
                rf_model._model_meta_matches_request(
                    meta_path,
                    dataset_mtime=123.5,
                    wanted_features=["a", "b"],
                )
            )
            self.assertFalse(
                rf_model._model_meta_matches_request(
                    meta_path,
                    dataset_mtime=124.5,
                    wanted_features=["a", "b"],
                )
            )
            self.assertFalse(
                rf_model._model_meta_matches_request(
                    meta_path,
                    dataset_mtime=123.5,
                    wanted_features=["a", "c"],
                )
            )


if __name__ == "__main__":
    unittest.main()
