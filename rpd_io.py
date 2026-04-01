# rpd_io.py
from __future__ import annotations

import io
import os
import re
import shutil
import datetime
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Tuple

RADAN_NS = "http://www.radan.com/ns/project"
NS = {"r": RADAN_NS}


# --- local helpers (kept self-contained for Step 2) ---

def now_stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)

def atomic_write_bytes(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)

def safe_int_1_9(s: str, default: int = 9) -> int:
    try:
        v = int(str(s).strip())
    except Exception:
        return default
    return max(1, min(9, v))

def _parse_int_text(s: str, default: int = 0) -> int:
    try:
        return int(float(str(s).strip()))
    except Exception:
        return default

def kit_label_from_rpd_text(kit_text: str) -> str:
    if not kit_text:
        return ""
    b = os.path.basename(kit_text)
    b = os.path.splitext(b)[0]
    return b.strip()

def _find_child_text(el: ET.Element, tag_candidates: List[str]) -> str:
    for t in tag_candidates:
        v = el.findtext(f"r:{t}", "", NS)
        if v:
            return v
    wanted = {str(t or "").strip().lower() for t in tag_candidates if str(t or "").strip()}
    if not wanted:
        return ""
    for child in list(el):
        tag = re.sub(r"^\{.*\}", "", str(child.tag or "")).strip().lower()
        if tag not in wanted:
            continue
        v = str(child.text or "").strip()
        if v:
            return v
    return ""


# --- data model (same shape as monolith) ---

@dataclass
class PartRow:
    pid: str
    sym: str
    kit_text: str
    priority: str
    qty: int
    material: str
    thickness: str
    extra: int = 0

    kit_label: str = ""
    suggested_kit: str = ""
    suggested_conf: float = 0.0

    approved: bool = False
    needs_review: bool = False

    @property
    def part(self) -> str:
        return os.path.splitext(os.path.basename(self.sym))[0]


# --- public API ---

def load_rpd(path: str) -> Tuple[ET.ElementTree, List[PartRow], Dict[str, str]]:
    tree = ET.parse(path)
    root = tree.getroot()
    parts: List[PartRow] = []
    debug: Dict[str, str] = {}

    part_els = root.findall(".//r:Part", NS)
    if part_els:
        sample = part_els[0]
        debug["sample_child_tags"] = ",".join(
            sorted({re.sub(r"^\{.*\}", "", c.tag) for c in list(sample)})
        )

    qty_tags = ["Qty", "QTY", "Quantity", "Count", "Num", "Number", "Instances"]
    extra_tags = ["Extra", "ExtraQty", "ExtraQTY", "ExtraQuantity"]
    mat_tags = ["Material", "Mat"]
    thk_tags = ["Thickness", "Thk", "Gauge"]

    for el in part_els:
        pid = el.findtext("r:ID", "", NS)
        sym = el.findtext("r:Symbol", "", NS)
        kit_text = el.findtext("r:Kit", "", NS)
        priority = el.findtext("r:Priority", "", NS)

        qty_s = _find_child_text(el, qty_tags)
        extra_s = _find_child_text(el, extra_tags)
        mat = _find_child_text(el, mat_tags)
        thk = _find_child_text(el, thk_tags)

        qty = _parse_int_text(qty_s, default=1) if qty_s else 1
        extra = _parse_int_text(extra_s, default=0) if extra_s else 0

        row = PartRow(
            pid=pid,
            sym=sym,
            kit_text=kit_text,
            priority=str(safe_int_1_9(priority or "9")),
            qty=qty,
            material=mat or "",
            thickness=thk or "",
            extra=extra,
        )
        row.kit_label = kit_label_from_rpd_text(kit_text)
        parts.append(row)

    return tree, parts, debug


def write_rpd_in_place(tree: ET.ElementTree, parts: List[PartRow], rpd_path: str) -> None:
    by_id = {p.pid: p for p in parts}
    root = tree.getroot()

    for el in root.findall(".//r:Part", NS):
        pid = el.findtext("r:ID", "", NS)
        if pid not in by_id:
            continue
        p = by_id[pid]

        kit_el = el.find(f"{{{RADAN_NS}}}Kit")
        if kit_el is None:
            kit_el = ET.SubElement(el, f"{{{RADAN_NS}}}Kit")
        kit_el.text = p.kit_text or ""

        pri_el = el.find(f"{{{RADAN_NS}}}Priority")
        if pri_el is None:
            pri_el = ET.SubElement(el, f"{{{RADAN_NS}}}Priority")
        pri_el.text = str(safe_int_1_9(p.priority or "9"))

    ET.register_namespace("", RADAN_NS)
    buf = io.BytesIO()
    tree.write(buf, encoding="utf-8", xml_declaration=True)
    atomic_write_bytes(rpd_path, buf.getvalue())
