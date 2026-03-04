# ml_pipeline.py
# Dataset + feature extraction
#
# Design constraints (locked):
# - Dataset is the source of truth.
# - Features must be computable from pdf_path + dxf_path only (no RPD dependency).
# - OCG / PDF layer structure may be used internally to filter noise, but MUST NOT
#   be stored as ML features.
# - No thickness/material in dataset or features.
# - Deterministic, restart-safe: per-row feature failures become NaN; logging continues.
#
# Output dataset:
#   <project>\ml_dataset.csv

from __future__ import annotations

import math
import os
import re
import csv
import json
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import numpy as np
from config import GLOBAL_DATASET_PATH

try:
    from PySide6.QtCore import QObject, QRunnable, Signal
except Exception:
    QObject = object  # type: ignore[assignment]
    QRunnable = object  # type: ignore[assignment]

    class _SignalStub:
        def emit(self, *args, **kwargs) -> None:
            return

    def Signal(*args, **kwargs):  # type: ignore[misc]
        return _SignalStub()

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

try:
    import ezdxf
except Exception:
    ezdxf = None


# -----------------------------
# Paths / schema
# -----------------------------

DATASET_PATH = GLOBAL_DATASET_PATH

TRACKING_COLS = [
    "timestamp_utc",
    "rpd_token",
    "part_name",
    "kit_label",
    "pdf_path",
    "dxf_path",
]

DXF_SIGNAL_COLS = [
    "dxf_perimeter_area_ratio",
    "dxf_concavity_ratio",
    "dxf_internal_void_area_ratio",
    "dxf_entity_count",
    "dxf_arc_length_ratio",
    "dxf_exterior_notch_count",
    "dxf_has_interior_polylines",
    "dxf_color_count",
    "dxf_has_nondefault_color",
]

PDF_SIGNAL_COLS = [
    "pdf_dim_density",
    "pdf_text_to_geom_ratio",
    "pdf_bendline_score",
    "pdf_ink_gradient_mean",
    "pdf_ink_gradient_std",
    "pdf_ink_gradient_max",
]

DXF_FUTURE_COLS = [f"dxf_future_{i:02d}" for i in range(1, 16)]
PDF_FUTURE_COLS = [f"pdf_future_{i:02d}" for i in range(1, 16)]

FEATURE_COLS = DXF_SIGNAL_COLS + PDF_SIGNAL_COLS + DXF_FUTURE_COLS + PDF_FUTURE_COLS

ALL_COLS = TRACKING_COLS + FEATURE_COLS

# ML log extraction workers (feature compute only). File write remains single-writer.
ML_LOG_MAX_WORKERS = max(1, min(8, int(os.environ.get("RK_ML_LOG_WORKERS", "2"))))


def _nan() -> float:
    return float("nan")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_float(x) -> float:
    try:
        if x is None:
            return _nan()
        v = float(x)
        if math.isfinite(v):
            return v
        return _nan()
    except Exception:
        return _nan()


def _safe_int(x) -> float:
    try:
        if x is None:
            return _nan()
        return float(int(x))
    except Exception:
        return _nan()


def _clamp01(x: float) -> float:
    if not math.isfinite(x):
        return _nan()
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _entropy_from_counts(counts: List[int]) -> float:
    total = sum(counts)
    if total <= 0:
        return _nan()
    ent = 0.0
    for c in counts:
        if c <= 0:
            continue
        p = c / total
        ent -= p * math.log(p + 1e-12)
    return ent


# -----------------------------
# Dataset I/O
# -----------------------------

def ensure_dataset_exists() -> None:
    os.makedirs(os.path.dirname(DATASET_PATH), exist_ok=True)
    if not os.path.exists(DATASET_PATH):
        df = pd.DataFrame(columns=ALL_COLS)
        df.to_csv(DATASET_PATH, index=False)


def append_labeled_row(
    part_name: str,
    kit_label: str,
    pdf_path: str,
    dxf_path: str,
    rpd_token: Optional[str] = None,
) -> None:
    """
    Append/Upsert one labeled row to the dataset.

    Global identity key: part_name (basename).
    Behavior: LAST ADDED WINS (if part_name already exists, it is replaced).

    Computes features from pdf_path/dxf_path only.
    """
    ensure_dataset_exists()
    df = pd.read_csv(DATASET_PATH)

    part_name_s = str(part_name or "").strip()
    if not part_name_s:
        return

    # Drop any existing rows for this part_name (last-added-wins)
    if "part_name" in df.columns and len(df) > 0:
        df = df[df["part_name"].astype(str) != part_name_s].copy()

    row = {
        "timestamp_utc": _utc_now_iso(),
        "rpd_token": str(rpd_token or "").strip(),
        "part_name": part_name_s,
        "kit_label": str(kit_label or "").strip(),
        "pdf_path": str(pdf_path or "").strip(),
        "dxf_path": str(dxf_path or "").strip(),
    }

    feats = compute_phase2_signals(row["pdf_path"], row["dxf_path"])
    row.update(feats)

    for c in ALL_COLS:
        if c not in row:
            row[c] = _nan()

    df = pd.concat([df, pd.DataFrame([row], columns=ALL_COLS)], ignore_index=True)
    df.to_csv(DATASET_PATH, index=False)


def _now_local_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def make_run_name(rpd_path: str) -> str:
    base = os.path.splitext(os.path.basename(rpd_path or ""))[0].strip()
    if not base:
        base = "run"
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", base).strip("_")
    return f"MLRun_{base}_{_now_local_stamp()}"


def load_existing_part_names(dataset_path: str = DATASET_PATH) -> set[str]:
    if not dataset_path or not os.path.exists(dataset_path):
        return set()
    out: set[str] = set()
    try:
        with open(dataset_path, newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                part = str(row.get("part_name") or row.get("part") or "").strip()
                if part:
                    out.add(part.upper())
    except Exception:
        return set()
    return out


class ScanLogger:
    def __init__(self, run_dir: str, run_name: str):
        self.run_dir = run_dir
        self.run_name = run_name
        os.makedirs(self.run_dir, exist_ok=True)
        self.meta_path = os.path.join(self.run_dir, f"{self.run_name}.meta.json")
        self.parts_path = os.path.join(self.run_dir, f"{self.run_name}.parts.jsonl")
        self.summary_path = os.path.join(self.run_dir, f"{self.run_name}.summary.json")
        self._t0 = time.time()

    def write_meta(self, meta: Dict[str, Any]) -> None:
        payload = dict(meta or {})
        payload.setdefault("timestamp_utc", _utc_now_iso())
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def log_part(self, item: Dict[str, Any]) -> None:
        payload = dict(item or {})
        payload.setdefault("timestamp_utc", _utc_now_iso())
        with open(self.parts_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")

    def write_summary(self, summary: Dict[str, Any]) -> None:
        payload = dict(summary or {})
        payload.setdefault("timestamp_utc", _utc_now_iso())
        payload.setdefault("duration_sec", round(max(0.0, time.time() - self._t0), 3))
        with open(self.summary_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)


def _part_name_from_obj(p: Any) -> str:
    part = str(getattr(p, "part", "") or "").strip()
    if part:
        return part
    sym = str(getattr(p, "sym", "") or "").strip()
    if not sym:
        return ""
    return os.path.splitext(os.path.basename(sym))[0].strip()


def _safe_emit(cb: Optional[Callable], *args) -> None:
    if cb is None:
        return
    try:
        cb(*args)
    except Exception:
        return


def _load_dataset_df(path: str) -> pd.DataFrame:
    if not path:
        path = DATASET_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if not os.path.exists(path):
        return pd.DataFrame(columns=ALL_COLS)
    try:
        df = pd.read_csv(path)
    except Exception:
        df = pd.DataFrame(columns=ALL_COLS)
    for c in ALL_COLS:
        if c not in df.columns:
            df[c] = _nan()
    return df


def _part_keys_from_df(df: pd.DataFrame) -> set[str]:
    out: set[str] = set()
    if "part_name" not in df.columns or len(df) == 0:
        return out
    for v in df["part_name"]:
        if pd.isna(v):
            continue
        part = str(v).strip()
        if part:
            out.add(part.upper())
    return out


def _compute_ml_log_row(
    task: Dict[str, Any],
    *,
    rpd_token: str,
    signals: List[str],
) -> Dict[str, Any]:
    part_name = str(task.get("part_name") or "").strip()
    kit = str(task.get("kit_label") or "").strip()
    pdf = str(task.get("pdf_path") or "").strip()
    dxf = str(task.get("dxf_path") or "").strip()

    feats = compute_phase2_signals(pdf, dxf)
    row = {
        "timestamp_utc": _utc_now_iso(),
        "rpd_token": str(rpd_token or "").strip(),
        "part_name": part_name,
        "kit_label": kit,
        "pdf_path": pdf,
        "dxf_path": dxf,
    }
    row.update(feats)
    for c in ALL_COLS:
        if c not in row:
            row[c] = _nan()

    sig_vals: Dict[str, float] = {}
    for s in signals:
        v = _safe_float(feats.get(s, 0.0))
        sig_vals[s] = float(v) if math.isfinite(v) else 0.0

    return {
        "row": row,
        "signals": sig_vals,
    }


def run_scan_and_log(
    parts: Iterable[Any],
    rpd_path: str,
    resolve_asset_fn: Callable[[str, str], str],
    sanitize_kit_name_fn: Callable[[str], str],
    balance_kit: str,
    run_dir: str,
    delay_ms: int = 0,
    signal_cols: Optional[List[str]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
    on_part: Optional[Callable[[Dict[str, Any]], None]] = None,
    meta_extra: Optional[Dict[str, Any]] = None,
    max_workers: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Scan parts, append labels/features to dataset, and log run artifacts.

    Returns a summary dict with run_name, dataset_path, run_dir, and row counts.
    """
    ensure_dataset_exists()
    os.makedirs(run_dir, exist_ok=True)

    part_rows = list(parts or [])
    total = len(part_rows)
    run_name = make_run_name(rpd_path)
    dataset_path = DATASET_PATH
    signals = list(signal_cols or (DXF_SIGNAL_COLS + PDF_SIGNAL_COLS))
    rpd_token = os.path.basename(rpd_path or "")
    base_df = _load_dataset_df(dataset_path)
    existing_parts = _part_keys_from_df(base_df)
    workers = int(max_workers if max_workers is not None else ML_LOG_MAX_WORKERS)
    workers = max(1, min(8, workers))

    logger = ScanLogger(run_dir, run_name)
    meta = {
        "run_name": run_name,
        "rpd_path": rpd_path,
        "rpd_token": rpd_token,
        "dataset_path": dataset_path,
        "signal_cols": signals,
        "compute_workers": int(workers),
        "global_dedupe_by_part": True,
        "artificial_delay_ms": int(delay_ms),
    }
    if meta_extra:
        meta.update(meta_extra)
    logger.write_meta(meta)

    counts = {
        "total_rows": int(total),
        "processed_rows": 0,
        "written_rows": 0,
        "skipped_duplicate_rows": 0,
        "missing_pdf_rows": 0,
        "missing_dxf_rows": 0,
        "append_error_rows": 0,
    }
    stopped = False
    done = 0
    _safe_emit(on_progress, done, total)

    tasks: List[Dict[str, Any]] = []
    for p in part_rows:
        if should_stop is not None:
            try:
                if should_stop():
                    stopped = True
                    break
            except Exception:
                pass

        part_name = _part_name_from_obj(p)
        kit_raw = str(getattr(p, "kit_label", "") or "")
        kit = sanitize_kit_name_fn(kit_raw) or balance_kit
        sym_path = str(getattr(p, "sym", "") or "")

        key = part_name.upper()
        if key and key in existing_parts:
            counts["processed_rows"] += 1
            counts["skipped_duplicate_rows"] += 1
            done += 1
            item = {
                "status": "skipped_duplicate",
                "part_name": part_name,
                "kit_label": kit,
                "reason": "dedupe_part_name",
            }
            logger.log_part(item)
            _safe_emit(on_part, item)
            _safe_emit(on_progress, done, total)
            if delay_ms > 0:
                time.sleep(int(delay_ms) / 1000.0)
            continue
        if key:
            existing_parts.add(key)

        pdf = resolve_asset_fn(sym_path, ".pdf") or ""
        dxf = resolve_asset_fn(sym_path, ".dxf") or ""
        pdf_exists = bool(pdf and os.path.exists(pdf))
        dxf_exists = bool(dxf and os.path.exists(dxf))
        if not pdf_exists:
            counts["missing_pdf_rows"] += 1
        if not dxf_exists:
            counts["missing_dxf_rows"] += 1

        tasks.append(
            {
                "part_name": part_name,
                "kit_label": kit,
                "pdf_path": pdf,
                "dxf_path": dxf,
                "pdf_exists": pdf_exists,
                "dxf_exists": dxf_exists,
            }
        )

    rows_to_upsert: List[Dict[str, Any]] = []
    submitted = 0
    processed_nondup = 0
    total_tasks = len(tasks)
    workers = max(1, min(workers, max(1, total_tasks)))

    def _handle_task_result(task: Dict[str, Any], res: Optional[Dict[str, Any]], err: str = "") -> None:
        nonlocal done, processed_nondup
        processed_nondup += 1
        counts["processed_rows"] += 1
        done += 1

        status = "computed"
        signal_vals: Dict[str, float] = {s: 0.0 for s in signals}
        if not err and res is not None:
            try:
                row = dict(res.get("row") or {})
                if row:
                    rows_to_upsert.append(row)
            except Exception:
                err = "row_build_failed"
            try:
                sig_obj = res.get("signals")
                if isinstance(sig_obj, dict):
                    signal_vals = {}
                    for s in signals:
                        v = _safe_float(sig_obj.get(s, 0.0))
                        signal_vals[s] = float(v) if math.isfinite(v) else 0.0
            except Exception:
                signal_vals = {s: 0.0 for s in signals}
        if err:
            status = "append_error"
            counts["append_error_rows"] += 1

        item = {
            "status": status,
            "part_name": str(task.get("part_name") or ""),
            "kit_label": str(task.get("kit_label") or ""),
            "pdf_path": str(task.get("pdf_path") or ""),
            "dxf_path": str(task.get("dxf_path") or ""),
            "pdf_exists": bool(task.get("pdf_exists", False)),
            "dxf_exists": bool(task.get("dxf_exists", False)),
            "signals": signal_vals,
            "error": str(err or ""),
        }
        logger.log_part(item)
        _safe_emit(on_part, item)
        _safe_emit(on_progress, done, total)
        if delay_ms > 0:
            time.sleep(int(delay_ms) / 1000.0)

    if not stopped and total_tasks > 0:
        if workers <= 1:
            for task in tasks:
                if should_stop is not None:
                    try:
                        if should_stop():
                            stopped = True
                            break
                    except Exception:
                        pass
                err = ""
                res = None
                try:
                    res = _compute_ml_log_row(task, rpd_token=rpd_token, signals=signals)
                except Exception as e:
                    err = str(e)
                _handle_task_result(task, res, err)
                submitted += 1
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                in_flight: Dict[Any, Dict[str, Any]] = {}

                def _submit_next() -> bool:
                    nonlocal submitted
                    if submitted >= total_tasks:
                        return False
                    task = tasks[submitted]
                    submitted += 1
                    fut = pool.submit(_compute_ml_log_row, task, rpd_token=rpd_token, signals=signals)
                    in_flight[fut] = task
                    return True

                for _ in range(min(workers, total_tasks)):
                    _submit_next()

                while in_flight:
                    if should_stop is not None:
                        try:
                            if should_stop():
                                stopped = True
                                break
                        except Exception:
                            pass

                    done_set, _ = wait(set(in_flight.keys()), timeout=0.2, return_when=FIRST_COMPLETED)
                    if not done_set:
                        continue
                    for fut in done_set:
                        task = in_flight.pop(fut)
                        err = ""
                        res = None
                        try:
                            res = fut.result()
                        except Exception as e:
                            err = str(e)
                        _handle_task_result(task, res, err)
                        if not stopped:
                            _submit_next()

    if rows_to_upsert:
        try:
            part_names = {
                str(r.get("part_name") or "").strip()
                for r in rows_to_upsert
                if str(r.get("part_name") or "").strip()
            }
            if part_names and "part_name" in base_df.columns and len(base_df) > 0:
                base_df = base_df[
                    ~base_df["part_name"].astype(str).isin(part_names)
                ].copy()
            up_df = pd.DataFrame(rows_to_upsert, columns=ALL_COLS)
            merged = pd.concat([base_df, up_df], ignore_index=True)
            merged.to_csv(dataset_path, index=False)
            counts["written_rows"] = int(len(rows_to_upsert))
        except Exception as e:
            counts["append_error_rows"] += int(len(rows_to_upsert))
            logger.log_part(
                {
                    "status": "append_error",
                    "part_name": "",
                    "kit_label": "",
                    "reason": "dataset_write_failed",
                    "error": str(e),
                }
            )

    summary = {
        **counts,
        "stopped": bool(stopped),
        "workers": int(workers),
        "run_name": run_name,
        "dataset_path": dataset_path,
        "run_dir": run_dir,
    }
    logger.write_summary(summary)
    return summary


def recompute_dataset_signals(
    dataset_path: str = DATASET_PATH,
    signal_cols: Optional[List[str]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, Any]:
    """
    Recompute ML feature columns for every existing row in dataset_path.

    Uses each row's stored pdf_path and dxf_path so rows outside the active
    project are included.
    """
    if not dataset_path:
        dataset_path = DATASET_PATH
    os.makedirs(os.path.dirname(dataset_path) or ".", exist_ok=True)
    if not os.path.exists(dataset_path):
        pd.DataFrame(columns=ALL_COLS).to_csv(dataset_path, index=False)

    df = pd.read_csv(dataset_path)
    if len(df) == 0:
        return {
            "dataset_path": dataset_path,
            "total_rows": 0,
            "processed_rows": 0,
            "updated_rows": 0,
            "error_rows": 0,
            "missing_pdf_rows": 0,
            "missing_dxf_rows": 0,
            "stopped": False,
        }

    for c in ALL_COLS:
        if c not in df.columns:
            df[c] = _nan()

    signals = list(signal_cols or (DXF_SIGNAL_COLS + PDF_SIGNAL_COLS))
    signals = [s for s in signals if s in FEATURE_COLS]
    if not signals:
        signals = list(DXF_SIGNAL_COLS + PDF_SIGNAL_COLS)

    total = int(len(df))
    processed = 0
    updated = 0
    errors = 0
    missing_pdf = 0
    missing_dxf = 0
    stopped = False
    _safe_emit(on_progress, 0, total)

    for idx in range(total):
        if should_stop is not None:
            try:
                if should_stop():
                    stopped = True
                    break
            except Exception:
                pass

        pdf_raw = df.at[idx, "pdf_path"] if "pdf_path" in df.columns else ""
        dxf_raw = df.at[idx, "dxf_path"] if "dxf_path" in df.columns else ""
        pdf_path = "" if pd.isna(pdf_raw) else str(pdf_raw).strip()
        dxf_path = "" if pd.isna(dxf_raw) else str(dxf_raw).strip()
        if not (pdf_path and os.path.exists(pdf_path)):
            missing_pdf += 1
        if not (dxf_path and os.path.exists(dxf_path)):
            missing_dxf += 1

        try:
            feats = compute_phase2_signals(pdf_path, dxf_path)
            for s in signals:
                df.at[idx, s] = _safe_float(feats.get(s, _nan()))
            updated += 1
        except Exception:
            errors += 1

        processed += 1
        _safe_emit(on_progress, processed, total)

    # Persist partial progress if canceled.
    df.to_csv(dataset_path, index=False)
    return {
        "dataset_path": dataset_path,
        "total_rows": total,
        "processed_rows": processed,
        "updated_rows": updated,
        "error_rows": errors,
        "missing_pdf_rows": missing_pdf,
        "missing_dxf_rows": missing_dxf,
        "stopped": bool(stopped),
    }



# -----------------------------
# Feature extraction entrypoint
# -----------------------------

def compute_phase2_signals(pdf_path: str, dxf_path: str) -> Dict[str, float]:
    feats: Dict[str, float] = {c: _nan() for c in FEATURE_COLS}

    try:
        feats.update(_compute_dxf_features(dxf_path))
    except Exception:
        pass

    try:
        feats.update(_compute_pdf_features_vector(pdf_path))
    except Exception:
        pass

    return feats


# -----------------------------
# DXF features (vector)
# -----------------------------

def _iter_dxf_entities(doc) -> Iterable:
    msp = doc.modelspace()
    for e in msp:
        yield e


def _layer_aci_color(doc, layer_name: str) -> Optional[int]:
    try:
        if not layer_name:
            return None
        layer = doc.layers.get(layer_name)
        if layer is None:
            return None
        if not layer.dxf.hasattr("color"):
            return None
        return abs(int(layer.dxf.color))
    except Exception:
        return None


def _entity_effective_color_key(e, doc) -> Optional[str]:
    # Prefer true-color when present.
    try:
        if hasattr(e, "dxf") and e.dxf.hasattr("true_color"):
            tc = int(e.dxf.true_color) & 0xFFFFFF
            if tc > 0:
                return f"rgb:{tc:06X}"
    except Exception:
        pass

    aci: Optional[int] = None
    try:
        if hasattr(e, "dxf") and e.dxf.hasattr("color"):
            aci = int(e.dxf.color)
    except Exception:
        aci = None

    # Resolve BYLAYER / BYBLOCK through layer color when possible.
    if aci in (None, 0, 256):
        layer_name = ""
        try:
            layer_name = str(e.dxf.layer or "")
        except Exception:
            layer_name = ""
        layer_aci = _layer_aci_color(doc, layer_name)
        if layer_aci is not None:
            aci = layer_aci

    if aci is None:
        return None
    return f"aci:{abs(int(aci))}"


def _color_key_is_nondefault(color_key: str) -> bool:
    if color_key.startswith("rgb:"):
        try:
            rgb = int(color_key.split(":", 1)[1], 16)
        except Exception:
            return False
        # Treat white / black / gray as default-ish; anything else counts as non-default.
        return rgb not in (0x000000, 0xFFFFFF, 0xBFBFBF, 0xC0C0C0, 0x808080)
    if color_key.startswith("aci:"):
        try:
            aci = int(color_key.split(":", 1)[1])
        except Exception:
            return False
        return aci not in (0, 7, 256)
    return False


def _polyline_points(e) -> Optional[List[Tuple[float, float]]]:
    t = e.dxftype()
    try:
        if t == "LINE":
            s = e.dxf.start
            en = e.dxf.end
            return [(float(s.x), float(s.y)), (float(en.x), float(en.y))]
        if t == "LWPOLYLINE":
            pts = [(float(x), float(y)) for (x, y, *_rest) in e.get_points()]
            return pts
        if t == "POLYLINE":
            pts = []
            for v in e.vertices():
                pts.append((float(v.dxf.location.x), float(v.dxf.location.y)))
            return pts
    except Exception:
        return None
    return None


def _segments_from_points(pts: List[Tuple[float, float]], closed: bool) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
    segs = []
    for i in range(len(pts) - 1):
        segs.append((pts[i], pts[i + 1]))
    if closed and len(pts) >= 3:
        segs.append((pts[-1], pts[0]))
    return segs


def _arc_span_deg(start_deg: float, end_deg: float) -> float:
    span = (float(end_deg) - float(start_deg)) % 360.0
    if span <= 1e-9:
        span = 360.0
    return span


def _points_close(a: Tuple[float, float], b: Tuple[float, float], tol: float) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _dedupe_consecutive_points(pts: List[Tuple[float, float]], tol: float) -> List[Tuple[float, float]]:
    if not pts:
        return []
    out = [pts[0]]
    for p in pts[1:]:
        if not _points_close(out[-1], p, tol):
            out.append(p)
    return out


def _stitch_open_paths_to_closed_loops(open_polys: List[List[Tuple[float, float]]]) -> List[List[Tuple[float, float]]]:
    segs: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    cloud: List[Tuple[float, float]] = []
    for p in open_polys:
        if len(p) < 2:
            continue
        cloud.extend(p)
        segs.extend(_segments_from_points(p, closed=False))
    segs = [(a, b) for (a, b) in segs if _seg_len(a, b) > 1e-9]
    if not segs:
        return []

    if len(cloud) >= 2:
        x0, y0, x1, y1 = _bbox_from_points(cloud)
        diag = math.hypot(x1 - x0, y1 - y0)
    else:
        diag = 0.0
    tol = max(1e-4, 1e-4 * diag)

    loops: List[List[Tuple[float, float]]] = []
    unused = list(segs)
    while unused:
        a, b = unused.pop()
        path: List[Tuple[float, float]] = [a, b]

        made_progress = True
        while made_progress and unused:
            made_progress = False
            for idx, (p0, p1) in enumerate(unused):
                if _points_close(path[-1], p0, tol):
                    path.append(p1)
                elif _points_close(path[-1], p1, tol):
                    path.append(p0)
                elif _points_close(path[0], p1, tol):
                    path.insert(0, p0)
                elif _points_close(path[0], p0, tol):
                    path.insert(0, p1)
                else:
                    continue
                unused.pop(idx)
                made_progress = True
                break

        if len(path) < 4:
            continue
        if not _points_close(path[0], path[-1], tol):
            continue
        path = path[:-1]
        path = _dedupe_consecutive_points(path, tol)
        if len(path) >= 3:
            loops.append(path)
    return loops


def _seg_len(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _seg_angle_deg(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
    ang = abs(ang) % 180.0
    return ang


def _is_ortho(angle_deg: float, tol: float = 5.0) -> bool:
    return (min(abs(angle_deg - 0.0), abs(angle_deg - 180.0)) <= tol) or (abs(angle_deg - 90.0) <= tol)


def _bbox_from_points(pts: List[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_aspect(x0: float, y0: float, x1: float, y1: float) -> float:
    w = max(1e-9, x1 - x0)
    h = max(1e-9, y1 - y0)
    return w / h if w >= h else h / w


def _circle_points(cx: float, cy: float, r: float, steps: int = 48) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    n = max(12, int(steps))
    for i in range(n):
        t = 2.0 * math.pi * (i / n)
        out.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    return out


def _poly_area_abs(pts: List[Tuple[float, float]]) -> float:
    if len(pts) < 3:
        return 0.0
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(0.5 * s)


def _poly_perimeter(pts: List[Tuple[float, float]]) -> float:
    if len(pts) < 2:
        return 0.0
    return sum(_seg_len(a, b) for (a, b) in _segments_from_points(pts, closed=True))


def _poly_area_signed(pts: List[Tuple[float, float]]) -> float:
    if len(pts) < 3:
        return 0.0
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return 0.5 * s


def _convex_hull(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    pts = sorted(set(points))
    if len(pts) <= 1:
        return pts

    def cross(o, a, b) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: List[Tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper: List[Tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    return lower[:-1] + upper[:-1]


def _point_in_poly(point: Tuple[float, float], poly: List[Tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    n = len(poly)
    if n < 3:
        return False
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        intersects = ((y1 > y) != (y2 > y))
        if intersects:
            x_hit = (x2 - x1) * (y - y1) / max(1e-12, (y2 - y1)) + x1
            if x < x_hit:
                inside = not inside
    return inside


def _compute_dxf_features(dxf_path: str) -> Dict[str, float]:
    out = {k: _nan() for k in DXF_SIGNAL_COLS}

    if not dxf_path or not os.path.exists(dxf_path) or ezdxf is None:
        return out

    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception:
        return out

    entities = list(_iter_dxf_entities(doc))
    out["dxf_entity_count"] = _safe_int(len(entities))

    colors: set[str] = set()
    has_nondefault_color = 0
    for e in entities:
        ck = _entity_effective_color_key(e, doc)
        if ck is None:
            continue
        colors.add(ck)
        if _color_key_is_nondefault(ck):
            has_nondefault_color = 1
    out["dxf_color_count"] = _safe_int(len(colors))
    out["dxf_has_nondefault_color"] = _safe_int(has_nondefault_color)

    closed_loops: List[List[Tuple[float, float]]] = []
    open_polys: List[List[Tuple[float, float]]] = []
    cloud_points: List[Tuple[float, float]] = []
    total_geom_len = 0.0
    arc_geom_len = 0.0

    for e in entities:
        t = e.dxftype()
        if t == "CIRCLE":
            try:
                c = e.dxf.center
                r = abs(float(e.dxf.radius))
                if r > 1e-9:
                    cpts = _circle_points(float(c.x), float(c.y), r)
                    cloud_points.extend(cpts)
                    closed_loops.append(cpts)
                    circ_len = 2.0 * math.pi * r
                    total_geom_len += circ_len
                    arc_geom_len += circ_len
            except Exception:
                pass
            continue

        if t == "ARC":
            try:
                r = abs(float(e.dxf.radius))
                if r > 1e-9:
                    span_deg = _arc_span_deg(float(e.dxf.start_angle), float(e.dxf.end_angle))
                    alen = math.radians(span_deg) * r
                    total_geom_len += alen
                    arc_geom_len += alen
            except Exception:
                pass
            continue

        pts = _polyline_points(e)
        if not pts or len(pts) < 2:
            continue
        cloud_points.extend(pts)
        is_closed = False
        try:
            if t == "LWPOLYLINE":
                is_closed = bool(e.closed)
            elif t == "POLYLINE":
                is_closed = bool(e.is_closed)
        except Exception:
            is_closed = False

        segs_here = _segments_from_points(pts, closed=is_closed and len(pts) >= 3)
        total_geom_len += sum(_seg_len(a, b) for (a, b) in segs_here)

        if is_closed and len(pts) >= 3:
            closed_loops.append(pts)
        else:
            open_polys.append(pts)

    if total_geom_len > 1e-9:
        out["dxf_arc_length_ratio"] = _safe_float(arc_geom_len / total_geom_len)
    else:
        out["dxf_arc_length_ratio"] = _safe_float(0.0)

    if not closed_loops and open_polys:
        closed_loops = _stitch_open_paths_to_closed_loops(open_polys)

    # Fallback when no closed loops are available: estimate using convex hull.
    if not closed_loops and len(cloud_points) >= 3:
        hull = _convex_hull(cloud_points)
        hull_area = _poly_area_abs(hull)
        hull_perim = _poly_perimeter(hull)
        if hull_area > 1e-9 and hull_perim > 1e-9:
            out["dxf_perimeter_area_ratio"] = _safe_float(hull_perim / hull_area)
            out["dxf_concavity_ratio"] = _safe_float(0.0)  # hull is fully convex by definition
            out["dxf_internal_void_area_ratio"] = _safe_float(0.0)
            out["dxf_has_interior_polylines"] = _safe_int(0)
            out["dxf_exterior_notch_count"] = _safe_int(0)
            return out

    # Last-chance fallback for very thin/degenerate geometry: bbox proxy ratio.
    if not closed_loops and len(cloud_points) >= 2:
        x0, y0, x1, y1 = _bbox_from_points(cloud_points)
        w = max(0.0, x1 - x0)
        h = max(0.0, y1 - y0)
        bbox_area = w * h
        bbox_perim = 2.0 * (w + h)
        if bbox_area > 1e-9 and bbox_perim > 1e-9:
            out["dxf_perimeter_area_ratio"] = _safe_float(bbox_perim / bbox_area)
            out["dxf_concavity_ratio"] = _safe_float(0.0)
            out["dxf_internal_void_area_ratio"] = _safe_float(0.0)
            out["dxf_has_interior_polylines"] = _safe_int(0)
            out["dxf_exterior_notch_count"] = _safe_int(0)
            return out

    if not closed_loops:
        out["dxf_perimeter_area_ratio"] = _safe_float(0.0)
        out["dxf_concavity_ratio"] = _safe_float(0.0)
        out["dxf_internal_void_area_ratio"] = _safe_float(0.0)
        out["dxf_has_interior_polylines"] = _safe_int(0)
        out["dxf_exterior_notch_count"] = _safe_int(0)
        return out

    loop_areas = [(float(_poly_area_abs(lp)), lp) for lp in closed_loops]
    loop_areas.sort(key=lambda t: t[0], reverse=True)
    outer_area, outer_loop = loop_areas[0]
    outer_perim = _poly_perimeter(outer_loop)

    if outer_area > 1e-9:
        out["dxf_perimeter_area_ratio"] = _safe_float(outer_perim / outer_area)
        void_area = sum(a for (a, _lp) in loop_areas[1:])
        out["dxf_internal_void_area_ratio"] = _safe_float(void_area / outer_area)
    else:
        # Degenerate "closed" loop; fall back to hull proxy if possible.
        hull = _convex_hull(cloud_points)
        hull_area = _poly_area_abs(hull)
        hull_perim = _poly_perimeter(hull)
        if hull_area > 1e-9 and hull_perim > 1e-9:
            out["dxf_perimeter_area_ratio"] = _safe_float(hull_perim / hull_area)
        else:
            out["dxf_perimeter_area_ratio"] = _safe_float(0.0)
        out["dxf_internal_void_area_ratio"] = _safe_float(0.0)

    hull = _convex_hull(cloud_points)
    hull_area = _poly_area_abs(hull)
    if outer_area > 1e-9 and hull_area > 1e-9:
        convexity = outer_area / hull_area
        out["dxf_concavity_ratio"] = _safe_float(_clamp01(1.0 - convexity))
    else:
        out["dxf_concavity_ratio"] = _safe_float(0.0)

    interior_flag = 1 if len(loop_areas) > 1 else 0
    if interior_flag == 0 and outer_loop:
        for p in open_polys:
            if len(p) < 2:
                continue
            if len(p) >= 3:
                cx = sum(pt[0] for pt in p) / len(p)
                cy = sum(pt[1] for pt in p) / len(p)
            else:
                cx = 0.5 * (p[0][0] + p[-1][0])
                cy = 0.5 * (p[0][1] + p[-1][1])
            if _point_in_poly((cx, cy), outer_loop):
                interior_flag = 1
                break
    out["dxf_has_interior_polylines"] = _safe_int(interior_flag)

    x0, y0, x1, y1 = _bbox_from_points(outer_loop)
    bbox_diag = math.hypot(x1 - x0, y1 - y0)
    short_thr = 0.03 * bbox_diag if bbox_diag > 1e-9 else 0.0
    notch_count = 0
    segs = _segments_from_points(outer_loop, closed=True)
    n = len(segs)
    for i in range(n):
        a0, b0 = segs[i - 1]
        a1, b1 = segs[i]
        l0 = _seg_len(a0, b0)
        l1 = _seg_len(a1, b1)
        if short_thr <= 0 or (l0 > short_thr and l1 > short_thr):
            continue
        ang0 = _seg_angle_deg(a0, b0)
        ang1 = _seg_angle_deg(a1, b1)
        diff = abs(ang1 - ang0) % 180.0
        diff = min(diff, 180.0 - diff)
        if 70.0 <= diff <= 110.0:
            notch_count += 1
    if notch_count == 0:
        # Fallback proxy: count concave near-90 corners and fold pairs into notch count.
        signed_area = _poly_area_signed(outer_loop)
        if abs(signed_area) > 1e-12 and len(outer_loop) >= 4:
            ccw = signed_area > 0.0
            concave_ortho = 0
            m = len(outer_loop)
            for i in range(m):
                p_prev = outer_loop[i - 1]
                p_cur = outer_loop[i]
                p_next = outer_loop[(i + 1) % m]
                v1x = p_cur[0] - p_prev[0]
                v1y = p_cur[1] - p_prev[1]
                v2x = p_next[0] - p_cur[0]
                v2y = p_next[1] - p_cur[1]
                if math.hypot(v1x, v1y) <= 1e-9 or math.hypot(v2x, v2y) <= 1e-9:
                    continue
                cross = v1x * v2y - v1y * v2x
                is_concave = (cross < -1e-12) if ccw else (cross > 1e-12)
                if not is_concave:
                    continue
                ang0 = _seg_angle_deg(p_prev, p_cur)
                ang1 = _seg_angle_deg(p_cur, p_next)
                diff = abs(ang1 - ang0) % 180.0
                diff = min(diff, 180.0 - diff)
                if 70.0 <= diff <= 110.0:
                    concave_ortho += 1
            if concave_ortho >= 2:
                notch_count = max(notch_count, concave_ortho // 2)
    out["dxf_exterior_notch_count"] = _safe_int(notch_count)

    # Keep core geometry ratios numeric for downstream tooling / CSV inspection.
    for col in ("dxf_perimeter_area_ratio", "dxf_concavity_ratio", "dxf_internal_void_area_ratio"):
        v = _safe_float(out.get(col))
        out[col] = float(v) if math.isfinite(v) else 0.0

    return out


# -----------------------------
# PDF features (vector, OCG filter internal)
# -----------------------------

_LAYER_KEEP_PATTERNS = [
    re.compile(r"\bvisible\b", re.IGNORECASE),
    re.compile(r"\bdimension\b", re.IGNORECASE),
    re.compile(r"\bsymbol\b", re.IGNORECASE),
    re.compile(r"\bbend\b", re.IGNORECASE),
    re.compile(r"\bcenterline\b", re.IGNORECASE),
    re.compile(r"\bcentreline\b", re.IGNORECASE),
]


def _page_area(page) -> float:
    r = page.rect
    return max(1e-9, float(r.width) * float(r.height))


def _bbox_union(boxes: List[Tuple[float, float, float, float]]) -> Optional[Tuple[float, float, float, float]]:
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    return (x0, y0, x1, y1)


def _bbox_aspect_from_box(b: Tuple[float, float, float, float]) -> float:
    w = max(1e-9, b[2] - b[0])
    h = max(1e-9, b[3] - b[1])
    return w / h if w >= h else h / w


def _enable_only_kept_layers(doc) -> None:
    try:
        oc = doc.get_oc()
    except Exception:
        return
    if not oc:
        return
    try:
        ocgs = oc.get("ocgs", None)
        if not ocgs:
            return
        keep_ids = set()
        for g in ocgs:
            name = str(g.get("name", "") or "")
            if any(p.search(name) for p in _LAYER_KEEP_PATTERNS):
                if "xref" in g:
                    keep_ids.add(int(g["xref"]))
        if not keep_ids:
            return
        state = {}
        for g in ocgs:
            xref = int(g.get("xref", 0) or 0)
            if xref <= 0:
                continue
            state[xref] = (xref in keep_ids)
        doc.set_oc(state)
    except Exception:
        return


def _compute_pdf_features_vector(pdf_path: str) -> Dict[str, float]:
    out = {k: _nan() for k in PDF_SIGNAL_COLS}

    if not pdf_path or not os.path.exists(pdf_path) or fitz is None:
        return out

    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return out

    try:
        if doc.page_count < 1:
            return out
        page = doc.load_page(0)
        page_rect = page.rect
        page_area = _page_area(page)

        def _layer_matches(layer: str, *tokens: str) -> bool:
            s = str(layer or "").lower()
            return any(t in s for t in tokens)

        drawings = []
        try:
            drawings = page.get_drawings()
        except Exception:
            drawings = []

        total_geom = 0
        dim_geom = 0
        bend_geom = 0
        geom_boxes: List[Tuple[float, float, float, float]] = []

        for d in drawings:
            items = d.get("items", []) or []
            geom_count = max(1, len(items))
            total_geom += geom_count
            layer = str(d.get("layer") or "")
            if _layer_matches(layer, "dimension", "dim"):
                dim_geom += geom_count
            if _layer_matches(layer, "bend", "centerline", "centreline"):
                bend_geom += geom_count
            r = d.get("rect", None)
            if r is not None:
                try:
                    geom_boxes.append((float(r.x0), float(r.y0), float(r.x1), float(r.y1)))
                except Exception:
                    pass

        total_chars = 0
        dim_chars = 0
        try:
            traces = page.get_texttrace()
        except Exception:
            traces = []
        for t in traces:
            chars = t.get("chars", []) or []
            n_chars = 0
            for ch in chars:
                if isinstance(ch, (list, tuple)) and ch:
                    n_chars += 1
            if n_chars <= 0:
                continue
            total_chars += n_chars
            layer = str(t.get("layer") or "")
            if _layer_matches(layer, "dimension", "dim"):
                dim_chars += n_chars

        out["pdf_text_to_geom_ratio"] = _safe_float(total_chars / max(1.0, float(total_geom)))
        out["pdf_bendline_score"] = _safe_float(bend_geom / max(1.0, float(total_geom)))

        dim_weight = float(dim_geom) + float(dim_chars)
        out["pdf_dim_density"] = _safe_float(dim_weight / max(1.0, page_area / 1000.0))

        grid_n = 12
        grads: List[float] = []
        if geom_boxes and float(page_rect.width) > 1e-9 and float(page_rect.height) > 1e-9:
            cell_w = float(page_rect.width) / grid_n
            cell_h = float(page_rect.height) / grid_n
            dens = [[0.0 for _ in range(grid_n)] for _ in range(grid_n)]

            for bb in geom_boxes:
                x0 = max(float(page_rect.x0), min(float(page_rect.x1), bb[0]))
                y0 = max(float(page_rect.y0), min(float(page_rect.y1), bb[1]))
                x1 = max(float(page_rect.x0), min(float(page_rect.x1), bb[2]))
                y1 = max(float(page_rect.y0), min(float(page_rect.y1), bb[3]))
                if x1 <= x0 or y1 <= y0:
                    continue
                ix0 = max(0, min(grid_n - 1, int((x0 - float(page_rect.x0)) / cell_w)))
                iy0 = max(0, min(grid_n - 1, int((y0 - float(page_rect.y0)) / cell_h)))
                ix1 = max(0, min(grid_n - 1, int((x1 - float(page_rect.x0)) / cell_w)))
                iy1 = max(0, min(grid_n - 1, int((y1 - float(page_rect.y0)) / cell_h)))
                for iy in range(iy0, iy1 + 1):
                    for ix in range(ix0, ix1 + 1):
                        dens[iy][ix] += 1.0

            for iy in range(grid_n):
                for ix in range(grid_n):
                    v = dens[iy][ix]
                    if ix + 1 < grid_n:
                        grads.append(abs(dens[iy][ix + 1] - v))
                    if iy + 1 < grid_n:
                        grads.append(abs(dens[iy + 1][ix] - v))

        if grads:
            g = np.asarray(grads, dtype=float)
            out["pdf_ink_gradient_mean"] = _safe_float(float(np.mean(g)))
            out["pdf_ink_gradient_std"] = _safe_float(float(np.std(g, ddof=1 if g.size > 1 else 0)))
            out["pdf_ink_gradient_max"] = _safe_float(float(np.max(g)))
    finally:
        doc.close()

    return out
