from __future__ import annotations

import os
from typing import Callable, List, Optional, Tuple

import pdf_packet
from app_utils import ensure_dir, now_stamp, windows_natural_sort_key
from rpd_io import PartRow


def sort_packet_parts(parts: List[PartRow]) -> List[PartRow]:
    return sorted(
        list(parts or []),
        key=lambda part: (
            windows_natural_sort_key(getattr(part, "sym", "") or getattr(part, "part", "")),
            str(getattr(part, "sym", "") or "").lower(),
        ),
    )


def build_packet(
    parts: List[PartRow],
    *,
    rpd_path: str,
    out_dirname: str,
    resolve_asset_fn: Callable[[str, str], Optional[str]],
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    should_cancel_cb: Optional[Callable[[], bool]] = None,
    max_workers: Optional[int] = None,
    render_mode: str = "raster",
) -> Tuple[str, int, int]:
    base_dir = os.path.dirname(rpd_path)
    out_dir = os.path.join(base_dir, out_dirname)
    ensure_dir(out_dir)
    packet_path = os.path.join(out_dir, f"PrintPacket_QTY_{now_stamp()}.pdf")
    ordered_parts = sort_packet_parts(parts)
    pages, missing = pdf_packet.build_watermarked_packet(
        ordered_parts,
        packet_path,
        resolve_asset_fn=resolve_asset_fn,
        progress_cb=progress_cb,
        should_cancel_cb=should_cancel_cb,
        max_workers=max_workers,
        render_mode=render_mode,
    )
    return packet_path, pages, missing


PacketBuildCanceled = pdf_packet.PacketBuildCanceled
PacketBuildEmpty = pdf_packet.PacketBuildEmpty
