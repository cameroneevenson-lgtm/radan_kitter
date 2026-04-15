# assets.py
from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple

import runtime_trace as rt
from config import (
    ASSET_LOOKUP_SETTINGS_PATH as CFG_ASSET_LOOKUP_SETTINGS_PATH,
    ENG_RELEASE_MAP as CFG_ENG_RELEASE_MAP,
    W_RELEASE_ROOT as CFG_W_RELEASE_ROOT,
)

DEFAULT_W_RELEASE_ROOT = os.path.normpath(CFG_W_RELEASE_ROOT)
DEFAULT_ENG_RELEASE_MAP: List[Tuple[str, str]] = [
    (os.path.normpath(str(src)), os.path.normpath(str(dst)))
    for src, dst in CFG_ENG_RELEASE_MAP
]
ASSET_LOOKUP_SETTINGS_PATH = CFG_ASSET_LOOKUP_SETTINGS_PATH

W_RELEASE_ROOT = DEFAULT_W_RELEASE_ROOT
ENG_RELEASE_MAP: List[Tuple[str, str]] = list(DEFAULT_ENG_RELEASE_MAP)

_BASE_W_RELEASE_ROOT = DEFAULT_W_RELEASE_ROOT
_BASE_ENG_RELEASE_MAP: List[Tuple[str, str]] = list(DEFAULT_ENG_RELEASE_MAP)
_ASSET_ROOT_SOURCE = "default"
_TREE_INDEX_CACHE: Dict[Tuple[str, str], Tuple[Dict[str, str], Dict[str, List[str]]]] = {}
_RESOLVE_RESULT_CACHE: Dict[Tuple[str, str, str], str] = {}
ASSET_INDEX_TRACE_MIN_MS = 150
ASSET_RESOLVE_TRACE_MIN_MS = 75


def _normalize_path(path: object) -> str:
    raw = str(path or "").strip()
    return os.path.normpath(raw) if raw else ""


def _clear_search_cache() -> None:
    _TREE_INDEX_CACHE.clear()
    _RESOLVE_RESULT_CACHE.clear()


def _normalize_release_map(pairs: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for src, dst in pairs:
        src_norm = _normalize_path(src)
        dst_norm = _normalize_path(dst)
        if not src_norm:
            continue
        out.append((src_norm, dst_norm))
    return out


def configure_release_mapping(
    w_release_root: Optional[str] = None,
    eng_release_map: Optional[List[Tuple[str, str]]] = None,
    *,
    remember_base: bool = True,
) -> None:
    global W_RELEASE_ROOT, ENG_RELEASE_MAP
    global _BASE_W_RELEASE_ROOT, _BASE_ENG_RELEASE_MAP, _ASSET_ROOT_SOURCE

    next_map = (
        _normalize_release_map(list(eng_release_map))
        if eng_release_map is not None
        else list(ENG_RELEASE_MAP)
    )
    if not next_map:
        next_map = list(DEFAULT_ENG_RELEASE_MAP)

    next_root = _normalize_path(w_release_root)
    if not next_root:
        next_root = _normalize_path(next_map[0][1]) if next_map else _BASE_W_RELEASE_ROOT
    if not next_root:
        next_root = DEFAULT_W_RELEASE_ROOT

    next_map = [(src, next_root) for src, _ in next_map]

    W_RELEASE_ROOT = next_root
    ENG_RELEASE_MAP = list(next_map)
    _clear_search_cache()

    if remember_base:
        _BASE_W_RELEASE_ROOT = W_RELEASE_ROOT
        _BASE_ENG_RELEASE_MAP = list(ENG_RELEASE_MAP)
        _ASSET_ROOT_SOURCE = "default"


def _ensure_settings_dir() -> None:
    settings_dir = os.path.dirname(_normalize_path(ASSET_LOOKUP_SETTINGS_PATH))
    if settings_dir:
        os.makedirs(settings_dir, exist_ok=True)


def _read_settings() -> dict:
    path = _normalize_path(ASSET_LOOKUP_SETTINGS_PATH)
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_settings(asset_root_override: str) -> None:
    _ensure_settings_dir()
    payload = {
        "asset_root_override": str(asset_root_override or "").strip(),
    }
    with open(ASSET_LOOKUP_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def set_asset_root_override(
    path: Optional[str],
    *,
    persist: bool = True,
    source: str = "saved",
) -> str:
    global _ASSET_ROOT_SOURCE

    root = _normalize_path(path)
    if root:
        configure_release_mapping(
            w_release_root=root,
            eng_release_map=_BASE_ENG_RELEASE_MAP,
            remember_base=False,
        )
        _ASSET_ROOT_SOURCE = str(source or "saved").strip().lower() or "saved"
    else:
        configure_release_mapping(
            w_release_root=_BASE_W_RELEASE_ROOT,
            eng_release_map=_BASE_ENG_RELEASE_MAP,
            remember_base=False,
        )
        _ASSET_ROOT_SOURCE = "default"

    if persist:
        _write_settings(root)
    return W_RELEASE_ROOT


def load_asset_root_preferences() -> str:
    env_root = _normalize_path(os.environ.get("RADAN_KITTER_ASSET_ROOT", ""))
    if env_root:
        return set_asset_root_override(env_root, persist=False, source="env")

    saved_root = _normalize_path(_read_settings().get("asset_root_override", ""))
    if saved_root:
        return set_asset_root_override(saved_root, persist=False, source="saved")

    return set_asset_root_override(None, persist=False, source="default")


def get_asset_root_state() -> dict:
    root = _normalize_path(W_RELEASE_ROOT)
    base_root = _normalize_path(_BASE_W_RELEASE_ROOT)
    source = str(_ASSET_ROOT_SOURCE or "default").strip().lower() or "default"
    return {
        "root": root,
        "base_root": base_root,
        "source": source,
        "override_active": bool(root and base_root and root.lower() != base_root.lower()) or source != "default",
    }


def _unique_norm_paths(paths: List[str]) -> List[str]:
    seen = set()
    out = []
    for p in paths:
        pn = _normalize_path(p)
        if not pn:
            continue
        key = pn.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(pn)
    return out


def _map_path_to_eng_release(source_path: str) -> str:
    sp = _normalize_path(source_path)
    if not sp:
        return ""
    sp_low = sp.lower()
    for src_prefix, dst_prefix in ENG_RELEASE_MAP:
        src_norm = _normalize_path(src_prefix)
        if not src_norm:
            continue
        src_low = src_norm.lower()
        if sp_low == src_low or sp_low.startswith(src_low + os.sep.lower()) or sp_low.startswith(src_low + "\\"):
            rel = sp[len(src_norm):].lstrip("\\/")
            return _normalize_path(os.path.join(dst_prefix, rel))
    return sp


def map_to_eng_release(sym_dir: str) -> List[str]:
    sym_norm = _normalize_path(sym_dir)
    mapped = _map_path_to_eng_release(sym_norm)
    if not mapped or mapped.lower() == sym_norm.lower():
        return []
    return _unique_norm_paths([mapped, os.path.dirname(mapped)])


def _extract_fnum_tail(path: str) -> Tuple[str, str]:
    norm = _normalize_path(path)
    if not norm:
        return "", ""
    tokens = [tok for tok in re.split(r"[\\/]+", norm) if tok]
    for idx, token in enumerate(tokens):
        if re.fullmatch(r"F\d{3,}", token, flags=re.IGNORECASE):
            tail_tokens = [tok for tok in tokens[idx + 1:] if tok]
            tail = os.path.join(*tail_tokens) if tail_tokens else ""
            return token.upper(), tail
    return "", ""


def _candidate_filenames(base: str, ext: str) -> List[str]:
    base = str(base or "").strip()
    ext = str(ext or "").strip().lower()
    if not base:
        return []
    if not ext.startswith("."):
        ext = "." + ext
    variants = [
        base,
        re.sub(r"[\s_-]+", "-", base).strip("-"),
        re.sub(r"[\s_-]+", " ", base).strip(),
        base.replace(" ", "-"),
        base.replace("-", " "),
        base.replace("_", "-"),
        base.replace("_", " "),
    ]
    out: List[str] = []
    seen = set()
    for variant in variants:
        stem = str(variant or "").strip()
        if not stem:
            continue
        fname = stem + ext
        key = fname.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(fname)
    return out


def _normalize_part_stem(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _match_immediate_dir(
    dir_path: str,
    base: str,
    ext: str,
    *,
    allow_fuzzy_listing: bool = True,
) -> Optional[str]:
    for fname in _candidate_filenames(base, ext):
        cand = os.path.join(dir_path, fname)
        if os.path.exists(cand):
            return cand

    if not allow_fuzzy_listing:
        return None

    try:
        names = os.listdir(dir_path)
    except Exception:
        return None

    target_stem = _normalize_part_stem(base)
    if not target_stem:
        return None

    matches: List[str] = []
    for name in names:
        stem, name_ext = os.path.splitext(name)
        if name_ext.lower() != ext.lower():
            continue
        if _normalize_part_stem(stem) != target_stem:
            continue
        matches.append(os.path.join(dir_path, name))

    if len(matches) == 1:
        return matches[0]
    return _pick_best_match(matches, preferred_dirs=[dir_path]) if matches else None


def _candidate_asset_dirs(sym_path: str) -> List[str]:
    sp = _normalize_path(sym_path)
    sym_dir = os.path.dirname(sp)
    root = _normalize_path(W_RELEASE_ROOT)
    fnum, tail = _extract_fnum_tail(sym_dir)

    out: List[str] = []
    for mapped_dir in map_to_eng_release(sym_dir):
        out.append(mapped_dir)
        out.append(os.path.join(mapped_dir, "Parts"))

    if root:
        if fnum:
            out.append(os.path.join(root, fnum))
            out.append(os.path.join(root, fnum, "Parts"))
            if tail:
                out.append(os.path.join(root, fnum, tail))
                out.append(os.path.join(root, fnum, tail, "Parts"))
                out.append(os.path.join(root, tail))
                out.append(os.path.join(root, tail, "Parts"))

    if sym_dir:
        out.append(sym_dir)
        out.append(os.path.join(sym_dir, "Parts"))

    if root:
        out.append(root)
        out.append(os.path.join(root, "Parts"))

    return _unique_norm_paths(out)


def _root_contains_fnum(root: str, fnum: str) -> bool:
    root_norm = _normalize_path(root)
    token = str(fnum or "").strip()
    if not root_norm or not token:
        return False
    pattern = rf"(^|[\\/]){re.escape(token)}([\\/]|$)"
    return re.search(pattern, root_norm, flags=re.IGNORECASE) is not None


def _candidate_search_roots(sym_path: str) -> List[str]:
    sp = _normalize_path(sym_path)
    sym_dir = os.path.dirname(sp)
    root = _normalize_path(W_RELEASE_ROOT)
    fnum, _tail = _extract_fnum_tail(sym_dir)
    state = get_asset_root_state()

    out: List[str] = []
    mapped = _map_path_to_eng_release(sp)
    if mapped and mapped.lower() != sp.lower():
        out.append(os.path.dirname(mapped))

    if root:
        override_active = bool(state.get("override_active", False))
        if fnum and _root_contains_fnum(root, fnum):
            out.append(root)
        elif fnum:
            out.append(os.path.join(root, fnum))
        elif override_active:
            out.append(root)

    if sym_dir:
        out.append(sym_dir)

    return _unique_norm_paths(out)


def _resolve_cache_key(sym_path: str, ext: str, search_mode: str) -> Tuple[str, str, str, str]:
    return (
        _normalize_path(sym_path).lower(),
        str(ext or "").strip().lower(),
        _normalize_path(W_RELEASE_ROOT).lower(),
        str(search_mode or "full").strip().lower(),
    )


def _allow_fuzzy_dir_scan(dir_path: str, sym_path: str) -> bool:
    dir_norm = _normalize_path(dir_path)
    if not dir_norm:
        return False

    sym_norm = _normalize_path(sym_path)
    sym_dir = os.path.dirname(sym_norm)
    fnum, _tail = _extract_fnum_tail(sym_dir)
    root = _normalize_path(W_RELEASE_ROOT)

    local_dirs = _unique_norm_paths([sym_dir, os.path.join(sym_dir, "Parts")])
    if dir_norm.lower() in {p.lower() for p in local_dirs}:
        return True

    mapped_dirs: List[str] = []
    for mapped_dir in map_to_eng_release(sym_dir):
        mapped_dirs.append(mapped_dir)
        mapped_dirs.append(os.path.join(mapped_dir, "Parts"))
    if dir_norm.lower() in {p.lower() for p in _unique_norm_paths(mapped_dirs)}:
        return True

    if fnum and _root_contains_fnum(dir_norm, fnum):
        return True

    if root and dir_norm.lower() in {
        root.lower(),
        os.path.join(root, "Parts").lower(),
    }:
        return False

    return False


def _index_tree(root_dir: str, ext: str) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    norm_root = _normalize_path(root_dir)
    cache_key = (norm_root.lower(), ext.lower())
    cached = _TREE_INDEX_CACHE.get(cache_key)
    if cached is not None:
        return cached

    exact: Dict[str, str] = {}
    stem_matches: Dict[str, List[str]] = {}
    dirs_scanned = 0
    matched_files = 0
    root_exists = os.path.isdir(norm_root)
    profile = rt.stage(
        "asset_lookup",
        "index_tree",
        min_elapsed_ms=ASSET_INDEX_TRACE_MIN_MS,
        root=norm_root,
        ext=ext,
    )
    if root_exists:
        try:
            for dirpath, _dirnames, filenames in os.walk(norm_root):
                dirs_scanned += 1
                for name in filenames:
                    stem, name_ext = os.path.splitext(name)
                    if name_ext.lower() != ext.lower():
                        continue
                    matched_files += 1
                    full = os.path.join(dirpath, name)
                    lower_name = name.lower()
                    exact.setdefault(lower_name, full)
                    stem_key = _normalize_part_stem(stem)
                    if not stem_key:
                        continue
                    stem_matches.setdefault(stem_key, []).append(full)
        except Exception as exc:
            profile.fail(
                exc,
                root_exists=root_exists,
                dirs_scanned=dirs_scanned,
                matched_files=matched_files,
            )
    profile.success(
        root_exists=root_exists,
        dirs_scanned=dirs_scanned,
        matched_files=matched_files,
    )

    cached = (exact, stem_matches)
    _TREE_INDEX_CACHE[cache_key] = cached
    return cached


def _pick_best_match(paths: List[str], *, preferred_dirs: List[str]) -> Optional[str]:
    cleaned = _unique_norm_paths(paths)
    if not cleaned:
        return None

    preferred = [p.lower() for p in _unique_norm_paths(preferred_dirs)]

    def _score(path: str) -> Tuple[int, int, int]:
        norm = _normalize_path(path).lower()
        in_preferred = 1
        for pref in preferred:
            if norm == pref or norm.startswith(pref + "\\") or norm.startswith(pref + "/"):
                in_preferred = 0
                break
        depth = norm.count("\\") + norm.count("/")
        return (in_preferred, depth, len(norm))

    return sorted(cleaned, key=_score)[0]


def _match_subtree(root_dir: str, base: str, ext: str, preferred_dirs: List[str]) -> Optional[str]:
    exact, stem_matches = _index_tree(root_dir, ext)

    for fname in _candidate_filenames(base, ext):
        hit = exact.get(fname.lower())
        if hit and os.path.exists(hit):
            return hit

    stem_key = _normalize_part_stem(base)
    if not stem_key:
        return None

    matches = [p for p in stem_matches.get(stem_key, []) if os.path.exists(p)]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    return _pick_best_match(matches, preferred_dirs=preferred_dirs)


def _resolve_asset(
    sym_path: str,
    ext: str,
    *,
    allow_subtree_search: bool,
    search_mode: str,
) -> Optional[str]:
    t0 = time.perf_counter()
    sp = _normalize_path(sym_path)
    if not sp:
        return None

    ext = str(ext or "").strip().lower()
    if not ext.startswith("."):
        ext = "." + ext

    cache_key = _resolve_cache_key(sp, ext, search_mode)
    cached = _RESOLVE_RESULT_CACHE.get(cache_key)
    if cached is not None:
        return cached or None

    base = os.path.splitext(os.path.basename(sp))[0]
    candidate_dirs = _candidate_asset_dirs(sp)
    search_roots = _candidate_search_roots(sp)

    for dir_path in candidate_dirs:
        hit = _match_immediate_dir(
            dir_path,
            base,
            ext,
            allow_fuzzy_listing=_allow_fuzzy_dir_scan(dir_path, sp),
        )
        if hit:
            _RESOLVE_RESULT_CACHE[cache_key] = hit
            elapsed_ms = int((time.perf_counter() - t0) * 1000.0)
            if elapsed_ms >= ASSET_RESOLVE_TRACE_MIN_MS:
                rt.event(
                    "asset_lookup",
                    "resolve",
                    elapsed_ms=elapsed_ms,
                    sym_path=sp,
                    ext=ext,
                    result="hit",
                    strategy="candidate_dir",
                    candidate_dir_count=len(candidate_dirs),
                    search_root_count=len(search_roots),
                    search_mode=search_mode,
                    hit_path=hit,
                )
            return hit

    if allow_subtree_search:
        for root_dir in search_roots:
            hit = _match_subtree(root_dir, base, ext, preferred_dirs=candidate_dirs)
            if hit:
                _RESOLVE_RESULT_CACHE[cache_key] = hit
                elapsed_ms = int((time.perf_counter() - t0) * 1000.0)
                if elapsed_ms >= ASSET_RESOLVE_TRACE_MIN_MS:
                    rt.event(
                        "asset_lookup",
                        "resolve",
                        elapsed_ms=elapsed_ms,
                        sym_path=sp,
                        ext=ext,
                        result="hit",
                        strategy="search_root",
                        candidate_dir_count=len(candidate_dirs),
                        search_root_count=len(search_roots),
                        search_mode=search_mode,
                        hit_path=hit,
                    )
                return hit

    _RESOLVE_RESULT_CACHE[cache_key] = ""
    rt.event(
        "asset_lookup",
        "resolve",
        elapsed_ms=int((time.perf_counter() - t0) * 1000.0),
        sym_path=sp,
        ext=ext,
        result="miss",
        strategy="miss",
        candidate_dir_count=len(candidate_dirs),
        search_root_count=len(search_roots),
        search_mode=search_mode,
        hit_path="",
    )
    return None


def resolve_asset(sym_path: str, ext: str) -> Optional[str]:
    return _resolve_asset(
        sym_path,
        ext,
        allow_subtree_search=True,
        search_mode="full",
    )


def resolve_asset_fast(sym_path: str, ext: str) -> Optional[str]:
    return _resolve_asset(
        sym_path,
        ext,
        allow_subtree_search=False,
        search_mode="fast",
    )
