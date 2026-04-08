from __future__ import annotations

import datetime
import os
import shutil


def now_stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        ensure_dir(parent)


def atomic_write_bytes(path: str, data: bytes) -> None:
    ensure_parent_dir(path)
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


def safe_int_1_9(value: object, default: int = 9) -> int:
    try:
        parsed = int(str(value).strip())
    except Exception:
        return default
    return max(1, min(9, parsed))
