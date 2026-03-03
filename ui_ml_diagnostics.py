# ui_ml_diagnostics.py
from __future__ import annotations

import os
from typing import List

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QTableWidget, QTableWidgetItem, QTextEdit, QSizePolicy
)

try:
    import ml_pipeline
    DEFAULT_DATASET_PATH = getattr(ml_pipeline, "V2_PATH", r"C:\Tools\ml_dataset_v2.csv")
    TRACKING_COLS = getattr(ml_pipeline, "TRACKING_COLS", [])
    FEATURE_COLS = getattr(ml_pipeline, "FEATURE_COLS", [])
except Exception:
    DEFAULT_DATASET_PATH = r"C:\Tools\ml_dataset_v2.csv"
    TRACKING_COLS = []
    FEATURE_COLS = []


def _safe_int(x) -> int:
    try:
        return int(x)
    except Exception:
        return 0


def _set_item(table: QTableWidget, r: int, c: int, text: str) -> None:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    table.setItem(r, c, it)


class MlDiagnosticsWidget(QWidget):
    """
    Calm diagnostics-only ML panel.

    - Reads ml_dataset_v2.csv
    - Reports dataset health, kit distribution, feature health
    - No training, no prediction, no gimmicks.
    """

    def __init__(self, dataset_path: str = DEFAULT_DATASET_PATH, parent=None):
        super().__init__(parent)
        self.dataset_path = dataset_path

        root = QVBoxLayout(self)

        # Header
        header = QHBoxLayout()
        self.path_label = QLabel(f"Dataset: {self.dataset_path}")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)

        header.addWidget(self.path_label, 1)
        header.addWidget(self.refresh_btn, 0)
        root.addLayout(header)

        # Dataset Health
        self.grp_health = QGroupBox("Dataset health")
        health_layout = QVBoxLayout(self.grp_health)
        self.health_text = QTextEdit()
        self.health_text.setReadOnly(True)
        self.health_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        health_layout.addWidget(self.health_text)
        root.addWidget(self.grp_health)

        # Kit Distribution
        self.grp_kits = QGroupBox("Kit distribution")
        kits_layout = QVBoxLayout(self.grp_kits)
        self.kit_table = QTableWidget(0, 3)
        self.kit_table.setHorizontalHeaderLabels(["kit_label", "count", "percent"])
        self.kit_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.kit_table.setSelectionMode(QTableWidget.NoSelection)
        self.kit_table.setSortingEnabled(True)
        kits_layout.addWidget(self.kit_table)
        root.addWidget(self.grp_kits)

        # Feature Health
        self.grp_feat = QGroupBox("Feature health")
        feat_layout = QVBoxLayout(self.grp_feat)
        self.feat_text = QTextEdit()
        self.feat_text.setReadOnly(True)
        feat_layout.addWidget(self.feat_text)
        root.addWidget(self.grp_feat)

        self.refresh()

    def refresh(self) -> None:
        if not os.path.exists(self.dataset_path):
            self.health_text.setPlainText(
                "Dataset file not found.\n"
                f"Expected: {self.dataset_path}\n\n"
                "Once you log rows, this panel will populate."
            )
            self.kit_table.setRowCount(0)
            self.feat_text.setPlainText("")
            return

        try:
            df = pd.read_csv(self.dataset_path)
        except Exception as e:
            self.health_text.setPlainText(f"Failed to read dataset:\n{e}")
            self.kit_table.setRowCount(0)
            self.feat_text.setPlainText("")
            return

        tracking = TRACKING_COLS[:] if TRACKING_COLS else [
            "schema_version", "timestamp_utc", "rpd_token", "part_name", "kit_label", "pdf_path", "dxf_path"
        ]
        feature_cols = FEATURE_COLS[:] if FEATURE_COLS else [c for c in df.columns if c not in tracking]

        rows = len(df)
        uniq_parts = df["part_name"].astype(str).nunique() if "part_name" in df.columns else 0
        dup_parts = rows - uniq_parts

        missing_pdf = 0
        missing_dxf = 0
        if "pdf_path" in df.columns:
            missing_pdf = _safe_int(((df["pdf_path"].isna()) | (df["pdf_path"].astype(str).str.strip() == "")).sum())
        if "dxf_path" in df.columns:
            missing_dxf = _safe_int(((df["dxf_path"].isna()) | (df["dxf_path"].astype(str).str.strip() == "")).sum())

        feat_df = df[feature_cols] if feature_cols and all(c in df.columns for c in feature_cols) else pd.DataFrame()
        nan_cells = 0
        nan_pct = 0.0
        if not feat_df.empty:
            nan_cells = _safe_int(feat_df.isna().sum().sum())
            total_cells = max(1, int(feat_df.shape[0] * feat_df.shape[1]))
            nan_pct = 100.0 * (nan_cells / total_cells)

        health_lines = [
            f"Rows: {rows}",
            f"Unique part_name: {uniq_parts}",
            f"Duplicate part_name rows: {dup_parts}  (should be 0 with last-wins upsert)",
            f"Missing pdf_path: {missing_pdf}",
            f"Missing dxf_path: {missing_dxf}",
            f"Feature columns: {len(feature_cols)}",
            f"NaN feature cells: {nan_cells} ({nan_pct:.1f}%)",
        ]
        self.health_text.setPlainText("\n".join(health_lines))

        self._populate_kit_table(df)
        self._populate_feature_health(df, feature_cols)

    def _populate_kit_table(self, df: pd.DataFrame) -> None:
        if "kit_label" not in df.columns or len(df) == 0:
            self.kit_table.setRowCount(0)
            return

        vc = df["kit_label"].astype(str).fillna("").replace("nan", "").value_counts(dropna=False)
        total = max(1, int(vc.sum()))

        rows = []
        for kit, cnt in vc.items():
            pct = 100.0 * (int(cnt) / total)
            rows.append((kit, int(cnt), f"{pct:.1f}%"))

        self.kit_table.setSortingEnabled(False)
        self.kit_table.setRowCount(len(rows))
        for r, (kit, cnt, pct) in enumerate(rows):
            _set_item(self.kit_table, r, 0, kit)
            _set_item(self.kit_table, r, 1, str(cnt))
            _set_item(self.kit_table, r, 2, pct)
        self.kit_table.setSortingEnabled(True)
        self.kit_table.resizeColumnsToContents()

    def _populate_feature_health(self, df: pd.DataFrame, feature_cols: List[str]) -> None:
        if not feature_cols or len(df) == 0:
            self.feat_text.setPlainText("")
            return

        cols_present = [c for c in feature_cols if c in df.columns]
        if not cols_present:
            self.feat_text.setPlainText("No feature columns found.")
            return

        feat = df[cols_present]

        nan_rate = feat.isna().mean()
        high_nan = nan_rate.sort_values(ascending=False).head(20)

        zero_var = []
        for c in cols_present:
            s = feat[c].dropna()
            if len(s) <= 1:
                continue
            if float(s.nunique()) <= 1.0:
                zero_var.append(c)
        zero_var = zero_var[:20]

        lines = []
        lines.append("High-NaN features (top 20):")
        for c, r in high_nan.items():
            lines.append(f"  {c}: {100.0 * float(r):.1f}% NaN")
        lines.append("")
        lines.append("Zero-variance features (top 20):")
        if zero_var:
            for c in zero_var:
                lines.append(f"  {c}")
        else:
            lines.append("  (none detected)")

        self.feat_text.setPlainText("\n".join(lines))
