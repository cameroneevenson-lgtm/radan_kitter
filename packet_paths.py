from __future__ import annotations

import os
import re
from typing import Callable, List, Sequence, Tuple


def unique_norm_paths(paths: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for path in paths:
        if not path:
            continue
        normalized = os.path.normpath(path)
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out


def map_to_eng_release(sym_path: str, *, eng_release_map: Sequence[Tuple[str, str]]) -> str:
    """Map a symbol path to a release path using ENG_RELEASE_MAP when possible.

    This is the single canonical implementation of the prefix-remap logic;
    assets.py delegates to it so the matching rules stay in one place.
    """
    normalized = os.path.normpath(sym_path or "")
    if not normalized:
        return normalized
    normalized_lower = normalized.lower()
    for src, dst in eng_release_map:
        src_raw = str(src or "").strip()
        if not src_raw:
            continue
        src_normalized = os.path.normpath(src_raw)
        src_lower = src_normalized.lower()
        # Require a full path-segment match (equal, or followed by a path
        # separator) so a prefix like "L:\Foo" doesn't wrongly match
        # "L:\FooBar\...".
        if (
            normalized_lower == src_lower
            or normalized_lower.startswith(src_lower + os.sep.lower())
            or normalized_lower.startswith(src_lower + "\\")
        ):
            rel = normalized[len(src_normalized) :].lstrip(r"\/")
            return os.path.normpath(os.path.join(dst, rel))
    return normalized


def force_w_candidates(
    sym_path: str,
    *,
    w_release_root: str,
    eng_release_map: Sequence[Tuple[str, str]],
) -> List[str]:
    """
    Generate W:-first candidates for pdf/dxf resolution.
    Logic:
      - Derive part name from symbol basename.
      - Prefer W:\\...\\<F-number>\\... first
      - Handle 'Parts' folder sometimes present.
      - Fallback: same directory as sym.
    """
    normalized = os.path.normpath(sym_path or "")
    base = os.path.splitext(os.path.basename(normalized))[0]

    tokens = re.split(r"[\\/]+", normalized)
    fnum = ""
    for token in tokens:
        if re.fullmatch(r"F\d{3,}", token, flags=re.IGNORECASE):
            fnum = token.upper()
            break

    candidates: List[str] = []
    if fnum:
        candidates.append(os.path.join(w_release_root, fnum, "Parts", base))
        candidates.append(os.path.join(w_release_root, fnum, base))

    mapped = map_to_eng_release(normalized, eng_release_map=eng_release_map)
    if mapped and mapped != normalized:
        candidates.append(os.path.join(os.path.dirname(mapped), base))

    if normalized:
        candidates.append(os.path.join(os.path.dirname(normalized), base))

    return unique_norm_paths(candidates)


def resolve_asset(
    sym_path: str,
    ext: str,
    *,
    w_release_root: str,
    eng_release_map: Sequence[Tuple[str, str]],
    exists_fn: Callable[[str], bool] = os.path.exists,
) -> str:
    """
    Resolve an asset path (PDF/DXF) from a symbol path.
    ext should be '.pdf' or '.dxf'.
    """
    normalized_ext = ext.lower().strip()
    if not normalized_ext.startswith("."):
        normalized_ext = "." + normalized_ext

    for base in force_w_candidates(
        sym_path,
        w_release_root=w_release_root,
        eng_release_map=eng_release_map,
    ):
        path = base + normalized_ext
        if exists_fn(path):
            return path

    normalized = os.path.normpath(sym_path or "")
    if normalized:
        path = os.path.join(
            os.path.dirname(normalized),
            os.path.splitext(os.path.basename(normalized))[0] + normalized_ext,
        )
        if exists_fn(path):
            return path

    return ""
