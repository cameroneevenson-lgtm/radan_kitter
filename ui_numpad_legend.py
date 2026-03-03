from __future__ import annotations

from typing import Dict, List, Optional


def build_numpad_legend_html(
    canon_kits: List[str],
    kit_abbr: Dict[str, str],
    highlight_idx: Optional[int],
) -> str:
    key_for_idx = [7, 8, 9, 4, 5, 6, 1, 2, 3]

    def cell(i: int) -> str:
        k = key_for_idx[i]
        kit = canon_kits[i]
        abbr = kit_abbr.get(kit, kit[:3].upper())
        base = (
            "min-width:74px; border:1px solid #9eb1c2; border-radius:5px;"
            "background:#f6f9fc; color:#16222d; padding:4px 6px;"
        )
        if i == highlight_idx:
            base = (
                "min-width:74px; border:2px solid #8f7800; border-radius:5px;"
                "background:#ffe16b; color:#121212; padding:3px 5px;"
            )
        return (
            f'<td style="{base}">'
            f'<div style="font-weight:700; font-size:14px;">{k}</div>'
            f'<div style="font-size:10px;">{abbr}</div>'
            "</td>"
        )

    op = (
        "min-width:74px; border:1px solid #9eb1c2; border-radius:5px;"
        "background:#eef3f8; color:#16222d; padding:4px 6px;"
    )

    return (
        "<div style=\"text-align:center;\">"
        "<table style=\"margin:4px auto; border-collapse:separate; border-spacing:4px;\">"
        f"<tr>{cell(0)}{cell(1)}{cell(2)}<td style=\"{op}\"><b>-</b><br/><span style=\"font-size:10px;\">Row Up</span></td></tr>"
        f"<tr>{cell(3)}{cell(4)}{cell(5)}<td rowspan=\"2\" style=\"{op}\"><b>+</b><br/><span style=\"font-size:10px;\">Row Down</span></td></tr>"
        f"<tr>{cell(6)}{cell(7)}{cell(8)}</tr>"
        f"<tr><td colspan=\"2\" style=\"{op}\"><b>0</b><br/><span style=\"font-size:10px;\">Clear</span></td><td style=\"{op}\"><b>.</b></td><td style=\"{op}\"><b>Enter</b><br/><span style=\"font-size:10px;\">Accept RF</span></td></tr>"
        "</table>"
        "</div>"
    )
