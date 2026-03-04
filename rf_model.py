from __future__ import annotations

import csv
import json
import math
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

_CACHE_LOCK = threading.Lock()
_MODEL_CACHE: Dict[str, Dict[str, Any]] = {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_float(v: object) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else 0.0
    except Exception:
        return 0.0


def _select_uncorrelated_features(
    X: np.ndarray,
    feature_names: Sequence[str],
    *,
    corr_threshold: float = 0.985,
) -> Tuple[np.ndarray, List[str], List[str], List[str]]:
    """
    Return a pruned feature matrix and selected feature names by removing:
    - constant/near-constant columns
    - highly correlated duplicate-signature columns (|corr| >= threshold)
    """
    if X.ndim != 2 or X.shape[1] <= 1:
        return X, list(feature_names), [], []

    n_features = int(X.shape[1])
    keep = [True] * n_features
    dropped_constant: List[str] = []
    dropped_correlated: List[str] = []

    # Drop constant columns first.
    variances = np.nanvar(X, axis=0)
    for j in range(n_features):
        v = float(variances[j]) if np.isfinite(variances[j]) else 0.0
        if v <= 1e-14:
            keep[j] = False
            dropped_constant.append(str(feature_names[j]))

    kept_idx = [j for j in range(n_features) if keep[j]]
    if len(kept_idx) <= 1:
        # Ensure at least one feature survives.
        if not kept_idx and n_features > 0:
            keep[0] = True
            kept_idx = [0]
            if str(feature_names[0]) in dropped_constant:
                dropped_constant.remove(str(feature_names[0]))
        Xk = X[:, kept_idx] if kept_idx else X[:, :1]
        names = [str(feature_names[j]) for j in kept_idx] if kept_idx else [str(feature_names[0])]
        return Xk, names, dropped_constant, dropped_correlated

    Xk = X[:, kept_idx]
    # Correlation on retained columns only.
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.corrcoef(Xk, rowvar=False)
    if corr.ndim != 2:
        names = [str(feature_names[j]) for j in kept_idx]
        return Xk, names, dropped_constant, dropped_correlated

    # Greedy: keep earlier columns, drop later highly-correlated ones.
    local_keep = [True] * len(kept_idx)
    for i in range(len(kept_idx)):
        if not local_keep[i]:
            continue
        for j in range(i + 1, len(kept_idx)):
            if not local_keep[j]:
                continue
            c = corr[i, j]
            if not np.isfinite(c):
                continue
            if abs(float(c)) >= float(corr_threshold):
                local_keep[j] = False
                dropped_correlated.append(str(feature_names[kept_idx[j]]))

    final_idx = [kept_idx[i] for i in range(len(kept_idx)) if local_keep[i]]
    if not final_idx:
        final_idx = [kept_idx[0]]
    Xf = X[:, final_idx]
    final_names = [str(feature_names[j]) for j in final_idx]
    return Xf, final_names, dropped_constant, dropped_correlated


def _model_key(model_path: str) -> str:
    return os.path.normcase(os.path.abspath(model_path or "")).lower()


def _load_dataset_for_rf(
    dataset_path: str,
    feature_cols: Sequence[str],
    allowed_labels: Optional[Sequence[str]] = None,
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    if not os.path.exists(dataset_path):
        raise RuntimeError(f"Dataset not found: {dataset_path}")

    allowed = set(str(x) for x in (allowed_labels or []))
    has_allowed_filter = bool(allowed)

    X_rows: List[List[float]] = []
    y: List[str] = []
    with open(dataset_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            kit = str(row.get("kit_label") or row.get("kit") or "").strip()
            if not kit:
                continue
            if has_allowed_filter and kit not in allowed:
                continue
            feats = [_safe_float(row.get(col, 0.0)) for col in feature_cols]
            X_rows.append(feats)
            y.append(kit)

    if not X_rows:
        raise RuntimeError("Dataset has no usable rows for RF.")
    return np.asarray(X_rows, dtype=float), np.asarray(y, dtype=object), list(feature_cols)


def train_or_load_rf(
    dataset_path: str,
    model_path: str,
    meta_path: str,
    feature_cols: Sequence[str],
    allowed_labels: Optional[Sequence[str]] = None,
    force_train: bool = False,
) -> Tuple[Any, Any, List[str], str]:
    """
    Returns (model, encoder, feature_names, source)
    source in {"memory", "disk", "trained"}.
    """
    try:
        from joblib import dump, load
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.preprocessing import LabelEncoder
    except Exception as e:
        raise RuntimeError(
            "RF Predict requires scikit-learn and joblib.\n"
            "Install:\n  pip install scikit-learn joblib"
        ) from e

    os.makedirs(os.path.dirname(model_path), exist_ok=True)

    dataset_mtime = os.path.getmtime(dataset_path) if os.path.exists(dataset_path) else 0.0
    model_mtime = os.path.getmtime(model_path) if os.path.exists(model_path) else 0.0
    key = _model_key(model_path)
    wanted_features = list(feature_cols)

    if not force_train:
        with _CACHE_LOCK:
            cached = _MODEL_CACHE.get(key)
        if cached:
            if (
                float(cached.get("dataset_mtime", -1.0)) == float(dataset_mtime)
                and float(cached.get("model_mtime", -1.0)) == float(model_mtime)
                and list(cached.get("requested_features", cached.get("feature_names", []))) == wanted_features
            ):
                return (
                    cached["model"],
                    cached["encoder"],
                    list(cached["feature_names"]),
                    "memory",
                )

    can_load = (
        (not force_train)
        and os.path.exists(model_path)
        and os.path.exists(meta_path)
        and model_mtime >= dataset_mtime
    )
    if can_load:
        obj = load(model_path)
        if "requested_features" not in obj:
            obj = {}
        feat_names = list(obj.get("feature_names", []))
        requested_features = list(obj.get("requested_features", feat_names))
        if requested_features == wanted_features:
            model = obj["model"]
            encoder = obj["encoder"]
            with _CACHE_LOCK:
                _MODEL_CACHE[key] = {
                    "model": model,
                    "encoder": encoder,
                    "feature_names": feat_names,
                    "requested_features": requested_features,
                    "dataset_mtime": float(dataset_mtime),
                    "model_mtime": float(model_mtime),
                }
            return model, encoder, feat_names, "disk"

    X, y, feat_names = _load_dataset_for_rf(dataset_path, wanted_features, allowed_labels=allowed_labels)
    X_train, selected_features, dropped_constant, dropped_correlated = _select_uncorrelated_features(
        X, feat_names, corr_threshold=0.985
    )

    le = LabelEncoder()
    y_enc = le.fit_transform(y)

    model = RandomForestClassifier(
        n_estimators=350,
        max_depth=None,
        min_samples_split=3,
        min_samples_leaf=1,
        n_jobs=-1,
        random_state=42,
    )
    model.fit(X_train, y_enc)

    dump(
        {
            "model": model,
            "encoder": le,
            "feature_names": selected_features,
            "requested_features": wanted_features,
            "dropped_constant_features": dropped_constant,
            "dropped_correlated_features": dropped_correlated,
            "corr_threshold_abs": 0.985,
        },
        model_path,
    )

    model_mtime_new = os.path.getmtime(model_path) if os.path.exists(model_path) else model_mtime
    meta = {
        "trained_at_utc": _utc_now_iso(),
        "dataset_path": dataset_path,
        "dataset_mtime": dataset_mtime,
        "requested_features": wanted_features,
        "selected_features": selected_features,
        "dropped_constant_features": dropped_constant,
        "dropped_correlated_features": dropped_correlated,
        "corr_threshold_abs": 0.985,
        "classes": list(le.classes_),
        "n_rows": int(X.shape[0]),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    with _CACHE_LOCK:
        _MODEL_CACHE[key] = {
            "model": model,
            "encoder": le,
            "feature_names": selected_features,
            "requested_features": wanted_features,
            "dataset_mtime": float(dataset_mtime),
            "model_mtime": float(model_mtime_new),
        }
    return model, le, selected_features, "trained"


def predict_with_rf(
    model: Any,
    encoder: Any,
    feature_names: Sequence[str],
    feature_rows: Iterable[Dict[str, float]],
) -> List[Tuple[str, float]]:
    rows = list(feature_rows or [])
    if not rows:
        return []

    X_rows: List[List[float]] = []
    for feats in rows:
        X_rows.append([_safe_float(feats.get(col, 0.0)) for col in feature_names])
    X = np.asarray(X_rows, dtype=float)

    probs = model.predict_proba(X)
    out: List[Tuple[str, float]] = []
    for p in probs:
        j = int(np.argmax(p))
        label = str(encoder.inverse_transform([j])[0])
        conf = float(p[j])
        out.append((label, conf))
    return out
