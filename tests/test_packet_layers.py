from __future__ import annotations

import types
import unittest

from packet_layers import (
    apply_packet_layer_policy,
    collect_layer_zero_masks,
    first_toggle_layer_aliases,
    is_layer_zero_name,
    matches_zero_layer_alias,
)


class _FakeDoc:
    def __init__(self) -> None:
        self.ui_cfgs = [
            {"number": 0, "text": "0 (ANSI)", "xref": 11},
            {"number": 1, "text": "Visible", "xref": 22},
            {"number": 2, "text": "Hidden", "xref": 33},
        ]
        self.ocgs = {
            11: {"name": "0 (ANSI)"},
            22: {"name": "Visible"},
            33: {"name": "Hidden"},
        }
        self.ui_states: dict[int, int] = {}
        self.layer_state = {"on": [11, 22, 33], "off": []}
        self.layer_calls = []

    def layer_ui_configs(self):
        return list(self.ui_cfgs)

    def set_layer_ui_config(self, num: int, state: int) -> None:
        self.ui_states[int(num)] = int(state)

    def get_ocgs(self):
        return dict(self.ocgs)

    def get_layer(self, _idx: int):
        return dict(self.layer_state)

    def set_layer(self, *_args, **kwargs) -> None:
        self.layer_calls.append(kwargs)


class PacketLayersTests(unittest.TestCase):
    def test_layer_zero_matching_handles_common_variants(self) -> None:
        self.assertTrue(is_layer_zero_name("0"))
        self.assertTrue(is_layer_zero_name("0 (ANSI)"))
        self.assertTrue(matches_zero_layer_alias("layer 0", ["0ansi"]))

    def test_first_toggle_layer_aliases_returns_first_normalized_layer(self) -> None:
        aliases = first_toggle_layer_aliases(_FakeDoc())
        self.assertEqual(aliases, ["0ansi"])

    def test_apply_packet_layer_policy_turns_off_zero_and_hidden_layers(self) -> None:
        doc = _FakeDoc()
        changed = apply_packet_layer_policy(doc)
        self.assertTrue(changed)
        self.assertEqual(doc.ui_states[0], 2)
        self.assertEqual(doc.ui_states[1], 1)
        self.assertEqual(doc.ui_states[2], 2)
        self.assertTrue(doc.layer_calls)
        last_call = doc.layer_calls[-1]
        self.assertIn(22, last_call.get("on", []))
        self.assertIn(11, last_call.get("off", []))
        self.assertIn(33, last_call.get("off", []))

    def test_collect_layer_zero_masks_filters_to_layer_zero_content(self) -> None:
        page = types.SimpleNamespace(
            rect=types.SimpleNamespace(width=12.0, height=8.0),
            get_drawings=lambda: [
                {
                    "layer": "0 (ANSI)",
                    "rect": types.SimpleNamespace(x0=1.0, y0=2.0, x1=3.0, y1=4.0),
                    "width": 1.5,
                    "closePath": True,
                    "items": [],
                },
                {
                    "layer": "Visible",
                    "rect": types.SimpleNamespace(x0=0.0, y0=0.0, x1=5.0, y1=5.0),
                    "width": 1.0,
                    "closePath": False,
                    "items": [],
                },
            ],
            get_texttrace=lambda: [
                {"layer": "0", "bbox": (1.0, 1.0, 2.0, 2.0)},
                {"layer": "Visible", "bbox": (2.0, 2.0, 3.0, 3.0)},
            ],
        )
        draw_masks, text_boxes, page_area = collect_layer_zero_masks(page, ["0ansi"])
        self.assertEqual(len(draw_masks), 1)
        self.assertEqual(len(text_boxes), 1)
        self.assertEqual(page_area, 96.0)


if __name__ == "__main__":
    unittest.main()
