from __future__ import annotations

import csv
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Sequence

import pandas as pd

_TEXT_TRACKING_COLS = {
    "timestamp_utc",
    "rpd_token",
    "part_name",
    "part_key",
    "kit_label",
    "pdf_path",
    "dxf_path",
}


def normalize_identity_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(raw)))
    except Exception:
        return os.path.normcase(os.path.normpath(raw))


def make_part_key(part_name: str, pdf_path: str, dxf_path: str) -> str:
    pdf_norm = normalize_identity_path(pdf_path)
    dxf_norm = normalize_identity_path(dxf_path)
    if pdf_norm or dxf_norm:
        return f"PDF={pdf_norm}|DXF={dxf_norm}"
    part_norm = str(part_name or "").strip().upper()
    return f"PART={part_norm}" if part_norm else ""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_local_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_dataset_exists(dataset_path: str, all_cols: Sequence[str]) -> None:
    os.makedirs(os.path.dirname(dataset_path) or ".", exist_ok=True)
    if not os.path.exists(dataset_path):
        pd.DataFrame(columns=list(all_cols)).to_csv(dataset_path, index=False)


def append_labeled_row(
    part_name: str,
    kit_label: str,
    pdf_path: str,
    dxf_path: str,
    *,
    dataset_path: str,
    all_cols: Sequence[str],
    compute_signals_fn: Callable[[str, str], Dict[str, float]],
    nan_fn: Callable[[], float],
    utc_now_iso_fn: Callable[[], str] = utc_now_iso,
    rpd_token: Optional[str] = None,
) -> None:
    """
    Append/Upsert one labeled row to the dataset.

    Global identity key: part_key derived from normalized PDF/DXF paths,
    with basename fallback when neither asset path is available.
    Behavior: LAST ADDED WINS for matching part_key.

    Computes features from pdf_path/dxf_path only.
    """
    ensure_dataset_exists(dataset_path, all_cols)
    df = load_dataset_df(dataset_path, all_cols, nan_fn)

    part_name_s = str(part_name or "").strip()
    pdf_path_s = str(pdf_path or "").strip()
    dxf_path_s = str(dxf_path or "").strip()
    part_key = make_part_key(part_name_s, pdf_path_s, dxf_path_s)
    if not part_name_s and not part_key:
        return

    if len(df) > 0:
        existing_keys = []
        for _idx, existing in df.iterrows():
            existing_keys.append(
                make_part_key(
                    str(existing.get("part_name") or ""),
                    str(existing.get("pdf_path") or ""),
                    str(existing.get("dxf_path") or ""),
                )
            )
        if part_key:
            keep_mask = [key != part_key for key in existing_keys]
            df = df.loc[keep_mask].copy()

    row = {
        "timestamp_utc": utc_now_iso_fn(),
        "rpd_token": str(rpd_token or "").strip(),
        "part_name": part_name_s,
        "part_key": part_key,
        "kit_label": str(kit_label or "").strip(),
        "pdf_path": pdf_path_s,
        "dxf_path": dxf_path_s,
    }

    feats = compute_signals_fn(row["pdf_path"], row["dxf_path"])
    row.update(feats)

    for col in all_cols:
        if col not in row:
            row[col] = nan_fn()

    out_cols = list(all_cols)
    new_row_df = pd.DataFrame([row], columns=out_cols)
    if df.empty:
        df = new_row_df
    else:
        df = pd.concat([df, new_row_df], ignore_index=True)
    df.to_csv(dataset_path, index=False)


def make_run_name(rpd_path: str, *, stamp_fn: Callable[[], str] = now_local_stamp) -> str:
    base = os.path.splitext(os.path.basename(rpd_path or ""))[0].strip()
    if not base:
        base = "run"
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", base).strip("_")
    return f"MLRun_{base}_{stamp_fn()}"


def load_existing_part_names(dataset_path: str) -> set[str]:
    if not dataset_path or not os.path.exists(dataset_path):
        return set()
    out: set[str] = set()
    try:
        with open(dataset_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = make_part_key(
                    str(row.get("part_name") or row.get("part") or ""),
                    str(row.get("pdf_path") or ""),
                    str(row.get("dxf_path") or ""),
                )
                if key:
                    out.add(key)
    except Exception:
        return set()
    return out


class ScanLogger:
    def __init__(
        self,
        run_dir: str,
        run_name: str,
        *,
        utc_now_iso_fn: Callable[[], str] = utc_now_iso,
    ):
        self.run_dir = run_dir
        self.run_name = run_name
        self._utc_now_iso = utc_now_iso_fn
        os.makedirs(self.run_dir, exist_ok=True)
        self.meta_path = os.path.join(self.run_dir, f"{self.run_name}.meta.json")
        self.parts_path = os.path.join(self.run_dir, f"{self.run_name}.parts.jsonl")
        self.summary_path = os.path.join(self.run_dir, f"{self.run_name}.summary.json")
        self._t0 = time.time()

    def write_meta(self, meta: Dict[str, Any]) -> None:
        payload = dict(meta or {})
        payload.setdefault("timestamp_utc", self._utc_now_iso())
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def log_part(self, item: Dict[str, Any]) -> None:
        payload = dict(item or {})
        payload.setdefault("timestamp_utc", self._utc_now_iso())
        with open(self.parts_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")

    def write_summary(self, summary: Dict[str, Any]) -> None:
        payload = dict(summary or {})
        payload.setdefault("timestamp_utc", self._utc_now_iso())
        payload.setdefault("duration_sec", round(max(0.0, time.time() - self._t0), 3))
        with open(self.summary_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)


def part_name_from_obj(part_obj: Any) -> str:
    part = str(getattr(part_obj, "part", "") or "").strip()
    if part:
        return part
    sym = str(getattr(part_obj, "sym", "") or "").strip()
    if not sym:
        return ""
    return os.path.splitext(os.path.basename(sym))[0].strip()


def safe_emit(cb: Optional[Callable], *args) -> None:
    if cb is None:
        return
    try:
        cb(*args)
    except Exception:
        return


def load_dataset_df(
    dataset_path: str,
    all_cols: Sequence[str],
    nan_fn: Callable[[], float],
) -> pd.DataFrame:
    path = dataset_path
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if not os.path.exists(path):
        return pd.DataFrame(columns=list(all_cols))
    try:
        df = pd.read_csv(path)
    except Exception:
        df = pd.DataFrame(columns=list(all_cols))
    for col in all_cols:
        if col not in df.columns:
            df[col] = "" if col in _TEXT_TRACKING_COLS else nan_fn()
    return df.loc[:, list(all_cols)].copy()


def part_keys_from_df(df: pd.DataFrame) -> set[str]:
    out: set[str] = set()
    if len(df) == 0:
        return out
    for _idx, row in df.iterrows():
        key = make_part_key(
            str(row.get("part_name") or ""),
            str(row.get("pdf_path") or ""),
            str(row.get("dxf_path") or ""),
        )
        if key:
            out.add(key)
    return out
