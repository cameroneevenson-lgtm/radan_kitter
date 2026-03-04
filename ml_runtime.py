from __future__ import annotations

import math
import traceback
from typing import Callable, Dict, List

import numpy as np

from PySide6.QtCore import QObject, QRunnable, Signal

from rpd_io import PartRow


class Welford:
    __slots__ = ("n", "mean", "M2")

    def __init__(self):
        self.n = 0
        self.mean = 0.0
        self.M2 = 0.0

    def add(self, x: float):
        self.n += 1
        d = x - self.mean
        self.mean += d / self.n
        d2 = x - self.mean
        self.M2 += d * d2

    def variance(self) -> float:
        return self.M2 / (self.n - 1) if self.n > 1 else 0.0


class MlStats:
    def __init__(self, kits: List[str], signals: List[str]):
        self.kits = kits
        self.signals = signals
        self.kit_counts: Dict[str, int] = {k: 0 for k in kits}
        self.stats: Dict[str, Dict[str, Welford]] = {
            k: {s: Welford() for s in signals} for k in kits
        }
        self.total = 0

    def ingest(self, kit: str, feats: Dict[str, float]):
        if kit not in self.kit_counts:
            return
        self.kit_counts[kit] += 1
        self.total += 1
        for s in self.signals:
            self.stats[kit][s].add(float(feats.get(s, 0.0) or 0.0))

    def means_matrix(self) -> np.ndarray:
        S, K = len(self.signals), len(self.kits)
        M = np.zeros((S, K), dtype=float)
        for j, k in enumerate(self.kits):
            for i, s in enumerate(self.signals):
                M[i, j] = self.stats[k][s].mean
        return M

    def vars_matrix(self) -> np.ndarray:
        S, K = len(self.signals), len(self.kits)
        V = np.zeros((S, K), dtype=float)
        for j, k in enumerate(self.kits):
            for i, s in enumerate(self.signals):
                V[i, j] = self.stats[k][s].variance()
        return V

    def separation(self) -> Dict[str, float]:
        eps = 1e-9
        sep = {}
        for s in self.signals:
            means = np.array([self.stats[k][s].mean for k in self.kits], dtype=float)
            within = np.array([self.stats[k][s].variance() for k in self.kits], dtype=float)
            num = float(np.var(means))
            den = float(np.mean(within)) + eps
            sep[s] = num / den
        return sep


def robust_norm_rows(M: np.ndarray) -> np.ndarray:
    out = M.copy()
    for i in range(out.shape[0]):
        row = out[i]
        if np.all(row == 0):
            continue
        p5 = np.percentile(row, 5)
        p95 = np.percentile(row, 95)
        denom = (p95 - p5) if (p95 - p5) > 1e-12 else 1.0
        out[i] = np.clip((row - p5) / denom, 0.0, 1.0)
    return out


def rf_features_for_part(
    p: PartRow,
    resolve_asset_fn: Callable[[str, str], str],
    feature_cols: List[str],
) -> Dict[str, float]:
    pdf = resolve_asset_fn(p.sym, ".pdf")
    dxf = resolve_asset_fn(p.sym, ".dxf")
    try:
        import ml_pipeline
        feats = ml_pipeline.compute_phase2_signals(pdf or "", dxf or "")
        out: Dict[str, float] = {}
        for k in feature_cols:
            v = float(feats.get(k, 0.0) or 0.0)
            out[k] = v if math.isfinite(v) else 0.0
        return out
    except Exception:
        return {k: 0.0 for k in feature_cols}


class MlWorkerSignals(QObject):
    progress = Signal(int, int)
    stats = Signal(object)
    done = Signal(str, str, str)  # run_name, dataset_path, run_dir
    error = Signal(str)


class MlScanWorker(QRunnable):
    def __init__(
        self,
        parts: List[PartRow],
        rpd_path: str,
        delay_ms: int,
        tools_dir: str,
        global_runs_dir: str,
        canon_kits: List[str],
        balance_kit: str,
        signal_cols: List[str],
        w_release_root: str,
        resolve_asset_fn: Callable[[str, str], str],
        sanitize_kit_name_fn: Callable[[str], str],
        now_stamp_fn: Callable[[], str],
        ensure_dir_fn: Callable[[str], None],
    ):
        super().__init__()
        self.parts = parts
        self.rpd_path = rpd_path
        self.delay_ms = int(delay_ms)
        self.tools_dir = tools_dir
        self.global_runs_dir = global_runs_dir
        self.canon_kits = canon_kits
        self.balance_kit = balance_kit
        self.signal_cols = signal_cols
        self.w_release_root = w_release_root
        self.resolve_asset_fn = resolve_asset_fn
        self.sanitize_kit_name_fn = sanitize_kit_name_fn
        self.now_stamp_fn = now_stamp_fn
        self.ensure_dir_fn = ensure_dir_fn

        self.signals = MlWorkerSignals()
        self._stop = False

    def request_stop(self):
        self._stop = True

    def run(self):
        try:
            import ml_pipeline

            self.ensure_dir_fn(self.tools_dir)
            self.ensure_dir_fn(self.global_runs_dir)

            kits = self.canon_kits[:] + [self.balance_kit]
            signals = self.signal_cols[:]
            stats = MlStats(kits, signals)

            def _on_part(item: Dict[str, object]) -> None:
                if str(item.get("status", "")) == "skipped_duplicate":
                    return
                kit = str(item.get("kit_label") or self.balance_kit)
                sig_obj = item.get("signals")
                feats: Dict[str, float] = {}
                if isinstance(sig_obj, dict):
                    for s in signals:
                        try:
                            v = float(sig_obj.get(s, 0.0) or 0.0)
                        except Exception:
                            v = 0.0
                        feats[s] = v if math.isfinite(v) else 0.0
                else:
                    feats = {s: 0.0 for s in signals}
                stats.ingest(kit, feats)
                self.signals.stats.emit(stats)

            summary = ml_pipeline.run_scan_and_log(
                parts=self.parts,
                rpd_path=self.rpd_path,
                resolve_asset_fn=self.resolve_asset_fn,
                sanitize_kit_name_fn=self.sanitize_kit_name_fn,
                balance_kit=self.balance_kit,
                run_dir=self.global_runs_dir,
                delay_ms=self.delay_ms,
                signal_cols=signals,
                should_stop=lambda: self._stop,
                on_progress=lambda i, t: self.signals.progress.emit(int(i), int(t)),
                on_part=_on_part,
                meta_extra={
                    "timestamp": self.now_stamp_fn(),
                    "kits": kits,
                    "signals": signals,
                    "w_release_root": self.w_release_root,
                },
            )

            self.signals.done.emit(
                str(summary.get("run_name", "")),
                str(summary.get("dataset_path", "")),
                str(summary.get("run_dir", self.global_runs_dir)),
            )
        except Exception:
            self.signals.error.emit(traceback.format_exc())
