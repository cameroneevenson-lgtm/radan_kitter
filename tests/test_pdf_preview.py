from __future__ import annotations

import unittest

from pdf_preview import _apply_preferred_layers


class _FakeDoc:
    def __init__(self, ui_cfgs) -> None:
        self.ui_cfgs = ui_cfgs
        self.ui_states: dict[int, int] = {}

    def layer_ui_configs(self):
        return list(self.ui_cfgs)

    def set_layer_ui_config(self, num: int, state: int) -> None:
        self.ui_states[int(num)] = int(state)


class PdfPreviewLayerPolicyTests(unittest.TestCase):
    def test_apply_preferred_layers_keeps_only_target_layers_on(self) -> None:
        doc = _FakeDoc(
            [
                {"number": 0, "text": "Visible", "on": 1},
                {"number": 1, "text": "Title", "on": 1},
                {"number": 2, "text": "Symbol", "on": 1},
                {"number": 3, "text": "0 (ANSI)", "on": 1},
            ]
        )
        changed = _apply_preferred_layers(doc)
        self.assertTrue(changed)
        self.assertEqual(doc.ui_states.get(0), 1)
        self.assertEqual(doc.ui_states.get(1), 1)
        # Symbol and layer 0 are not in the target set, so pass 1 turns them off
        # and neither is turned back on in pass 2.
        self.assertEqual(doc.ui_states.get(2), 2)
        self.assertEqual(doc.ui_states.get(3), 2)

    def test_apply_preferred_layers_falls_back_to_hiding_known_non_preview_layers(self) -> None:
        doc = _FakeDoc(
            [
                {"number": 0, "text": "Custom Layer", "on": 1},
                {"number": 1, "text": "Symbol", "on": 1},
                {"number": 2, "text": "0", "on": 1},
            ]
        )
        changed = _apply_preferred_layers(doc)
        self.assertTrue(changed)
        # No target layer names were found, so the fallback only forces off
        # known non-preview layers (symbol/border/hidden/"0") and leaves others alone.
        self.assertNotIn(0, doc.ui_states)
        self.assertEqual(doc.ui_states.get(1), 2)
        self.assertEqual(doc.ui_states.get(2), 2)


if __name__ == "__main__":
    unittest.main()
