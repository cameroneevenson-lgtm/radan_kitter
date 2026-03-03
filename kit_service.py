from __future__ import annotations

import os
from typing import Dict, List

import rpd_io
import sym_io
from app_utils import (
    backup_file,
    ensure_dir,
    force_l_drive_path,
    is_valid_kit_name,
    kit_file_path_for_part_sym,
    kit_text_for_rpd,
    safe_int_1_9,
    sanitize_kit_name,
)
from rpd_io import PartRow


def apply_balance_and_update_kit_texts(
    parts: List[PartRow],
    *,
    kits_dirname: str,
    kit_to_priority: Dict[str, str],
) -> None:
    for p in parts:
        k = sanitize_kit_name(p.kit_label)
        if not k:
            p.kit_label = ""
            p.kit_text = ""
            p.priority = str(safe_int_1_9(p.priority or "9"))
            continue
        if not is_valid_kit_name(k):
            k = sanitize_kit_name(k)
            if not k:
                p.kit_label = ""
                p.kit_text = ""
                p.priority = str(safe_int_1_9(p.priority or "9"))
                continue
        p.kit_label = k
        p.kit_text = kit_text_for_rpd(p.sym, k, kits_dirname)
        if k in kit_to_priority:
            p.priority = kit_to_priority[k]
        else:
            p.priority = str(safe_int_1_9(p.priority or "9"))


def prepare_kits(
    parts: List[PartRow],
    *,
    rpd_path: str,
    donor_template_path: str,
    bak_dirname: str,
    kits_dirname: str,
    kit_to_priority: Dict[str, str],
) -> int:
    apply_balance_and_update_kit_texts(
        parts,
        kits_dirname=kits_dirname,
        kit_to_priority=kit_to_priority,
    )

    base_dir = os.path.dirname(rpd_path)

    # Write kit name into Attr 109 (Comments) on each part .sym from RPD paths.
    parts_backup_dir = os.path.join(base_dir, bak_dirname, "parts")
    ensure_dir(parts_backup_dir)
    touched: set[str] = set()
    for p in parts:
        kit_name = sanitize_kit_name(p.kit_label)
        if not kit_name:
            continue
        sym_path = os.path.normpath(p.sym or "")
        if not sym_path or sym_path.lower() in touched:
            continue
        if f"\\{kits_dirname}\\" in sym_path.lower():
            continue
        touched.add(sym_path.lower())
        if os.path.exists(sym_path):
            backup_file(sym_path, parts_backup_dir)
            sym_io.set_sym_attr_109_comment(sym_path, kit_name)

    # Build kit .sym files from donor.
    if not os.path.exists(donor_template_path):
        raise RuntimeError(f"Donor not found: {donor_template_path}")

    kits_backup_dir = os.path.join(base_dir, bak_dirname, "kits")
    ensure_dir(kits_backup_dir)
    kits_to_parts = sym_io.group_parts_by_kit(
        parts=parts,
        sanitize_kit_name=sanitize_kit_name,
        is_valid_kit_name=is_valid_kit_name,
    )

    for kit_label, plist in kits_to_parts.items():
        kit_label = sanitize_kit_name(kit_label)
        if not kit_label:
            continue
        if not is_valid_kit_name(kit_label):
            kit_label = sanitize_kit_name(kit_label)
            if not kit_label:
                continue
        member_syms = [force_l_drive_path(p.sym) for p in plist]
        out_path = kit_file_path_for_part_sym(plist[0].sym, kit_label, kits_dirname)
        sym_io.build_kit_sym_from_donor(
            donor_path=donor_template_path,
            member_part_syms=member_syms,
            out_kit_sym_path=out_path,
            backup_dir=kits_backup_dir,
        )
    return len(kits_to_parts)


def write_rpd_with_backup(
    tree,
    parts: List[PartRow],
    *,
    rpd_path: str,
    bak_dirname: str,
) -> str:
    base_dir = os.path.dirname(rpd_path)
    bak_dir = os.path.join(base_dir, bak_dirname)
    ensure_dir(bak_dir)
    bak_path = backup_file(rpd_path, bak_dir)
    rpd_io.write_rpd_in_place(tree, parts, rpd_path)
    return bak_path
