from __future__ import annotations

import unittest

import rf_service


class RfServiceTests(unittest.TestCase):
    def test_single_label_collapse_detects_large_uniform_prediction_batch(self) -> None:
        preds = [("Walls", 0.82) for _ in range(rf_service.SINGLE_LABEL_COLLAPSE_MIN_ROWS)]

        self.assertEqual(
            rf_service._single_label_collapse_source(preds),
            f"single_label_collapse:Walls:{rf_service.SINGLE_LABEL_COLLAPSE_MIN_ROWS}",
        )

    def test_single_label_collapse_allows_small_or_mixed_batches(self) -> None:
        small = [("Walls", 0.82) for _ in range(rf_service.SINGLE_LABEL_COLLAPSE_MIN_ROWS - 1)]
        mixed = [("Walls", 0.82), ("Tops", 0.61)] * rf_service.SINGLE_LABEL_COLLAPSE_MIN_ROWS

        self.assertEqual(rf_service._single_label_collapse_source(small), "")
        self.assertEqual(rf_service._single_label_collapse_source(mixed), "")


if __name__ == "__main__":
    unittest.main()
