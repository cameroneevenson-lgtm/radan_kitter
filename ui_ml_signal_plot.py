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
    QSizePolicy,
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


class _AspectLockedDialog(QDialog):
    def __init__(self, aspect_ratio: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        ar = float(aspect_ratio) if aspect_ratio else 1.0
        self._aspect_ratio = max(0.2, min(6.0, ar))
        self._aspect_lock_guard = False

    def resizeEvent(self, event) -> None:
        if self._aspect_lock_guard:
            super().resizeEvent(event)
            return

        new_size = event.size()
        old_size = event.oldSize()
        w = max(1, int(new_size.width()))
        h = max(1, int(new_size.height()))

        if old_size.isValid():
            dw = abs(w - int(old_size.width()))
            dh = abs(h - int(old_size.height()))
        else:
            # First resize event: treat width as the driver.
            dw, dh = 1, 0

        target_w = w
        target_h = h
        if dw >= dh:
            target_h = max(1, int(round(w / self._aspect_ratio)))
        else:
            target_w = max(1, int(round(h * self._aspect_ratio)))

        if target_w != w or target_h != h:
            self._aspect_lock_guard = True
            try:
                self.resize(target_w, target_h)
            finally:
                self._aspect_lock_guard = False

        super().resizeEvent(event)

_PLOT_DATA_CACHE: Dict[tuple, dict] = {}
_COUNT_LIKE_SIGNALS = {
    "dxf_entity_count",
    "dxf_arc_count",
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
}
# Plot-only shaping so specific signals stay readable:
# - gamma < 1.0 boosts low values
# - gamma > 1.0 compresses high values
_PLOT_GAMMA_BY_SIGNAL = {
    "dxf_fill_ratio": 2.8,
}
_TAIL_STRETCH_BY_SIGNAL = {
    "dxf_fill_ratio": 1.0,
}
# Visualization controls (plot-only; no ML data-path impact).
PLOT_USE_MINMAX_NORM = True
PLOT_ENABLE_SMOOTHING = True
PLOT_SMOOTH_POINTS_PER_SEGMENT = 36
PLOT_SMOOTHING_PASSES = 4
PLOT_TARGET_MAX_MEAN_RADIAL = 0.68
PLOT_SD_MAX_SIGMA = 3.0
PLOT_SORT_BY_LOBE_STRENGTH = True
PLOT_RADAR_THEME = True
PLOT_RADAR_FIG_BG = "#02060d"
PLOT_RADAR_AX_BG = "#061223"
PLOT_RADAR_GRID = "#73ffd2"
PLOT_RADAR_TEXT = "#d8ffef"
PLOT_SIGMA_BAND_COUNT = 32
PLOT_SIGMA_ALPHA_OUTER = 0.003
PLOT_SIGMA_ALPHA_INNER = 0.072
# Backward-compatible band definition for existing renderer path.
PLOT_SIGMA_BANDS: Sequence[Tuple[float, float]] = (
    (1.0, 0.12),
    (0.8, 0.18),
    (0.6, 0.24),
    (0.4, 0.30),
    (0.2, 0.36),
)


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


def _interp_closed_signal(
    values: Sequence[float],
    *,
    points_per_segment: int,
    smooth_passes: int,
) -> Tuple[List[float], List[float]]:
    """
    Interpolate closed circular signal with optional smoothing for display only.
    Returns (angles, values) with closed loop endpoints included.
    """
    vals = [0.0 if not _finite(v) else float(v) for v in values]
    n = len(vals)
    if n <= 0:
        return [], []
    if n == 1:
        return [0.0, 2.0 * math.pi], [vals[0], vals[0]]

    if np is None or points_per_segment <= 1:
        angles = [(2.0 * math.pi * i) / n for i in range(n)] + [0.0]
        return angles, vals + [vals[0]]

    dense_n = max(n * int(points_per_segment), n)
    base_x = np.arange(n + 1, dtype=float)
    base_y = np.asarray(vals + [vals[0]], dtype=float)
    x_new = np.linspace(0.0, float(n), int(dense_n) + 1)
    y_new = np.interp(x_new, base_x, base_y)

    if smooth_passes > 0 and dense_n >= 8:
        kernel = np.asarray([1.0, 4.0, 6.0, 4.0, 1.0], dtype=float)
        kernel /= np.sum(kernel)
        core = y_new[:-1].copy()  # periodic core, exclude duplicate endpoint
        for _ in range(int(max(0, smooth_passes))):
            pad = 2
            ext = np.concatenate([core[-pad:], core, core[:pad]])
            core = np.convolve(ext, kernel, mode="same")[pad:-pad]
        y_new = np.concatenate([core, [core[0]]])

    y_new = np.clip(y_new, 0.0, 1.0)
    a_new = np.linspace(0.0, 2.0 * math.pi, int(dense_n) + 1)
    return a_new.tolist(), y_new.tolist()


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
    # - optional min-max normalization (or robust p5..p95 fallback).
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
        if s in _TAIL_STRETCH_BY_SIGNAL:
            eps = 1e-6
            col = col.clip(lower=0.0, upper=1.0 - eps)
            if np is not None:
                col = -np.log(1.0 - col + eps)
            else:
                col = col.map(
                    lambda v: (-math.log(max(eps, 1.0 - float(v) + eps)))
                    if _finite(v) else float("nan")
                )
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
        if PLOT_USE_MINMAX_NORM:
            lo = float(finite_vals.min())
            hi = float(finite_vals.max())
        else:
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

    if PLOT_SORT_BY_LOBE_STRENGTH and len(present_signals) > 1:
        def _lobe_score(sig: str) -> float:
            try:
                mcol = pd.to_numeric(mean_df[sig], errors="coerce")
                scol = pd.to_numeric(std_df[sig], errors="coerce").fillna(0.0)
                mfin = mcol[np.isfinite(mcol)] if np is not None else mcol.dropna()
                if len(mfin) < 2:
                    return -1.0
                between = float(mfin.std(ddof=0))
                within = float(scol.mean())
                return between / (within + 1e-9)
            except Exception:
                return -1.0

        present_signals = sorted(
            present_signals,
            key=lambda s: (_lobe_score(s), str(s)),
            reverse=True,
        )

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
    return (
        p,
        mtime_ns,
        size,
        tuple(str(c) for c in signal_cols),
        bool(PLOT_USE_MINMAX_NORM),
        bool(PLOT_ENABLE_SMOOTHING),
        int(PLOT_SMOOTH_POINTS_PER_SEGMENT),
        int(PLOT_SMOOTHING_PASSES),
        float(PLOT_TARGET_MAX_MEAN_RADIAL),
        float(PLOT_SD_MAX_SIGMA),
        bool(PLOT_RADAR_THEME),
        int(PLOT_SIGMA_BAND_COUNT),
    )


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
    show_labels: bool = True,
    grid_rows_override: int = 0,
) -> None:
    fig.clf()
    if PLOT_RADAR_THEME:
        fig.patch.set_facecolor(PLOT_RADAR_FIG_BG)
    if int(grid_rows_override) > 0:
        grid_rows = max(1, int(grid_rows_override))
        grid_cols = max(1, int(math.ceil(len(signals) / float(grid_rows))))
    else:
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
        cmap = matplotlib.colormaps.get_cmap("turbo" if PLOT_RADAR_THEME else "tab10")
    else:
        import matplotlib.cm as _cm
        cmap = _cm.get_cmap("turbo" if PLOT_RADAR_THEME else "tab10")

    for sig_idx, sig_name in enumerate(signals):
        ax = fig.add_subplot(grid_rows, grid_cols, sig_idx + 1, projection="polar")
        if PLOT_RADAR_THEME:
            ax.set_facecolor(PLOT_RADAR_AX_BG)
        means: List[float] = []
        stds: List[float] = []
        for kit in kit_names:
            m_vals = list(kit_to_mean.get(kit, []))
            s_vals = list(kit_to_std.get(kit, []))
            mv = m_vals[sig_idx] if sig_idx < len(m_vals) else float("nan")
            sv = s_vals[sig_idx] if sig_idx < len(s_vals) else float("nan")
            means.append(0.0 if not _finite(mv) else float(mv))
            stds.append(0.0 if not _finite(sv) else max(0.0, float(sv)))

        if len(signals) > 1:
            color_pos = 0.30 + 0.65 * (float(sig_idx) / float(len(signals) - 1))
        else:
            color_pos = 0.72
        color = cmap(float(color_pos))
        if PLOT_ENABLE_SMOOTHING:
            a_plot, m_plot = _interp_closed_signal(
                means,
                points_per_segment=PLOT_SMOOTH_POINTS_PER_SEGMENT,
                smooth_passes=PLOT_SMOOTHING_PASSES,
            )
            _a_std, s_plot = _interp_closed_signal(
                stds,
                points_per_segment=PLOT_SMOOTH_POINTS_PER_SEGMENT,
                smooth_passes=PLOT_SMOOTHING_PASSES,
            )
        else:
            a_plot = closed_angles
            m_plot = means + [means[0]]
            s_plot = stds + [stds[0]]

        # Per-signal radial normalization for display:
        # scale so max(mean) lands at the configured target radius.
        target = float(PLOT_TARGET_MAX_MEAN_RADIAL)
        if target > 0.0:
            peak_mean = 0.0
            for m in m_plot:
                if _finite(m):
                    peak_mean = max(peak_mean, float(m))
            if peak_mean > 1e-12:
                radial_scale = target / peak_mean
                m_plot = [max(0.0, min(1.0, float(m) * radial_scale)) for m in m_plot]
                s_plot = [max(0.0, float(sd) * radial_scale) for sd in s_plot]

        # Gradient distribution band: stacked nested sigma bands.
        for sigma, alpha in PLOT_SIGMA_BANDS:
            lower = [max(0.0, m - float(sigma) * sd) for m, sd in zip(m_plot, s_plot)]
            upper = [min(1.0, m + float(sigma) * sd) for m, sd in zip(m_plot, s_plot)]
            ax.fill_between(a_plot, lower, upper, color=color, alpha=float(alpha), linewidth=0.2)

        # Explicit ±1σ envelope (outline) for quick spread reading.
        lower1 = [max(0.0, m - sd) for m, sd in zip(m_plot, s_plot)]
        upper1 = [min(1.0, m + sd) for m, sd in zip(m_plot, s_plot)]
        ax.plot(a_plot, lower1, linewidth=1.0, color=color, alpha=0.62)
        ax.plot(a_plot, upper1, linewidth=1.0, color=color, alpha=0.62)
        # Mean line (primary).
        ax.plot(a_plot, m_plot, linewidth=2.4, color=color, alpha=1.0)

        ax.set_ylim(0.0, 1.0)
        ax.set_theta_offset(math.pi / 2.0)  # first kit at top
        ax.set_theta_direction(-1)  # clockwise
        ax.set_yticks([0.25, 0.5, 0.75, 1.0])
        ax.set_yticklabels([])
        ax.set_xticks(kit_angles)
        if show_labels and PLOT_RADAR_THEME:
            ax.set_xticklabels(kit_labels, fontsize=7, color=PLOT_RADAR_TEXT)
        else:
            ax.set_xticklabels(kit_labels if show_labels else [], fontsize=7)
        if PLOT_RADAR_THEME:
            ax.grid(True, alpha=0.32, linewidth=0.75, color=PLOT_RADAR_GRID)
        else:
            ax.grid(True, alpha=0.20, linewidth=0.6)
        try:
            if PLOT_RADAR_THEME:
                ax.spines["polar"].set_color(PLOT_RADAR_GRID)
                ax.spines["polar"].set_alpha(0.45)
            else:
                ax.spines["polar"].set_alpha(0.25)
        except Exception:
            pass
        full_name = str(sig_name or "").strip()
        if show_labels:
            if PLOT_RADAR_THEME:
                ax.set_title(f"{signal_labels[sig_idx]} - {full_name}", fontsize=9, pad=10, color=PLOT_RADAR_TEXT)
            else:
                ax.set_title(f"{signal_labels[sig_idx]} - {full_name}", fontsize=9, pad=10)
        else:
            ax.set_title("")

    total_cells = grid_rows * grid_cols
    for extra_idx in range(len(signals), total_cells):
        ax = fig.add_subplot(grid_rows, grid_cols, extra_idx + 1)
        ax.axis("off")
    try:
        if show_labels:
            if PLOT_RADAR_THEME:
                fig.suptitle("ML Signal Distribution (Polar)", fontsize=12, y=0.995, color=PLOT_RADAR_TEXT)
            else:
                fig.suptitle("ML Signal Distribution (Polar)", fontsize=12, y=0.995)
            fig.subplots_adjust(left=0.03, right=0.98, top=0.95, bottom=0.05, wspace=0.22, hspace=0.28)
        else:
            # Embedded pane: maximize use of available area.
            fig.subplots_adjust(left=0.002, right=0.998, top=0.998, bottom=0.008, wspace=0.015, hspace=0.02)
    except Exception:
        pass


def render_plot_pixmap(
    *,
    dataset_path: str,
    signal_cols: Sequence[str],
    width_px: int = 980,
    height_px: int = 360,
    show_labels: bool = False,
    grid_rows: int = 0,
) -> Tuple[QPixmap, Dict[str, int]]:
    if Figure is None or FigureCanvasAgg is None:
        raise RuntimeError("matplotlib Agg backend is not available in this runtime.")
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

    dpi = 100.0
    fig_w = max(6.0, float(max(320, int(width_px))) / dpi)
    fig_h = max(3.0, float(max(200, int(height_px))) / dpi)
    fig = Figure(figsize=(fig_w, fig_h), dpi=dpi, tight_layout=True)
    _draw_signal_grid(
        fig,
        kit_names=kit_names,
        signals=signals,
        kit_to_mean=kit_to_mean,
        kit_to_std=kit_to_std,
        show_labels=bool(show_labels),
        grid_rows_override=int(grid_rows),
    )
    canvas = FigureCanvasAgg(fig)
    canvas.draw()
    bio = io.BytesIO()
    if show_labels:
        fig.savefig(bio, format="png", dpi=max(96, int(dpi)), bbox_inches="tight")
    else:
        # Embedded pane: trim inert outer padding so the plot uses pane height.
        fig.savefig(
            bio,
            format="png",
            dpi=max(96, int(dpi)),
            bbox_inches="tight",
            pad_inches=0.01,
        )
    pix = QPixmap()
    pix.loadFromData(bio.getvalue(), "PNG")
    return pix, {
        "rows": int(total_rows),
        "kits": int(len(kit_names)),
        "signals": int(len(signals)),
    }


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

    dialog = _AspectLockedDialog(1260.0 / 860.0, parent)
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
        show_labels=True,
        grid_rows_override=4,
    )

    # Preferred: interactive Qt canvas. Fallback: static PNG if Qt backend unavailable.
    if FigureCanvasQTAgg is not None:
        canvas = FigureCanvasQTAgg(fig)
        canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        canvas.setMinimumSize(1, 1)
        layout.addWidget(canvas, 1)
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
