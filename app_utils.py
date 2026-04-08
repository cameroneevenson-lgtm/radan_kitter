from __future__ import annotations

import os
import re

from file_utils import atomic_write_bytes, backup_file, ensure_dir, now_stamp, safe_int_1_9


def kit_label_from_rpd_text(kit_text: str) -> str:
    if not kit_text:
        return ""
    b = os.path.basename(kit_text)
    b = os.path.splitext(b)[0]
    return b.strip()


def is_valid_kit_name(name: str) -> bool:
    if not name:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 \-]*", name))


def sanitize_kit_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return ""
    name = name.replace("_", "-")
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"[^A-Za-z0-9 \-]", "", name).strip()
    return name


def force_l_drive_path(path: str) -> str:
    p = os.path.normpath(path or "")
    if not p:
        return p
    drive, rest = os.path.splitdrive(p)
    if drive:
        return "L:" + rest
    return p


def kit_file_path_for_part_sym(part_sym_path: str, kit_label: str, kits_dirname: str) -> str:
    kit_label = sanitize_kit_name(kit_label)
    sym_dir = os.path.dirname(part_sym_path)
    kits_dir = os.path.join(sym_dir, kits_dirname)
    ensure_dir(kits_dir)
    return os.path.join(kits_dir, f"{kit_label}.sym")


def kit_text_for_rpd(part_sym_path: str, kit_label: str, kits_dirname: str) -> str:
    kit_label = sanitize_kit_name(kit_label)
    if not kit_label:
        return ""
    return kit_file_path_for_part_sym(part_sym_path, kit_label, kits_dirname)


def windows_natural_sort_key(text: str):
    """
    Sort text the way Windows Explorer typically orders file names:
    numeric runs compare as numbers, not plain strings.
    """
    raw = os.path.basename(str(text or ""))
    stem = os.path.splitext(raw)[0].strip().lower()
    if not stem:
        return ((1, ""),)
    key = []
    for part in re.split(r"(\d+)", stem):
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    if not key:
        key.append((1, stem))
    return tuple(key)
