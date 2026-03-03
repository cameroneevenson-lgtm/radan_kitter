from __future__ import annotations

import datetime
import os
import re
import shutil
from typing import Callable, Dict, List, Optional, Tuple, TypeVar

T = TypeVar("T")


def now_stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


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


def read_text_fallback(path: str) -> str:
    data = open(path, "rb").read()
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def write_text_utf8(path: str, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


SYM_PATH_RE = re.compile(
    r"[A-Za-z]:[\\/](?:[^<>\"\r\n]+)[\\/](?:[^<>\"\r\n]+)\.sym",
    re.IGNORECASE,
)


def donor_extract_placeholder_paths(donor_text: str) -> Tuple[str, int]:
    paths = SYM_PATH_RE.findall(donor_text)
    if not paths:
        raise RuntimeError("Donor .sym: could not find any embedded .sym paths to use as instance slots.")
    freq: Dict[str, int] = {}
    for p in paths:
        key = os.path.normpath(p).lower()
        freq[key] = freq.get(key, 0) + 1
    placeholder_key = max(freq.items(), key=lambda kv: kv[1])[0]
    placeholder = None
    for p in paths:
        if os.path.normpath(p).lower() == placeholder_key:
            placeholder = p
            break
    if placeholder is None:
        placeholder = paths[0]
    capacity = freq[placeholder_key]
    return placeholder, capacity


def _extract_slot_blocks(
    lines: List[str],
    placeholder: str,
) -> List[Tuple[int, int, int, int]]:
    """
    Parse donor slot blocks and return tuples:
      (start_idx, end_idx, head_idx, path_idx)
    where block range is inclusive and expected shape is:
      U,,,,2...
      [optional N,...]
      F,...$/<part short>
      C,$
      U,,$<placeholder path>
      U,$
    """
    ph_path_norm = os.path.normpath(placeholder).lower()
    path_lines: List[int] = []
    for i, ln in enumerate(lines):
        nln = os.path.normpath(ln.strip().replace("U,,", "").replace("$", "")).lower()
        if nln == ph_path_norm or placeholder in ln:
            path_lines.append(i)

    if not path_lines:
        return []

    out: List[Tuple[int, int, int, int]] = []
    for path_idx in path_lines:
        # Start at nearest preceding slot opener.
        start_idx = path_idx
        for j in range(path_idx, max(-1, path_idx - 12), -1):
            if j >= 0 and lines[j].startswith("U,,,,2"):
                start_idx = j
                break

        # End at next "U,$" line, else path line.
        end_idx = path_idx
        if path_idx + 1 < len(lines) and lines[path_idx + 1].startswith("U,$"):
            end_idx = path_idx + 1

        # Find head line containing the short-name marker.
        head_idx = max(start_idx, path_idx - 1)
        for j in range(path_idx - 1, max(start_idx - 1, path_idx - 12), -1):
            if "$/" in lines[j]:
                head_idx = j
                break

        out.append((start_idx, end_idx, head_idx, path_idx))
    return out


def build_kit_sym_from_donor(
    donor_path: str,
    member_part_syms: List[str],
    out_kit_sym_path: str,
    backup_dir: Optional[str] = None,
) -> None:
    if not os.path.exists(donor_path):
        raise RuntimeError(f"Donor template not found: {donor_path}")

    donor_text = read_text_fallback(donor_path)
    placeholder, capacity = donor_extract_placeholder_paths(donor_text)

    k = len(member_part_syms)
    lines = donor_text.splitlines(keepends=True)
    ph_short = os.path.splitext(os.path.basename(placeholder))[0]
    slot_blocks = _extract_slot_blocks(lines, placeholder)
    if not slot_blocks:
        raise RuntimeError("Donor has no parsable instance slot blocks.")

    # Deduplicate any accidental repeated ranges.
    dedup: List[Tuple[int, int, int, int]] = []
    seen = set()
    for b in slot_blocks:
        key = (b[0], b[1])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(b)
    slot_blocks = sorted(dedup, key=lambda b: b[0])
    capacity = len(slot_blocks)

    prefix = lines[:slot_blocks[0][0]]
    suffix = lines[slot_blocks[-1][1] + 1 :]

    generated: List[str] = []
    for idx in range(k):
        src = slot_blocks[idx % capacity]
        s0, s1, h0, p0 = src
        block = lines[s0 : s1 + 1]
        head_rel = max(0, min(len(block) - 1, h0 - s0))
        path_rel = max(0, min(len(block) - 1, p0 - s0))

        sym_path = member_part_syms[idx]
        sym_short = os.path.splitext(os.path.basename(sym_path))[0]

        block[path_rel] = block[path_rel].replace(placeholder, sym_path)
        block[head_rel] = re.sub(rf"(\$/){re.escape(ph_short)}(\s*)$", rf"\1{sym_short}\2", block[head_rel])
        generated.extend(block)

    new_text = "".join(prefix + generated + suffix)

    new_text = re.sub(
        r'(<Info\s+num="0"\s+name="Number of Loops"\s+value=")\d+(")',
        rf"\g<1>{k}\2",
        new_text,
    )
    new_text = re.sub(
        r'(<Symbol\s+name="[^"]+"\s+count=")\d+(")',
        rf"\g<1>{k}\2",
        new_text,
    )

    if k > 0:
        first_short = os.path.splitext(os.path.basename(member_part_syms[0]))[0]
        new_text = re.sub(
            r'(<Symbol\s+name=")[^"]+("\s+count="\d+")',
            rf"\1{first_short}\2",
            new_text,
            count=1,
        )

    ensure_dir(os.path.dirname(out_kit_sym_path))
    if os.path.exists(out_kit_sym_path) and backup_dir:
        backup_file(out_kit_sym_path, backup_dir)
    write_text_utf8(out_kit_sym_path, new_text)


def set_sym_attr_109_comment(sym_path: str, comment: str) -> bool:
    if not sym_path or not os.path.exists(sym_path):
        return False
    txt = read_text_fallback(sym_path)
    pat = r'(<Attr\s+num="109"[^>]*)(>)'
    m = re.search(pat, txt, flags=re.IGNORECASE)
    if not m:
        return False
    open_tag = m.group(1)
    if re.search(r'\bvalue="[^"]*"', open_tag, flags=re.IGNORECASE):
        open_tag2 = re.sub(r'\bvalue="[^"]*"', f'value="{comment}"', open_tag, flags=re.IGNORECASE)
    else:
        open_tag2 = open_tag + f' value="{comment}"'
    new_txt = txt[:m.start(1)] + open_tag2 + txt[m.end(1):]
    if new_txt != txt:
        write_text_utf8(sym_path, new_txt)
    return True


def group_parts_by_kit(
    parts: List[T],
    sanitize_kit_name: Callable[[str], str],
    is_valid_kit_name: Callable[[str], bool],
) -> Dict[str, List[T]]:
    kits: Dict[str, List[T]] = {}
    for p in parts:
        kit_label = str(getattr(p, "kit_label", "") or "")
        k = sanitize_kit_name(kit_label)
        if not k:
            continue
        if not is_valid_kit_name(k):
            k = sanitize_kit_name(k)
            if not k:
                continue
        kits.setdefault(k, []).append(p)
    return kits
