from __future__ import annotations

import datetime
import os
import re
import shutil


def now_stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def atomic_write_bytes(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def backup_file(src_path: str, bak_dir: str) -> str:
    ensure_dir(bak_dir)
    base = os.path.basename(src_path)
    dst = os.path.join(bak_dir, f"{base}.{now_stamp()}.bak")
    shutil.copy2(src_path, dst)
    return dst


def safe_int_1_9(s: str, default: int = 9) -> int:
    try:
        v = int(str(s).strip())
    except Exception:
        return default
    return max(1, min(9, v))


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

