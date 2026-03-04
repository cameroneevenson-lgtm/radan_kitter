from __future__ import annotations

import io
import math
import os
from typing import Dict, List, Sequence, Tuple

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

try:
    import numpy as np
except Exception:
    np = None

try:
    from matplotlib.figure import Figure
except Exception:
    Figure = None

try:
    import matplotlib
except Exception:
    matplotlib = None

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
except Exception:
    try:
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
    except Exception:
        FigureCanvasQTAgg = None

try:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
except Exception:
    FigureCanvasAgg = None

try:
    from config import CANON_KITS, KIT_ABBR
except Exception:
    CANON_KITS = []
    KIT_ABBR = {}

_PLOT_DATA_CACHE: Dict[tuple, dict] = {}
_COUNT_LIKE_SIGNALS = {
    "dxf_entity_count",
    "dxf_arc_count",
    "dxf_hole_count",
    "dxf_exterior_notch_count",
    "dxf_has_interior_polylines",
    "dxf_color_count",
}
# Plot-only override: show sparse count signals as presence rate by kit.
# This makes very sparse signals (like notch count) visually readable.
_PRESENCE_RATE_SIGNALS = {
    "dxf_exterior_notch_count",
}
# Plot-only log scaling for weak low-range signals.
# value -> log1p(k*value)/log1p(k), k > 0
_LOG_SCALE_BY_SIGNAL = {
    "dxf_concavity_ratio": 40.0,  # dxf2
}
# Plot-only shaping so specific signals stay readable:
# - gamma < 1.0 boosts low values
# - gamma > 1.0 compresses high values
_PLOT_GAMMA_BY_SIGNAL = {
    "dxf_longest_edge_ratio": 1.35,    # dxf8
}


def _finite(v: float) -> bool:
    try:
        return math.isfinite(float(v))
    except Exception:
        return False


def _coerce_numeric(df: pd.DataFrame, cols: Sequence[str]) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def _short_signal_labels(signals: Sequence[str]) -> List[str]:
    out: List[str] = []
    d_i = 0
    p_i = 0
    x_i = 0
    for s in signals:
        name = str(s or "").strip().lower()
        if name.startswith("dxf"):
            d_i += 1
            out.append(f"dxf{d_i}")
        elif name.startswith("pdf"):
            p_i += 1
            out.append(f"pdf{p_i}")
        else:
            x_i += 1
            out.append(f"sig{x_i}")
    return out


def _ordered_kit_names(kit_names: Sequence[str]) -> List[str]:
    by_norm: Dict[str, str] = {}
    for k in kit_names:
        s = str(k or "").strip()
        if not s:
            continue
        by_norm.setdefault(s.lower(), s)
    out: List[str] = []
    for k in CANON_KITS:
        s = str(k or "").strip()
        if not s:
            continue
        found = by_norm.pop(s.lower(), None)
        if found:
            out.append(found)
    if by_norm:
        out.extend(sorted(by_norm.values(), key=lambda x: x.lower()))
    return out


def _kit_slice_labels(kit_names: Sequence[str]) -> List[str]:
    out: List[str] = []
    for k in kit_names:
        ks = str(k or "").strip()
        ab = str(KIT_ABBR.get(ks, "") or "").strip().upper()
        if not ab:
            letters = "".join(ch for ch in ks.upper() if ch.isalpha())
            ab = letters[:3] if letters else ks[:3].upper()
        out.append(ab[:3])
    return out


def _normalized_kit_signal_stats(
    df: pd.DataFrame,
    signal_cols: Sequence[str],
    kit_col: str = "kit_label",
) -> Tuple[List[str], List[str], Dict[str, List[float]], Dict[str, List[float]], int]:
    work = df.copy()
    if kit_col not in work.columns:
        return [], [], {}, {}, 0

    work[kit_col] = work[kit_col].astype(str).str.strip()
    work = work[work[kit_col] != ""]
    total_rows = int(len(work))
    if total_rows <= 0:
        return [], [], {}, {}, 0

    present_signals = [c for c in signal_cols if c in work.columns]
    if not present_signals:
        return [], [], {}, {}, total_rows

    work = _coerce_numeric(work, present_signals)
    kit_counts = work.groupby(kit_col, dropna=False).size().sort_values(ascending=False)
    kit_names = _ordered_kit_names([str(k) for k in kit_counts.index.tolist()])

    # Transform features for better comparability on the polar plot:
    # - log1p on count-like signals to reduce outlier compression.
    # - robust p5..p95 scaling to keep rare spikes from flattening the chart.
    transformed = work[[kit_col] + present_signals].copy()
    for s in present_signals:
        col = pd.to_numeric(transformed[s], errors="coerce")
        if s in _PRESENCE_RATE_SIGNALS:
            col_out = pd.Series(float("nan"), index=col.index, dtype=float)
            mask = col.notna()
            if bool(mask.any()):
                col_out.loc[mask] = (col.loc[mask] > 0.0).astype(float)
            transformed[s] = col_out
            continue
        if s in _COUNT_LIKE_SIGNALS:
            col = col.clip(lower=0)
            if np is not None:
                col = np.log1p(col)
            else:
                col = col.map(lambda v: math.log1p(float(v)) if _finite(v) and float(v) >= 0.0 else float("nan"))
        log_k = _LOG_SCALE_BY_SIGNAL.get(s)
        if log_k is not None:
            k = max(1e-6, float(log_k))
            den = math.log1p(k)
            col = col.clip(lower=0)
            if np is not None:
                col = np.log1p(k * col) / den
            else:
                col = col.map(
                    lambda v: (math.log1p(k * float(v)) / den)
                    if _finite(v) and float(v) >= 0.0
                    else float("nan")
                )
        transformed[s] = col

    mins: Dict[str, float] = {}
    maxs: Dict[str, float] = {}
    for s in present_signals:
        col = pd.to_numeric(transformed[s], errors="coerce")
        finite_vals = col[np.isfinite(col)] if np is not None else col.dropna()
        if len(finite_vals) <= 0:
            mins[s] = float("nan")
            maxs[s] = float("nan")
            continue
        try:
            lo = float(finite_vals.quantile(0.05))
            hi = float(finite_vals.quantile(0.95))
        except Exception:
            lo = float(finite_vals.min())
            hi = float(finite_vals.max())
        # Fallback for tiny spread after robust clipping.
        if not (_finite(lo) and _finite(hi)) or abs(hi - lo) < 1e-12:
            lo = float(finite_vals.min())
            hi = float(finite_vals.max())
        mins[s] = lo
        maxs[s] = hi

    norm = transformed[[kit_col] + present_signals].copy()
    for s in present_signals:
        lo = mins.get(s, float("nan"))
        hi = maxs.get(s, float("nan"))
        if not (_finite(lo) and _finite(hi)):
            norm[s] = float("nan")
            continue
        if abs(hi - lo) < 1e-12:
            norm[s] = 0.5
            continue
        norm[s] = ((norm[s] - lo) / (hi - lo)).clip(0.0, 1.0)
        if s in _PRESENCE_RATE_SIGNALS:
            # Visual boost for sparse presence-rate signals (plot-only).
            # Keeps ordering but makes low non-zero rates easier to interpret.
            norm[s] = norm[s].pow(0.5)
        gamma = _PLOT_GAMMA_BY_SIGNAL.get(s)
        if gamma is not None:
            # Plot-only shaping; raw dataset values stay unchanged.
            norm[s] = norm[s].pow(float(gamma))

    mean_df = norm.groupby(kit_col, dropna=False)[present_signals].mean(numeric_only=True)
    std_df = norm.groupby(kit_col, dropna=False)[present_signals].std(numeric_only=True).fillna(0.0)
    if mean_df.empty:
        return [], [], {}, {}, total_rows

    # Second-stage normalization for sparse presence-rate signals:
    # normalize across kits (between-kit spread), not across rows.
    # This makes low-rate but discriminative signals visually readable.
    for s in present_signals:
        if s not in _PRESENCE_RATE_SIGNALS:
            continue
        try:
            mcol = pd.to_numeric(mean_df[s], errors="coerce")
            finite = mcol[np.isfinite(mcol)] if np is not None else mcol.dropna()
            if len(finite) <= 0:
                continue
            try:
                lo = float(finite.quantile(0.05))
                hi = float(finite.quantile(0.95))
            except Exception:
                lo = float(finite.min())
                hi = float(finite.max())
            if not (_finite(lo) and _finite(hi)) or abs(hi - lo) < 1e-12:
                lo = float(finite.min())
                hi = float(finite.max())
            if not (_finite(lo) and _finite(hi)) or abs(hi - lo) < 1e-12:
                continue
            den = float(hi - lo)
            mean_df[s] = ((mcol - lo) / den).clip(0.0, 1.0)
            scol = pd.to_numeric(std_df[s], errors="coerce").fillna(0.0)
            std_df[s] = (scol / den).clip(lower=0.0)
        except Exception:
            continue

    kit_to_mean: Dict[str, List[float]] = {}
    kit_to_std: Dict[str, List[float]] = {}
    for kit in kit_names:
        m_vals: List[float] = []
        s_vals: List[float] = []
        for s in present_signals:
            mv = float(mean_df.at[kit, s]) if s in mean_df.columns else float("nan")
            sv = float(std_df.at[kit, s]) if s in std_df.columns else 0.0
            m_vals.append(mv if _finite(mv) else float("nan"))
            s_vals.append(max(0.0, sv) if _finite(sv) else 0.0)
        kit_to_mean[str(kit)] = m_vals
        kit_to_std[str(kit)] = s_vals

    return kit_names, present_signals, kit_to_mean, kit_to_std, total_rows


def _dataset_cache_key(dataset_path: str, signal_cols: Sequence[str]) -> tuple:
    p = os.path.normpath(os.path.abspath(dataset_path))
    try:
        st = os.stat(p)
        mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
        size = int(st.st_size)
    except Exception:
        mtime_ns = 0
        size = 0
    return (p, mtime_ns, size, tuple(str(c) for c in signal_cols))


def _load_plot_payload(dataset_path: str, signal_cols: Sequence[str]) -> dict:
    key = _dataset_cache_key(dataset_path, signal_cols)
    cached = _PLOT_DATA_CACHE.get(key)
    if cached is not None:
        return cached

    wanted = {str(c) for c in (signal_cols or [])}
    wanted.add("kit_label")
    try:
        df = pd.read_csv(
            dataset_path,
            usecols=lambda c: c in wanted,
            low_memory=False,
        )
    except Exception:
        # Fallback for unusual CSV dialect / parser issues.
        df = pd.read_csv(dataset_path, low_memory=False)

    kit_names, signals, kit_to_mean, kit_to_std, total_rows = _normalized_kit_signal_stats(df, signal_cols)
    payload = {
        "kit_names": kit_names,
        "signals": signals,
        "kit_to_mean": kit_to_mean,
        "kit_to_std": kit_to_std,
        "total_rows": int(total_rows),
        "dataset_path": dataset_path,
    }
    if len(_PLOT_DATA_CACHE) >= 4:
        # Keep a tiny cache to avoid stale growth.
        oldest_key = next(iter(_PLOT_DATA_CACHE.keys()))
        _PLOT_DATA_CACHE.pop(oldest_key, None)
    _PLOT_DATA_CACHE[key] = payload
    return payload


def _draw_signal_grid(
    fig: Figure,
    *,
    kit_names: Sequence[str],
    signals: Sequence[str],
    kit_to_mean: Dict[str, List[float]],
    kit_to_std: Dict[str, List[float]],
) -> None:
    fig.clf()
    grid_cols = 5
    grid_rows = max(3, int(math.ceil(len(signals) / float(grid_cols))))
    n_kits = len(kit_names)
    if n_kits <= 0:
        return
    kit_angles = [(2.0 * math.pi * i) / n_kits for i in range(n_kits)]
    closed_angles = kit_angles + [kit_angles[0]]
    signal_labels = _short_signal_labels(signals)
    kit_labels = _kit_slice_labels(kit_names)

    if matplotlib is not None and hasattr(matplotlib, "colormaps"):
        cmap = matplotlib.colormaps.get_cmap("tab10")
    else:
        import matplotlib.cm as _cm
        cmap = _cm.get_cmap("tab10")

    for sig_idx, sig_name in enumerate(signals):
        ax = fig.add_subplot(grid_rows, grid_cols, sig_idx + 1, projection="polar")
        means: List[float] = []
        stds: List[float] = []
        for kit in kit_names:
            m_vals = list(kit_to_mean.get(kit, []))
            s_vals = list(kit_to_std.get(kit, []))
            mv = m_vals[sig_idx] if sig_idx < len(m_vals) else float("nan")
            sv = s_vals[sig_idx] if sig_idx < len(s_vals) else float("nan")
            means.append(0.0 if not _finite(mv) else float(mv))
            stds.append(0.0 if not _finite(sv) else max(0.0, float(sv)))

        lower1 = [max(0.0, m - sd) for m, sd in zip(means, stds)]
        upper1 = [min(1.0, m + sd) for m, sd in zip(means, stds)]
        lower2 = [max(0.0, m - 2.0 * sd) for m, sd in zip(means, stds)]
        upper2 = [min(1.0, m + 2.0 * sd) for m, sd in zip(means, stds)]

        means_c = means + [means[0]]
        lower1_c = lower1 + [lower1[0]]
        upper1_c = upper1 + [upper1[0]]
        lower2_c = lower2 + [lower2[0]]
        upper2_c = upper2 + [upper2[0]]

        color = cmap(sig_idx % 10)
        ax.fill_between(closed_angles, lower2_c, upper2_c, color=color, alpha=0.07, linewidth=0.0)
        ax.fill_between(closed_angles, lower1_c, upper1_c, color=color, alpha=0.16, linewidth=0.0)
        ax.plot(closed_angles, means_c, linewidth=1.6, color=color)

        ax.set_ylim(0.0, 1.0)
        ax.set_yticks([0.25, 0.5, 0.75, 1.0])
        ax.set_yticklabels([])
        ax.set_xticks(kit_angles)
        ax.set_xticklabels(kit_labels, fontsize=7)
        ax.grid(True, alpha=0.35)
        full_name = str(sig_name or "").strip()
        ax.set_title(f"{signal_labels[sig_idx]} - {full_name}", fontsize=9, pad=10)

    total_cells = grid_rows * grid_cols
    for extra_idx in range(len(signals), total_cells):
        ax = fig.add_subplot(grid_rows, grid_cols, extra_idx + 1)
        ax.axis("off")
    try:
        fig.suptitle("ML Signal Distribution (Polar)", fontsize=12, y=0.995)
        fig.subplots_adjust(left=0.03, right=0.98, top=0.95, bottom=0.05, wspace=0.22, hspace=0.28)
    except Exception:
        pass


def create_polar_dialog(
    *,
    parent: QWidget,
    dataset_path: str,
    signal_cols: Sequence[str],
) -> QDialog:
    if Figure is None:
        raise RuntimeError("matplotlib is not available in this runtime.")
    if not dataset_path or not os.path.exists(dataset_path):
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    payload = _load_plot_payload(dataset_path, signal_cols)
    kit_names = payload.get("kit_names", [])
    signals = payload.get("signals", [])
    kit_to_mean = payload.get("kit_to_mean", {})
    kit_to_std = payload.get("kit_to_std", {})
    total_rows = int(payload.get("total_rows", 0))
    if not kit_names or not signals:
        raise RuntimeError("Dataset has no usable kit/signal rows to plot.")

    dialog = QDialog(parent)
    dialog.setWindowTitle("ML Signal Distribution (Polar)")
    dialog.resize(1260, 860)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(6)

    header = QLabel(
        f"Dataset: {dataset_path}\nRows: {total_rows} | Kits: {len(kit_names)} | Signals: {len(signals)} "
        f"(dxf1.. / pdf1.. labels)"
    )
    header.setTextInteractionFlags(Qt.TextSelectableByMouse)
    layout.addWidget(header)

    fig = Figure(figsize=(15.0, 8.4), dpi=100, tight_layout=True)
    _draw_signal_grid(
        fig,
        kit_names=kit_names,
        signals=signals,
        kit_to_mean=kit_to_mean,
        kit_to_std=kit_to_std,
    )

    # Preferred: interactive Qt canvas. Fallback: static PNG if Qt backend unavailable.
    if FigureCanvasQTAgg is not None:
        canvas = FigureCanvasQTAgg(fig)
        sc = QScrollArea(dialog)
        sc.setWidgetResizable(False)
        sc.setAlignment(Qt.AlignCenter)
        sc.setWidget(canvas)
        layout.addWidget(sc, 1)

        def _sync_canvas_size() -> None:
            try:
                px_w = int(max(920, fig.get_figwidth() * fig.get_dpi()))
                px_h = int(max(620, fig.get_figheight() * fig.get_dpi()))
                canvas.setMinimumSize(px_w, px_h)
                canvas.resize(px_w, px_h)
            except Exception:
                pass

        _sync_canvas_size()
        canvas.draw_idle()
    else:
        if FigureCanvasAgg is None:
            raise RuntimeError("No matplotlib Qt/Agg backend available for rendering.")
        canvas = FigureCanvasAgg(fig)
        canvas.draw()
        bio = io.BytesIO()
        fig.savefig(bio, format="png", dpi=130, bbox_inches="tight")
        pix = QPixmap()
        pix.loadFromData(bio.getvalue(), "PNG")
        note = QLabel("Qt plot backend unavailable: showing static polar render.")
        layout.addWidget(note)
        img_label = QLabel()
        img_label.setAlignment(Qt.AlignCenter)
        img_label.setPixmap(pix)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(img_label)
        layout.addWidget(sc, 1)
    return dialog
