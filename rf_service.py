from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence, Tuple

import ml_runtime
import rf_model
from rpd_io import PartRow


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

    for i, p in enumerate(parts, start=1):
        if should_cancel_cb is not None and should_cancel_cb():
            return [], "canceled"
        feature_rows.append(
            ml_runtime.rf_features_for_part(
                p,
                resolve_asset_fn=resolve_asset_fn,
                feature_cols=feature_cols,
            )
        )
        if progress_cb is not None:
            progress_cb(i, total, f"RF: extracting features...\n{p.part}")

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
    preds = rf_model.predict_with_rf(model, encoder, feat_names, feature_rows)
    return preds, source
