# assets.py
from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

from config import ENG_RELEASE_MAP as CFG_ENG_RELEASE_MAP, W_RELEASE_ROOT as CFG_W_RELEASE_ROOT

W_RELEASE_ROOT = CFG_W_RELEASE_ROOT
ENG_RELEASE_MAP: List[Tuple[str, str]] = list(CFG_ENG_RELEASE_MAP)


def configure_release_mapping(
    w_release_root: Optional[str] = None,
    eng_release_map: Optional[List[Tuple[str, str]]] = None,
) -> None:
    global W_RELEASE_ROOT, ENG_RELEASE_MAP
    if w_release_root:
        W_RELEASE_ROOT = str(w_release_root)
    if eng_release_map is not None:
        ENG_RELEASE_MAP = list(eng_release_map)


def _unique_norm_paths(paths: List[str]) -> List[str]:
    seen = set()
    out = []
    for p in paths:
        if not p:
            continue
        p2 = os.path.normpath(p)
        key = p2.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p2)
    return out


def map_to_eng_release(sym_dir: str) -> List[str]:
    out: List[str] = []
    for src_prefix, dst_prefix in ENG_RELEASE_MAP:
        if sym_dir.lower().startswith(src_prefix.lower()):
            mapped = dst_prefix + sym_dir[len(src_prefix):]
            out.append(mapped)
            out.append(os.path.dirname(mapped))
    return _unique_norm_paths(out)


def _force_w_candidates(sym_dir: str, fname: str) -> List[str]:
    tried: List[str] = []

    for d in map_to_eng_release(sym_dir):
        tried.append(os.path.join(d, fname))
        tried.append(os.path.join(d, "Parts", fname))

    m = re.search(r"(F\d{3,6}.*)$", sym_dir, flags=re.IGNORECASE)
    if m:
        tail = m.group(1)
        tried.append(os.path.join(W_RELEASE_ROOT, tail, fname))
        tried.append(os.path.join(W_RELEASE_ROOT, tail, "Parts", fname))

    drive, rest = os.path.splitdrive(sym_dir)
    if rest:
        rest2 = rest.lstrip("\\/")
        m2 = re.search(r"(F\d{3,6}.*)$", rest2, flags=re.IGNORECASE)
        if m2:
            tail2 = m2.group(1)
            tried.append(os.path.join(W_RELEASE_ROOT, tail2, fname))
            tried.append(os.path.join(W_RELEASE_ROOT, tail2, "Parts", fname))
        else:
            tried.append(os.path.join(W_RELEASE_ROOT, rest2, fname))
            tried.append(os.path.join(W_RELEASE_ROOT, rest2, "Parts", fname))

    return _unique_norm_paths(tried)


def resolve_asset(sym_path: str, ext: str) -> Optional[str]:
    sym_dir = os.path.dirname(sym_path)
    base = os.path.splitext(os.path.basename(sym_path))[0]
    fname = base + ext

    for cand in _force_w_candidates(sym_dir, fname):
        if os.path.exists(cand):
            return cand

    cand = os.path.join(sym_dir, fname)
    if os.path.exists(cand):
        return cand

    return None
