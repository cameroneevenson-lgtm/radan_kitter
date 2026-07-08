from __future__ import annotations

import os
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import ml_runtime
import rf_model
from rpd_io import PartRow


SINGLE_LABEL_COLLAPSE_MIN_ROWS = 10


def _single_label_collapse_source(preds: Sequence[Tuple[str, float]]) -> str:
    labels = [str(label or "").strip() for label, _conf in preds if str(label or "").strip()]
    if len(labels) < SINGLE_LABEL_COLLAPSE_MIN_ROWS:
        return ""
    unique_labels = sorted(set(labels))
    if len(unique_labels) != 1:
        return ""
    return f"single_label_collapse:{unique_labels[0]}:{len(labels)}"


def run_rf_suggestions(
    parts: List[PartRow],
    *,
    dataset_path: str,
    model_path: str,
    meta_path: str,
    feature_cols: Sequence[str],
    allowed_labels: Sequence[str],
    resolve_asset_fn: Callable[[str, str], Optional[str]],
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    should_cancel_cb: Optional[Callable[[], bool]] = None,
) -> Tuple[List[Tuple[str, float]], str]:
    total = len(parts)
    feature_rows: List[Dict[str, float]] = []
    active_rows: List[int] = []
    preds: List[Tuple[str, float]] = [("", 0.0) for _ in parts]

    for i, p in enumerate(parts, start=1):
        if should_cancel_cb is not None and should_cancel_cb():
            return [], "canceled"
        pdf = resolve_asset_fn(p.sym, ".pdf") or ""
        if not (pdf and os.path.exists(pdf)):
            if progress_cb is not None:
                progress_cb(i, total, f"RF: skipping missing PDF...\n{p.part}")
            continue
        feature_rows.append(
            ml_runtime.rf_features_for_part(
                p,
                resolve_asset_fn=resolve_asset_fn,
                feature_cols=feature_cols,
            )
        )
        active_rows.append(i - 1)
        if progress_cb is not None:
            progress_cb(i, total, f"RF: extracting features...\n{p.part}")

    if not active_rows:
        return preds, "no_pdf_rows"

    if progress_cb is not None:
        progress_cb(total, total, "RF: loading model...")

    model, encoder, feat_names, source = rf_model.train_or_load_rf(
        dataset_path=dataset_path,
        model_path=model_path,
        meta_path=meta_path,
        feature_cols=feature_cols,
        allowed_labels=allowed_labels,
        force_train=False,
    )
    active_preds = rf_model.predict_with_rf(model, encoder, feat_names, feature_rows)
    collapse_source = _single_label_collapse_source(active_preds)
    if collapse_source:
        return preds, collapse_source
    for row_idx, pred in zip(active_rows, active_preds):
        preds[row_idx] = pred
    return preds, source
