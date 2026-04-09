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
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import numpy as np
from config import GLOBAL_DATASET_PATH
from ml_dxf_features import compute_dxf_features as _compute_dxf_features_impl
from ml_dataset_store import (
    ScanLogger,
    append_labeled_row as _append_labeled_row_impl,
    ensure_dataset_exists as _ensure_dataset_exists_impl,
    load_dataset_df as _load_dataset_df_impl,
    load_existing_part_names as _load_existing_part_names_impl,
    make_part_key as _make_part_key_impl,
    make_run_name as _make_run_name_impl,
    now_local_stamp as _now_local_stamp_impl,
    part_keys_from_df as _part_keys_from_df_impl,
    part_name_from_obj as _part_name_from_obj_impl,
    safe_emit as _safe_emit_impl,
)
from ml_pdf_features import compute_pdf_features_vector as _compute_pdf_features_vector_impl

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
    "part_key",
    "kit_label",
    "pdf_path",
    "dxf_path",
]

DXF_SIGNAL_COLS = [
    "dxf_perimeter_area_ratio",
    "dxf_internal_void_area_ratio",
    "dxf_entity_count",
    "dxf_arc_count",
    "dxf_bbox_aspect_ratio",
    "dxf_fill_ratio",
    "dxf_edge_length_cv",
    "dxf_edge_band_entity_ratio",
    "dxf_arc_length_ratio",
    "dxf_exterior_notch_count",
    "dxf_has_interior_polylines",
    "dxf_color_count",
]

PDF_SIGNAL_COLS = [
    "pdf_dim_density",
    "pdf_red_dim_density",
    "pdf_bendline_score",
    "pdf_bendline_entity_density",
]

DXF_FUTURE_COLS = [f"dxf_future_{i:02d}" for i in range(1, 16)]
PDF_FUTURE_COLS = [f"pdf_future_{i:02d}" for i in range(1, 16)]

FEATURE_COLS = DXF_SIGNAL_COLS + PDF_SIGNAL_COLS + DXF_FUTURE_COLS + PDF_FUTURE_COLS

ALL_COLS = TRACKING_COLS + FEATURE_COLS

# ML log extraction workers (feature compute only). File write remains single-writer.
ML_LOG_MAX_WORKERS = 2
# ML recompute workers (feature compute only). File write remains single-writer.
ML_RECOMPUTE_MAX_WORKERS = 2


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


def _format_error(exc: Exception) -> str:
    name = type(exc).__name__
    text = str(exc or "").strip()
    return f"{name}: {text}" if text else name


def _join_feature_errors(feature_errors: Dict[str, str]) -> str:
    if not feature_errors:
        return ""
    parts = []
    for key in ("dxf", "pdf"):
        msg = str(feature_errors.get(key) or "").strip()
        if msg:
            parts.append(f"{key.upper()}: {msg}")
    return "; ".join(parts)


def _append_example(values: List[str], text: str, limit: int = 5) -> None:
    item = str(text or "").strip()
    if not item or len(values) >= int(limit):
        return
    values.append(item)


# -----------------------------
# Dataset I/O
# -----------------------------

def ensure_dataset_exists() -> None:
    _ensure_dataset_exists_impl(DATASET_PATH, ALL_COLS)


def append_labeled_row(
    part_name: str,
    kit_label: str,
    pdf_path: str,
    dxf_path: str,
    rpd_token: Optional[str] = None,
) -> None:
    _append_labeled_row_impl(
        part_name,
        kit_label,
        pdf_path,
        dxf_path,
        dataset_path=DATASET_PATH,
        all_cols=ALL_COLS,
        compute_signals_fn=compute_phase2_signals,
        nan_fn=_nan,
        utc_now_iso_fn=_utc_now_iso,
        rpd_token=rpd_token,
    )


def _now_local_stamp() -> str:
    return _now_local_stamp_impl()


def make_run_name(rpd_path: str) -> str:
    return _make_run_name_impl(rpd_path, stamp_fn=_now_local_stamp)


def load_existing_part_names(dataset_path: str = DATASET_PATH) -> set[str]:
    return _load_existing_part_names_impl(dataset_path)


def _part_name_from_obj(p: Any) -> str:
    return _part_name_from_obj_impl(p)


def _safe_emit(cb: Optional[Callable], *args) -> None:
    _safe_emit_impl(cb, *args)


def _load_dataset_df(path: str) -> pd.DataFrame:
    return _load_dataset_df_impl(path or DATASET_PATH, ALL_COLS, _nan)


def _part_keys_from_df(df: pd.DataFrame) -> set[str]:
    return _part_keys_from_df_impl(df)


def _make_part_key(part_name: str, pdf_path: str, dxf_path: str) -> str:
    return _make_part_key_impl(part_name, pdf_path, dxf_path)


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

    feats, feature_errors = _compute_phase2_signals_detail(pdf, dxf)
    row = {
        "timestamp_utc": _utc_now_iso(),
        "rpd_token": str(rpd_token or "").strip(),
        "part_name": part_name,
        "part_key": _make_part_key(part_name, pdf, dxf),
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
        "feature_errors": feature_errors,
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
        "skipped_missing_pdf_rows": 0,
        "missing_pdf_rows": 0,
        "missing_dxf_rows": 0,
        "feature_error_rows": 0,
        "dxf_feature_error_rows": 0,
        "pdf_feature_error_rows": 0,
        "append_error_rows": 0,
    }
    warning_examples: List[str] = []
    error_examples: List[str] = []
    missing_pdf_examples: List[str] = []
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

        pdf = resolve_asset_fn(sym_path, ".pdf") or ""
        pdf_exists = bool(pdf and os.path.exists(pdf))
        if not pdf_exists:
            counts["missing_pdf_rows"] += 1
            counts["skipped_missing_pdf_rows"] += 1
            counts["processed_rows"] += 1
            done += 1
            item = {
                "status": "skipped_missing_pdf",
                "part_name": part_name,
                "kit_label": kit,
                "pdf_path": str(pdf or ""),
                "reason": "missing_pdf",
            }
            _append_example(missing_pdf_examples, f"{part_name or '(unnamed)'} -> {str(pdf or '').strip() or '(missing path)'}")
            logger.log_part(item)
            _safe_emit(on_part, item)
            _safe_emit(on_progress, done, total)
            if delay_ms > 0:
                time.sleep(int(delay_ms) / 1000.0)
            continue

        dxf = resolve_asset_fn(sym_path, ".dxf") or ""
        dxf_exists = bool(dxf and os.path.exists(dxf))
        if not dxf_exists:
            counts["missing_dxf_rows"] += 1

        part_key = _make_part_key(part_name, pdf, dxf)
        if part_key and part_key in existing_parts:
            counts["processed_rows"] += 1
            counts["skipped_duplicate_rows"] += 1
            done += 1
            item = {
                "status": "skipped_duplicate",
                "part_name": part_name,
                "part_key": part_key,
                "kit_label": kit,
                "pdf_path": str(pdf or ""),
                "dxf_path": str(dxf or ""),
                "reason": "dedupe_part_key",
            }
            logger.log_part(item)
            _safe_emit(on_part, item)
            _safe_emit(on_progress, done, total)
            if delay_ms > 0:
                time.sleep(int(delay_ms) / 1000.0)
            continue
        if part_key:
            existing_parts.add(part_key)

        tasks.append(
            {
                "part_name": part_name,
                "part_key": part_key,
                "kit_label": kit,
                "pdf_path": pdf,
                "dxf_path": dxf,
                "pdf_exists": pdf_exists,
                "dxf_exists": dxf_exists,
            }
        )

    rows_to_upsert: List[Dict[str, Any]] = []
    submitted = 0
    total_tasks = len(tasks)
    workers = max(1, min(workers, max(1, total_tasks)))

    def _handle_task_result(task: Dict[str, Any], res: Optional[Dict[str, Any]], err: str = "") -> None:
        nonlocal done
        counts["processed_rows"] += 1
        done += 1

        status = "computed"
        signal_vals: Dict[str, float] = {s: 0.0 for s in signals}
        feature_errors: Dict[str, str] = {}
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
            try:
                err_obj = res.get("feature_errors")
                if isinstance(err_obj, dict):
                    feature_errors = {
                        str(k): str(v).strip()
                        for k, v in err_obj.items()
                        if str(v or "").strip()
                    }
            except Exception:
                feature_errors = {}
        if feature_errors:
            counts["feature_error_rows"] += 1
            if feature_errors.get("dxf"):
                counts["dxf_feature_error_rows"] += 1
            if feature_errors.get("pdf"):
                counts["pdf_feature_error_rows"] += 1
            _append_example(
                warning_examples,
                f"{str(task.get('part_name') or '(unnamed)')} -> {_join_feature_errors(feature_errors)}",
            )
        if err:
            status = "append_error"
            counts["append_error_rows"] += 1
            _append_example(
                error_examples,
                f"{str(task.get('part_name') or '(unnamed)')} -> {str(err).strip() or 'append_error'}",
            )
        elif feature_errors:
            status = "computed_with_warnings"

        item = {
            "status": status,
            "part_name": str(task.get("part_name") or ""),
            "part_key": str(task.get("part_key") or ""),
            "kit_label": str(task.get("kit_label") or ""),
            "pdf_path": str(task.get("pdf_path") or ""),
            "dxf_path": str(task.get("dxf_path") or ""),
            "pdf_exists": bool(task.get("pdf_exists", False)),
            "dxf_exists": bool(task.get("dxf_exists", False)),
            "signals": signal_vals,
            "feature_errors": feature_errors,
            "error": str(err or _join_feature_errors(feature_errors)),
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
        "warning_examples": warning_examples,
        "error_examples": error_examples,
        "missing_pdf_examples": missing_pdf_examples,
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
    max_workers: Optional[int] = None,
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
    for c in ALL_COLS:
        if c not in df.columns:
            df[c] = _nan()
    df = df.loc[:, ALL_COLS].copy()
    if "part_key" in df.columns:
        df["part_key"] = df["part_key"].astype(object)
        df["part_key"] = df["part_key"].where(~df["part_key"].isna(), "")
    for idx in range(len(df)):
        part_name = "" if pd.isna(df.at[idx, "part_name"]) else str(df.at[idx, "part_name"]).strip()
        pdf_path = "" if pd.isna(df.at[idx, "pdf_path"]) else str(df.at[idx, "pdf_path"]).strip()
        dxf_path = "" if pd.isna(df.at[idx, "dxf_path"]) else str(df.at[idx, "dxf_path"]).strip()
        df.at[idx, "part_key"] = _make_part_key(part_name, pdf_path, dxf_path)
    if len(df) == 0:
        return {
            "dataset_path": dataset_path,
            "total_rows": 0,
            "processed_rows": 0,
            "updated_rows": 0,
            "error_rows": 0,
            "missing_pdf_rows": 0,
            "missing_dxf_rows": 0,
            "feature_error_rows": 0,
            "dxf_feature_error_rows": 0,
            "pdf_feature_error_rows": 0,
            "stopped": False,
            "workers": 1,
        }

    signals = list(signal_cols or (DXF_SIGNAL_COLS + PDF_SIGNAL_COLS))
    signals = [s for s in signals if s in FEATURE_COLS]
    if not signals:
        signals = list(DXF_SIGNAL_COLS + PDF_SIGNAL_COLS)

    total = int(len(df))
    workers = int(max_workers if max_workers is not None else ML_RECOMPUTE_MAX_WORKERS)
    workers = max(1, min(8, workers))
    workers = min(workers, max(1, total))
    processed = 0
    updated = 0
    errors = 0
    missing_pdf = 0
    missing_dxf = 0
    feature_error_rows = 0
    dxf_feature_error_rows = 0
    pdf_feature_error_rows = 0
    error_examples: List[str] = []
    missing_path_examples: List[str] = []
    stopped = False
    _safe_emit(on_progress, 0, total)

    tasks: List[Tuple[int, str, str]] = []
    for idx in range(total):
        pdf_raw = df.at[idx, "pdf_path"] if "pdf_path" in df.columns else ""
        dxf_raw = df.at[idx, "dxf_path"] if "dxf_path" in df.columns else ""
        pdf_path = "" if pd.isna(pdf_raw) else str(pdf_raw).strip()
        dxf_path = "" if pd.isna(dxf_raw) else str(dxf_raw).strip()
        tasks.append((idx, pdf_path, dxf_path))

    def _apply_row_result(
        idx: int,
        pdf_path: str,
        dxf_path: str,
        feats: Optional[Dict[str, float]],
        feature_errors: Optional[Dict[str, str]] = None,
        hard_error: str = "",
    ) -> None:
        nonlocal processed, updated, errors, missing_pdf, missing_dxf
        nonlocal feature_error_rows, dxf_feature_error_rows, pdf_feature_error_rows
        if not (pdf_path and os.path.exists(pdf_path)):
            missing_pdf += 1
            _append_example(
                missing_path_examples,
                f"{str(df.at[idx, 'part_name'] or '(unnamed)')} -> missing PDF: {pdf_path or '(blank)'}",
            )
        if not (dxf_path and os.path.exists(dxf_path)):
            missing_dxf += 1
            _append_example(
                missing_path_examples,
                f"{str(df.at[idx, 'part_name'] or '(unnamed)')} -> missing DXF: {dxf_path or '(blank)'}",
            )
        if feats is not None:
            for s in signals:
                df.at[idx, s] = _safe_float(feats.get(s, _nan()))
            updated += 1
        row_feature_errors = {
            str(k): str(v).strip()
            for k, v in (feature_errors or {}).items()
            if str(v or "").strip()
        }
        if row_feature_errors:
            feature_error_rows += 1
            if row_feature_errors.get("dxf"):
                dxf_feature_error_rows += 1
            if row_feature_errors.get("pdf"):
                pdf_feature_error_rows += 1
            _append_example(
                error_examples,
                f"{str(df.at[idx, 'part_name'] or '(unnamed)')} -> {_join_feature_errors(row_feature_errors)}",
            )
        if hard_error:
            _append_example(
                error_examples,
                f"{str(df.at[idx, 'part_name'] or '(unnamed)')} -> {hard_error}",
            )
        if hard_error or row_feature_errors or feats is None:
            errors += 1
        processed += 1
        _safe_emit(on_progress, processed, total)

    if workers <= 1:
        for idx, pdf_path, dxf_path in tasks:
            if should_stop is not None:
                try:
                    if should_stop():
                        stopped = True
                        break
                except Exception:
                    pass
            feats: Optional[Dict[str, float]] = None
            feature_errors: Dict[str, str] = {}
            hard_error = ""
            try:
                feats, feature_errors = _compute_phase2_signals_detail(pdf_path, dxf_path)
            except Exception as exc:
                hard_error = _format_error(exc)
                feats = None
            _apply_row_result(idx, pdf_path, dxf_path, feats, feature_errors, hard_error)
    else:
        submitted = 0
        in_flight: Dict[Any, Tuple[int, str, str]] = {}
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            def _submit_next() -> bool:
                nonlocal submitted
                if submitted >= len(tasks):
                    return False
                idx, pdf_path, dxf_path = tasks[submitted]
                submitted += 1
                fut = pool.submit(_compute_phase2_signals_detail, pdf_path, dxf_path)
                in_flight[fut] = (idx, pdf_path, dxf_path)
                return True

            for _ in range(min(workers, len(tasks))):
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
                    idx, pdf_path, dxf_path = in_flight.pop(fut)
                    feats: Optional[Dict[str, float]] = None
                    feature_errors: Dict[str, str] = {}
                    hard_error = ""
                    try:
                        feats, feature_errors = fut.result()
                    except Exception as exc:
                        hard_error = _format_error(exc)
                        feats = None
                    _apply_row_result(idx, pdf_path, dxf_path, feats, feature_errors, hard_error)
                    if not stopped:
                        _submit_next()
        finally:
            if stopped:
                for fut in list(in_flight.keys()):
                    try:
                        fut.cancel()
                    except Exception:
                        pass
                try:
                    pool.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    pool.shutdown(wait=False)
            else:
                pool.shutdown(wait=True)

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
        "feature_error_rows": feature_error_rows,
        "dxf_feature_error_rows": dxf_feature_error_rows,
        "pdf_feature_error_rows": pdf_feature_error_rows,
        "error_examples": error_examples,
        "missing_path_examples": missing_path_examples,
        "stopped": bool(stopped),
        "workers": int(workers),
    }



# -----------------------------
# Feature extraction entrypoint
# -----------------------------

def _compute_phase2_signals_detail(pdf_path: str, dxf_path: str) -> Tuple[Dict[str, float], Dict[str, str]]:
    feats: Dict[str, float] = {c: _nan() for c in FEATURE_COLS}
    errors: Dict[str, str] = {}

    try:
        feats.update(_compute_dxf_features(dxf_path))
    except Exception as exc:
        errors["dxf"] = _format_error(exc)

    try:
        feats.update(_compute_pdf_features_vector(pdf_path))
    except Exception as exc:
        errors["pdf"] = _format_error(exc)

    return feats, errors


def compute_phase2_signals(pdf_path: str, dxf_path: str) -> Dict[str, float]:
    feats, _ = _compute_phase2_signals_detail(pdf_path, dxf_path)
    return feats


def _compute_dxf_features(dxf_path: str) -> Dict[str, float]:
    return _compute_dxf_features_impl(
        dxf_path,
        dxf_signal_cols=DXF_SIGNAL_COLS,
        nan_fn=_nan,
        safe_float_fn=_safe_float,
        safe_int_fn=_safe_int,
        clamp01_fn=_clamp01,
        ezdxf_module=ezdxf,
    )


def _compute_pdf_features_vector(pdf_path: str) -> Dict[str, float]:
    return _compute_pdf_features_vector_impl(
        pdf_path,
        pdf_signal_cols=PDF_SIGNAL_COLS,
        nan_fn=_nan,
        safe_float_fn=_safe_float,
        fitz_module=fitz,
    )
