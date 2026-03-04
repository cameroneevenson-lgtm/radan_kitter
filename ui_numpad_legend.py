from __future__ import annotations

from html import escape
from typing import Dict, List, Optional


def build_numpad_legend_html(
    canon_kits: List[str],
    kit_abbr: Dict[str, str],
    highlight_idx: Optional[int],
    selected_idx: Optional[int] = None,
) -> str:
    keys_layout = [7, 8, 9, 4, 5, 6, 1, 2, 3]
    # Canonical priorities are 1..9 for canon_kits[0..8].
    # Map each visible numpad key to canonical kit index.
    idx_for_key = {
        1: 0, 2: 1, 3: 2,
        4: 3, 5: 4, 6: 5,
        7: 6, 8: 7, 9: 8,
    }

    def cell(i: int) -> str:
        k = keys_layout[i]
        kit_idx = idx_for_key[k]
        kit = canon_kits[kit_idx]
        # Keep full kit name visible in legend (3-letter abbreviations were ambiguous).
        label = kit.strip() or kit_abbr.get(kit, kit[:3].upper())
        base = (
            "min-width:108px; border:1px solid #9eb1c2; border-radius:5px;"
            "background:#f6f9fc; color:#16222d; padding:0;"
        )
        inner_pad = "padding:4px 6px;"
        if kit_idx == selected_idx and kit_idx == highlight_idx:
            base = (
                "min-width:108px; border:2px solid #d8ba5a; border-radius:5px;"
                "background:#2c4f7b; color:#f4f8ff; padding:0;"
            )
            inner_pad = "padding:3px 5px;"
        elif kit_idx == selected_idx:
            base = (
                "min-width:108px; border:2px solid #7db2ff; border-radius:5px;"
                "background:#2f6feb; color:#f4f8ff; padding:0;"
            )
            inner_pad = "padding:3px 5px;"
        elif kit_idx == highlight_idx:
            base = (
                "min-width:108px; border:2px solid #8f7800; border-radius:5px;"
                "background:#ffe16b; color:#121212; padding:0;"
            )
            inner_pad = "padding:3px 5px;"
        link = (
            f'<a href="assign:{kit_idx}" '
            f'style="display:block; width:100%; text-decoration:none; color:inherit; {inner_pad}">'
            f'<div style="font-weight:700; font-size:14px;">{k}</div>'
            f'<div style="font-size:10px; line-height:1.15;">{escape(label)}</div>'
            "</a>"
        )
        return (
            f'<td style="{base}">'
            f"{link}"
            "</td>"
        )

    op = (
        "min-width:74px; border:1px solid #9eb1c2; border-radius:5px;"
        "background:#eef3f8; color:#16222d; padding:0;"
    )

    def op_cell(href: str, key_label: str, sub_label: str) -> str:
        return (
            f'<td style="{op}">'
            f'<a href="{href}" style="display:block; width:100%; padding:4px 6px; text-decoration:none; color:inherit;">'
            f"<b>{key_label}</b><br/><span style=\"font-size:10px;\">{sub_label}</span>"
            "</a>"
            "</td>"
        )

    return (
        "<div style=\"text-align:center;\">"
        "<table style=\"margin:4px auto; border-collapse:separate; border-spacing:4px;\">"
        f"<tr>{cell(0)}{cell(1)}{cell(2)}{op_cell('move_up', '-', 'Row Up')}</tr>"
        f"<tr>{cell(3)}{cell(4)}{cell(5)}<td rowspan=\"2\" style=\"{op}\"><a href=\"move_down\" style=\"display:block; width:100%; min-height:74px; padding:4px 6px; text-decoration:none; color:inherit;\"><b>+</b><br/><span style=\"font-size:10px;\">Row Down</span></a></td></tr>"
        f"<tr>{cell(6)}{cell(7)}{cell(8)}</tr>"
        f"<tr><td colspan=\"2\" style=\"{op}\"><a href=\"clear\" style=\"display:block; width:100%; padding:4px 6px; text-decoration:none; color:inherit;\"><b>0</b><br/><span style=\"font-size:10px;\">Clear</span></a></td><td style=\"{op}\"><b>.</b></td><td style=\"{op}\"><a href=\"accept\" style=\"display:block; width:100%; padding:4px 6px; text-decoration:none; color:inherit;\"><b>Enter</b><br/><span style=\"font-size:10px;\">Accept RF</span></a></td></tr>"
        "</table>"
        "</div>"
    )
