from __future__ import annotations

import io
import math
import os
from typing import Dict, List, Sequence, Tuple

import pandas as pd
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
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
    kit_names = [str(k) for k in kit_counts.index.tolist()]

    # Per-signal global normalization so mixed-scale features can be compared on one polar chart.
    mins: Dict[str, float] = {}
    maxs: Dict[str, float] = {}
    for s in present_signals:
        col = pd.to_numeric(work[s], errors="coerce")
        finite_vals = col[np.isfinite(col)] if np is not None else col.dropna()
        if len(finite_vals) <= 0:
            mins[s] = float("nan")
            maxs[s] = float("nan")
            continue
        mins[s] = float(finite_vals.min())
        maxs[s] = float(finite_vals.max())

    norm = work[[kit_col] + present_signals].copy()
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

    mean_df = norm.groupby(kit_col, dropna=False)[present_signals].mean(numeric_only=True)
    std_df = norm.groupby(kit_col, dropna=False)[present_signals].std(numeric_only=True).fillna(0.0)
    if mean_df.empty:
        return [], [], {}, {}, total_rows

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
    try:
        fig.set_size_inches(grid_cols * 3.0, grid_rows * 2.8, forward=True)
    except Exception:
        pass
    n_kits = len(kit_names)
    if n_kits <= 0:
        return
    kit_angles = [(2.0 * math.pi * i) / n_kits for i in range(n_kits)]
    closed_angles = kit_angles + [kit_angles[0]]
    signal_labels = _short_signal_labels(signals)

    if matplotlib is not None and hasattr(matplotlib, "colormaps"):
        cmap = matplotlib.colormaps.get_cmap("tab10")
    else:
        import matplotlib.cm as _cm
        cmap = _cm.get_cmap("tab10")

    for sig_idx, _sig_name in enumerate(signals):
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
        ax.set_xticklabels([])
        ax.grid(True, alpha=0.35)
        ax.set_title(signal_labels[sig_idx], fontsize=10, pad=10)

    total_cells = grid_rows * grid_cols
    for extra_idx in range(len(signals), total_cells):
        ax = fig.add_subplot(grid_rows, grid_cols, extra_idx + 1)
        ax.axis("off")
    try:
        fig.tight_layout()
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

    df = pd.read_csv(dataset_path)
    dataset_rows = int(len(df))
    kit_names, signals, kit_to_mean, kit_to_std, total_rows = _normalized_kit_signal_stats(df, signal_cols)
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

    controls = QHBoxLayout()
    controls.setSpacing(8)
    controls.addWidget(QLabel("Rows used:"))
    row_spin = QSpinBox(dialog)
    row_spin.setMinimum(1)
    row_spin.setMaximum(max(1, dataset_rows))
    row_spin.setValue(max(1, dataset_rows))
    row_spin.setSingleStep(1)
    controls.addWidget(row_spin)
    play_btn = QPushButton("Replay", dialog)
    play_btn.setCheckable(True)
    controls.addWidget(play_btn)
    full_btn = QPushButton("Full", dialog)
    controls.addWidget(full_btn)
    status_lbl = QLabel(f"Showing rows: {row_spin.value()}/{max(1, dataset_rows)}")
    controls.addWidget(status_lbl, 1)
    layout.addLayout(controls)

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
        layout.addWidget(canvas, 1)
        canvas.draw_idle()

        timer = QTimer(dialog)
        timer.setInterval(80)

        def _render_rows(n_rows: int) -> None:
            n = max(1, min(int(n_rows), max(1, dataset_rows)))
            sub = df.iloc[:n]
            k2, s2, m2, sd2, _ = _normalized_kit_signal_stats(sub, signal_cols)
            if not k2 or not s2:
                status_lbl.setText(f"Showing rows: {n}/{max(1, dataset_rows)} (no kit/signal data yet)")
                return
            _draw_signal_grid(
                fig,
                kit_names=k2,
                signals=s2,
                kit_to_mean=m2,
                kit_to_std=sd2,
            )
            status_lbl.setText(f"Showing rows: {n}/{max(1, dataset_rows)}")
            canvas.draw_idle()

        def _tick() -> None:
            cur = int(row_spin.value())
            max_rows = max(1, dataset_rows)
            if cur >= max_rows:
                timer.stop()
                play_btn.setChecked(False)
                return
            row_spin.setValue(cur + 1)

        def _toggle_play(on: bool) -> None:
            if on:
                if int(row_spin.value()) >= max(1, dataset_rows):
                    row_spin.setValue(1)
                play_btn.setText("Stop")
                timer.start()
            else:
                play_btn.setText("Replay")
                timer.stop()

        def _set_full() -> None:
            if play_btn.isChecked():
                play_btn.setChecked(False)
            row_spin.setValue(max(1, dataset_rows))

        timer.timeout.connect(_tick)
        play_btn.toggled.connect(_toggle_play)
        full_btn.clicked.connect(_set_full)
        row_spin.valueChanged.connect(lambda v: _render_rows(int(v)))
    else:
        row_spin.setEnabled(False)
        play_btn.setEnabled(False)
        full_btn.setEnabled(False)
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
