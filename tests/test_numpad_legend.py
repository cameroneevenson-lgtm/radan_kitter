from __future__ import annotations

import unittest

from ui_numpad_legend import NumpadLegendWidget


class NumpadLegendTests(unittest.TestCase):
    def test_format_kit_label_wraps_words(self) -> None:
        self.assertEqual(
            NumpadLegendWidget._format_kit_label("Wheel Wells"),
            "Wheel\nWells",
        )

    def test_format_kit_label_preserves_single_word(self) -> None:
        self.assertEqual(
            NumpadLegendWidget._format_kit_label("Bottoms"),
            "Bottoms",
        )


if __name__ == "__main__":
    unittest.main()
