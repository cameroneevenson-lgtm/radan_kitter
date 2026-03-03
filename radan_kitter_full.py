# radan_kitter.py  (Production-ready, Option A locked + Donor Kits + RF Predict + GLOBAL ML DEDUPE)
#
# THIS VERSION UPDATES (per your request)
# - ML signals are delegated to ml_pipeline schema columns.
# - Dashboard visuals: heartbeat pulse + higher transparency + subtle static/noise overlay
# - ML scan dashboard updates smoothly for EACH part ingested
# - Artificial slowdown during ML scan so you can watch it “grow” (default on; toggle Turbo)
# - Video capture of the dashboard saved under C:\Tools\_ml_runs\<run_name>.mp4 (fallback .gif)
#
# UI fixes:
# - Preview updates on arrow-key navigation (selectionChanged debounce)
# - Optional one-shot autosize columns (capped) without layout collapse
# - QShortcut import fixed (QtGui)
#
# Notes:
# - Donor kit generator left AS-IS (marginal broken) as requested.
#
# Dependencies:
#   pip install PySide6 pymupdf ezdxf numpy pandas matplotlib
#   pip install scikit-learn joblib
#   (optional for MP4) pip install imageio imageio-ffmpeg
#
from __future__ import annotations

import os
import sys
import traceback
from typing import Callable, Dict, List, Optional, Tuple

import xml.etree.ElementTree as ET

from PySide6.QtCore import (
    Qt, QTimer
)
from PySide6.QtGui import (
    QKeySequence, QShortcut, QKeyEvent, QPixmap
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFileDialog,
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableView,
    QAbstractItemView, QHeaderView, QMessageBox, QSplitter,
    QSizePolicy, QProgressDialog
)

import rpd_io
import pdf_packet
import assets
import sym_io
import rf_model
import ml_runtime
from pdf_preview import PdfPreviewView
from app_utils import (
    now_stamp,
    ensure_dir,
    backup_file,
    safe_int_1_9,
    is_valid_kit_name,
    sanitize_kit_name,
    force_l_drive_path,
)
from ui_parts_table import PartsModel


# -----------------------------
# Configuration
# -----------------------------

CANON_KITS = [
    "Bottoms",
    "Sides",
    "Tops",
    "Backs",
    "Tall Sides",
    "Brackets",
    "Wheel Wells",
    "Walls",
    "Flat Parts",
]
KIT_ABBR = {
    "Bottoms": "BOT",
    "Sides": "SID",
    "Tops": "TOP",
    "Backs": "BAC",
    "Tall Sides": "TAL",
    "Brackets": "BRK",
    "Wheel Wells": "WHL",
    "Walls": "WAL",
    "Flat Parts": "FLT",
    "Balance": "BAL",
}
KIT_TO_PRIORITY = {k: str(i + 1) for i, k in enumerate(CANON_KITS)}  # 1..9

BALANCE_KIT = "Balance"

BAK_DIRNAME = "_bak"
OUT_DIRNAME = "_out"
KITS_DIRNAME = "_kits"
ML_RUNS_DIRNAME = "_ml_runs"
ML_MODELS_DIRNAME = "_ml_models"

TOOLS_DIR = r"C:\Tools"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANY_LOGO_PATH = os.path.join(APP_DIR, "bs-logo.png")
GLOBAL_DATASET_PATH = os.path.join(TOOLS_DIR, "ml_dataset.csv")
GLOBAL_RUNS_DIR = os.path.join(TOOLS_DIR, ML_RUNS_DIRNAME)
GLOBAL_MODELS_DIR = os.path.join(TOOLS_DIR, ML_MODELS_DIRNAME)

DONOR_TEMPLATE_PATH = os.path.join(APP_DIR, "KitDonor-100Instances.sym")
if not os.path.exists(DONOR_TEMPLATE_PATH):
    legacy_tools = os.path.join(TOOLS_DIR, "KitDonor-100Instances.sym")
    DONOR_TEMPLATE_PATH = legacy_tools

W_RELEASE_ROOT = r"W:\LASER\For Battleshield Fabrication"
ENG_RELEASE_MAP: List[Tuple[str, str]] = [
    (r"L:\BATTLESHIELD\F-LARGE FLEET", W_RELEASE_ROOT),
    (r"L:\BATTLESHIELD", W_RELEASE_ROOT),
]
assets.configure_release_mapping(
    w_release_root=W_RELEASE_ROOT,
    eng_release_map=ENG_RELEASE_MAP,
)

PDF_PREVIEW_MIN_W = 420

ML_SIGNAL_COLS = [
    "dxf_perimeter_area_ratio",
    "dxf_convexity_ratio",
    "dxf_internal_void_area_ratio",
    "dxf_entity_count",
    "dxf_exterior_notch_count",
    "dxf_has_interior_polylines",
    "dxf_color_count",
    "dxf_has_nondefault_color",
    "pdf_dim_density",
    "pdf_text_to_geom_ratio",
    "pdf_bendline_score",
    "pdf_ink_gradient_mean",
    "pdf_ink_gradient_std",
    "pdf_ink_gradient_max",
]
# Backward-compat variable names used by existing worker/trainer code.
HUD_SIGNALS_6 = ML_SIGNAL_COLS[:]
HUD_SIGNALS_8 = ML_SIGNAL_COLS[:]
RF_FEATURES = ML_SIGNAL_COLS[:]

RF_MODEL_PATH = os.path.join(GLOBAL_MODELS_DIR, "rf_kit_predictor.joblib")
RF_META_PATH  = os.path.join(GLOBAL_MODELS_DIR, "rf_kit_predictor.meta.json")


# -----------------------------
# Utility helpers
# -----------------------------

def kit_file_path_for_part_sym(part_sym_path: str, kit_label: str) -> str:
    kit_label = sanitize_kit_name(kit_label)
    sym_dir = os.path.dirname(part_sym_path)
    kits_dir = os.path.join(sym_dir, KITS_DIRNAME)
    ensure_dir(kits_dir)
    return os.path.join(kits_dir, f"{kit_label}.sym")

def kit_text_for_rpd(part_sym_path: str, kit_label: str) -> str:
    kit_label = sanitize_kit_name(kit_label)
    if not kit_label:
        return ""
    return kit_file_path_for_part_sym(part_sym_path, kit_label)


# -----------------------------
# RPD parsing
# -----------------------------

PartRow = rpd_io.PartRow

def load_rpd(path: str) -> Tuple[ET.ElementTree, List[PartRow], Dict[str, str]]:
    return rpd_io.load_rpd(path)

def write_rpd_in_place(tree: ET.ElementTree, parts: List[PartRow], rpd_path: str) -> None:
    rpd_io.write_rpd_in_place(tree, parts, rpd_path)


# -----------------------------
# Path resolution (FORCE W)
# -----------------------------

def resolve_asset(sym_path: str, ext: str) -> Optional[str]:
    return assets.resolve_asset(sym_path, ext)


# -----------------------------
# Table model + delegates (UI columns: Part, Kit, Priority, Suggest, Conf, OK, Review)
# -----------------------------


# -----------------------------
# ML stats + extraction
# -----------------------------

Welford = ml_runtime.Welford
MlStats = ml_runtime.MlStats
robust_norm_rows = ml_runtime.robust_norm_rows

def rf_features_for_part(p: PartRow) -> Dict[str, float]:
    return ml_runtime.rf_features_for_part(p, resolve_asset_fn=resolve_asset, feature_cols=RF_FEATURES)


class MlScanWorker(ml_runtime.MlScanWorker):
    def __init__(self, parts: List[PartRow], rpd_path: str, delay_ms: int):
        super().__init__(
            parts=parts,
            rpd_path=rpd_path,
            delay_ms=delay_ms,
            tools_dir=TOOLS_DIR,
            global_runs_dir=GLOBAL_RUNS_DIR,
            canon_kits=CANON_KITS,
            balance_kit=BALANCE_KIT,
            signal_cols=HUD_SIGNALS_8,
            w_release_root=W_RELEASE_ROOT,
            resolve_asset_fn=resolve_asset,
            sanitize_kit_name_fn=sanitize_kit_name,
            now_stamp_fn=now_stamp,
            ensure_dir_fn=ensure_dir,
        )


# -----------------------------
# Dashboard and recorder removed
# -----------------------------


# -----------------------------
# Watermark packet builder (QTY N)
# -----------------------------

def build_watermarked_packet(
    parts: List[PartRow],
    out_pdf_path: str,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> Tuple[int, int]:
    return pdf_packet.build_watermarked_packet(
        parts,
        out_pdf_path,
        resolve_asset_fn=resolve_asset,
        progress_cb=progress_cb,
    )


# -----------------------------
# Donor/SYM IO delegated to sym_io.py
# -----------------------------

def donor_extract_placeholder_paths(donor_text: str) -> Tuple[str, int]:
    return sym_io.donor_extract_placeholder_paths(donor_text)


def build_kit_sym_from_donor(
    donor_path: str,
    member_part_syms: List[str],
    out_kit_sym_path: str,
    backup_dir: Optional[str] = None,
) -> None:
    return sym_io.build_kit_sym_from_donor(
        donor_path=donor_path,
        member_part_syms=member_part_syms,
        out_kit_sym_path=out_kit_sym_path,
        backup_dir=backup_dir,
    )


def group_parts_by_kit_with_balance(parts: List[PartRow]) -> Dict[str, List[PartRow]]:
    return sym_io.group_parts_by_kit(
        parts=parts,
        sanitize_kit_name=sanitize_kit_name,
        is_valid_kit_name=is_valid_kit_name,
    )


def set_sym_attr_109_comment(sym_path: str, comment: str) -> bool:
    return sym_io.set_sym_attr_109_comment(sym_path, comment)


# -----------------------------
# Main app (rebuilt clean)
# -----------------------------

class Main(QMainWindow):
    """
    Clean, stable Main window:
    - Open RPD -> table
    - Crisp single-page PDF preview (cached)
    - Kit assignment via table edit + numpad 3x3 mapping
    - Clear kits (selected) sets priority to 5 (per request)
    - Commit writes kit+priority to ORIGINAL RPD (Option A) + generates kit .sym from donor + creates print packet
    """

    # Numpad layout mapping: 7 8 9 / 4 5 6 / 1 2 3 -> kits 1..9
    NUMPAD_TO_KIT_INDEX = {
        Qt.Key_7: 0, Qt.Key_8: 1, Qt.Key_9: 2,
        Qt.Key_4: 3, Qt.Key_5: 4, Qt.Key_6: 5,
        Qt.Key_1: 6, Qt.Key_2: 7, Qt.Key_3: 8,
    }

    def __init__(self):
        super().__init__()
        self.setWindowTitle("RADAN Kitter")

        self.tree: Optional[ET.ElementTree] = None
        self.parts: List[PartRow] = []
        self.rpd_path: str = ""
        self.model: Optional[PartsModel] = None

        self.table = QTableView()
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setMinimumWidth(460)

        self.pdf_view = PdfPreviewView()
        self.pdf_view.set_dpi(340)
        self.pdf_view.setMinimumWidth(PDF_PREVIEW_MIN_W)
        self.pdf_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.pdf_view.setStyleSheet("QGraphicsView { background: #0f1114; }")

        legend_txt = (
            "Numpad Kit Legend\n"
            f"7: {CANON_KITS[0]}    8: {CANON_KITS[1]}    9: {CANON_KITS[2]}\n"
            f"4: {CANON_KITS[3]}    5: {CANON_KITS[4]}    6: {CANON_KITS[5]}\n"
            f"1: {CANON_KITS[6]}    2: {CANON_KITS[7]}    3: {CANON_KITS[8]}"
        )
        self.numpad_legend = QLabel(legend_txt)
        self.numpad_legend.setWordWrap(True)
        self.numpad_legend.setAlignment(Qt.AlignCenter)
        self.numpad_legend.setMinimumHeight(92)
        self.numpad_legend.setStyleSheet(
            "QLabel {"
            " color: #0f1720;"
            " background: #e8eef5;"
            " border: 1px solid #c5d1de;"
            " border-radius: 6px;"
            " padding: 6px 8px;"
            " }"
        )
        self.numpad_legend.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._update_numpad_legend(None)

        open_btn = QPushButton("Open RPD")
        open_btn.clicked.connect(self.open_rpd)

        prep_kits_btn = QPushButton("Prepare Kits")
        prep_kits_btn.clicked.connect(self.prepare_kits_only)

        write_rpd_btn = QPushButton("Write RPD")
        write_rpd_btn.clicked.connect(self.write_rpd_only)

        packet_btn = QPushButton("Build Packet")
        packet_btn.clicked.connect(self.build_packet_only)

        rf_suggest_btn = QPushButton("RF Suggest")
        rf_suggest_btn.clicked.connect(self.run_rf_suggestions)

        clear_btn = QPushButton("Clear kits (selected)")
        clear_btn.clicked.connect(self.clear_selected_kits)

        self.logo_label = QLabel("")
        self.logo_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.logo_label.setVisible(False)
        self._set_company_logo()

        # Keep top actions explicit and production-focused.
        top = QHBoxLayout()
        top.addWidget(open_btn)
        top.addWidget(prep_kits_btn)
        top.addWidget(write_rpd_btn)
        top.addWidget(packet_btn)
        top.addWidget(rf_suggest_btn)
        top.addWidget(clear_btn)
        top.addStretch(1)
        top.addWidget(self.logo_label, 0, Qt.AlignRight | Qt.AlignVCenter)

        self.splitter = QSplitter()
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.table)
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(4)
        right_lay.addWidget(self.numpad_legend, 0)
        right_lay.addWidget(self.pdf_view, 1)
        self.splitter.addWidget(right)
        # Bias toward preview width so landscape letter pages can use more vertical space.
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setSizes([760, 1140])
        self.splitter.splitterMoved.connect(lambda *_: self._preview_timer.start(0))

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.addLayout(top)
        lay.addWidget(self.splitter)
        self.setCentralWidget(root)
        self.resize(1850, 1100)
        self.setMinimumSize(1400, 860)

        self._preview_timer = QTimer()
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self.preview_current)

        # Trigger preview on mouse + keyboard navigation
        self.table.clicked.connect(lambda *_: self._preview_timer.start(25))
        self._setup_numpad_shortcuts()

        # Open RPD passed as first CLI argument (SendTo)
        if len(sys.argv) > 1 and sys.argv[1].lower().endswith('.rpd'):
            try:
                self._load_rpd_path(sys.argv[1])
            except Exception:
                QMessageBox.critical(self, 'Open RPD failed', traceback.format_exc())


    def _setup_numpad_shortcuts(self) -> None:
        self._numpad_shortcuts: List[QShortcut] = []

        def add_shortcut(seq: QKeySequence, handler) -> None:
            sc = QShortcut(seq, self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(handler)
            self._numpad_shortcuts.append(sc)

        for key, kit_idx in self.NUMPAD_TO_KIT_INDEX.items():
            add_shortcut(QKeySequence(key), lambda i=kit_idx: self._on_numpad_assign(i))
            add_shortcut(QKeySequence(key | Qt.KeypadModifier), lambda i=kit_idx: self._on_numpad_assign(i))

        add_shortcut(QKeySequence(Qt.Key_0), self._on_numpad_clear)
        add_shortcut(QKeySequence(Qt.Key_0 | Qt.KeypadModifier), self._on_numpad_clear)

        add_shortcut(QKeySequence(Qt.Key_Return), self._on_numpad_accept_suggestion)
        add_shortcut(QKeySequence(Qt.Key_Enter), self._on_numpad_accept_suggestion)
        add_shortcut(QKeySequence(Qt.Key_Return | Qt.KeypadModifier), self._on_numpad_accept_suggestion)
        add_shortcut(QKeySequence(Qt.Key_Enter | Qt.KeypadModifier), self._on_numpad_accept_suggestion)

        add_shortcut(QKeySequence(Qt.Key_Minus), lambda: self._on_numpad_move(-1))
        add_shortcut(QKeySequence(Qt.Key_Minus | Qt.KeypadModifier), lambda: self._on_numpad_move(-1))
        add_shortcut(QKeySequence(Qt.Key_Plus), lambda: self._on_numpad_move(+1))
        add_shortcut(QKeySequence(Qt.Key_Plus | Qt.KeypadModifier), lambda: self._on_numpad_move(+1))

    def _set_company_logo(self) -> None:
        if not os.path.exists(COMPANY_LOGO_PATH):
            self.logo_label.clear()
            self.logo_label.setVisible(False)
            return
        pm = QPixmap(COMPANY_LOGO_PATH)
        if pm.isNull():
            self.logo_label.clear()
            self.logo_label.setVisible(False)
            return
        target_h = 34
        scaled = pm.scaledToHeight(target_h, Qt.SmoothTransformation)
        self.logo_label.setPixmap(scaled)
        self.logo_label.setFixedHeight(target_h + 4)
        self.logo_label.setVisible(True)

    def _table_is_editing(self) -> bool:
        return self.table.state() == QAbstractItemView.EditingState

    def _current_row(self) -> int:
        if self.model is None:
            return -1
        idx = self.table.currentIndex()
        if idx.isValid():
            return idx.row()
        if self.model.rowCount() > 0:
            idx0 = self.model.index(0, 0)
            self.table.setCurrentIndex(idx0)
            return 0
        return -1

    def _on_numpad_assign(self, kit_idx: int) -> bool:
        if self.model is None or self._table_is_editing():
            return False
        row = self._current_row()
        if row < 0:
            return False
        self._assign_kit_by_index(row, kit_idx)
        self.preview_current()
        return True

    def _on_numpad_clear(self) -> bool:
        if self.model is None or self._table_is_editing():
            return False
        row = self._current_row()
        if row < 0:
            return False
        kit_idx = self.model.index(row, 1)
        pri_idx = self.model.index(row, 2)
        self.model.setData(kit_idx, "", Qt.EditRole)
        self.model.setData(pri_idx, "5", Qt.EditRole)
        self.preview_current()
        return True

    def _on_numpad_accept_suggestion(self) -> bool:
        if self.model is None or self._table_is_editing():
            return False
        row = self._current_row()
        if row < 0 or row >= len(self.model.rows):
            return False
        sug = sanitize_kit_name(self.model.rows[row].suggested_kit)
        if not sug:
            return False
        kit_idx = self.model.index(row, 1)
        self.model.setData(kit_idx, sug, Qt.EditRole)
        self.model.rows[row].approved = True
        ok_idx = self.model.index(row, 5)
        rv_idx = self.model.index(row, 6)
        self.model.dataChanged.emit(ok_idx, rv_idx)
        self.preview_current()
        return True

    def _on_numpad_move(self, delta: int) -> bool:
        if self.model is None or self._table_is_editing():
            return False
        row = self._current_row()
        if row < 0:
            return False
        new_row = max(0, min(self.model.rowCount() - 1, row + int(delta)))
        col = self.table.currentIndex().column() if self.table.currentIndex().isValid() else 0
        idx = self.model.index(new_row, max(0, col))
        self.table.setCurrentIndex(idx)
        self.table.scrollTo(idx, QTableView.PositionAtCenter)
        self.preview_current()
        return True

    # ----------------- UI events -----------------

    def keyPressEvent(self, e: QKeyEvent) -> None:
        # Numpad kit assignment for CURRENT row
        k = e.key()
        if k in (Qt.Key_Return, Qt.Key_Enter):
            if self._on_numpad_accept_suggestion():
                e.accept()
                return
        if k == Qt.Key_0:
            if self._on_numpad_clear():
                e.accept()
                return
        if k in self.NUMPAD_TO_KIT_INDEX:
            if self._on_numpad_assign(self.NUMPAD_TO_KIT_INDEX[k]):
                e.accept()
                return
        if k == Qt.Key_Minus:
            if self._on_numpad_move(-1):
                e.accept()
                return
        if k == Qt.Key_Plus:
            if self._on_numpad_move(+1):
                e.accept()
                return
        super().keyPressEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.model and self.parts:
            self._preview_timer.start(60)

    def _hook_selection_model(self) -> None:
        """Connect current-row change to preview refresh after model install."""
        sm = self.table.selectionModel()
        if sm is None:
            return
        try:
            sm.currentChanged.disconnect()
        except Exception:
            pass
        sm.currentChanged.connect(lambda *_: self._preview_timer.start(25))

    # ----------------- Core actions -----------------

    def open_rpd(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open RPD", "", "RADAN Project (*.rpd)")
        if not path:
            return
        self._load_rpd_path(path)

    def _load_rpd_path(self, path: str) -> None:
        self.tree, self.parts, debug = load_rpd(path)
        self.rpd_path = path

        self.model = PartsModel(
            self.parts,
            sanitize_kit_name_fn=sanitize_kit_name,
            kit_text_for_rpd_fn=kit_text_for_rpd,
            safe_int_1_9_fn=safe_int_1_9,
            kit_to_priority=KIT_TO_PRIORITY,
        )
        self.table.setModel(self.model)
        self.model.dataChanged.connect(lambda *_: self._update_numpad_legend(self.table.currentIndex().row()))

        self._hook_selection_model()
        # Stable columns: one-time resize then interactive
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)

        # Ensure kit column wide enough for canonical names
        try:
            kit_col = 1  # PartsModel: [Part, Kit, Priority, ...]
            fm = self.table.fontMetrics()
            maxw = max(fm.horizontalAdvance(k) for k in CANON_KITS) + 28
            self.table.setColumnWidth(kit_col, max(self.table.columnWidth(kit_col), maxw))
        except Exception:
            pass

        self.preview_current()

    def _update_numpad_legend(self, row: Optional[int]) -> None:
        key_for_idx = [7, 8, 9, 4, 5, 6, 1, 2, 3]
        highlight_idx = None
        if self.model is not None and row is not None and 0 <= row < len(self.model.rows):
            sug = sanitize_kit_name(self.model.rows[row].suggested_kit)
            if sug in CANON_KITS:
                highlight_idx = CANON_KITS.index(sug)

        def cell(i: int) -> str:
            k = key_for_idx[i]
            kit = CANON_KITS[i]
            abbr = KIT_ABBR.get(kit, kit[:3].upper())
            base = (
                "min-width:74px; border:1px solid #9eb1c2; border-radius:5px;"
                "background:#f6f9fc; color:#16222d; padding:4px 6px;"
            )
            if i == highlight_idx:
                base = (
                    "min-width:74px; border:2px solid #8f7800; border-radius:5px;"
                    "background:#ffe16b; color:#121212; padding:3px 5px;"
                )
            return (
                f'<td style="{base}">'
                f'<div style="font-weight:700; font-size:14px;">{k}</div>'
                f'<div style="font-size:10px;">{abbr}</div>'
                "</td>"
            )

        op = (
            "min-width:74px; border:1px solid #9eb1c2; border-radius:5px;"
            "background:#eef3f8; color:#16222d; padding:4px 6px;"
        )

        html = (
            "<div style=\"text-align:center;\">"
            "<b>Numpad Kit Legend</b><br/>"
            "<table style=\"margin:4px auto; border-collapse:separate; border-spacing:4px;\">"
            f"<tr>{cell(0)}{cell(1)}{cell(2)}<td style=\"{op}\"><b>-</b><br/><span style=\"font-size:10px;\">Row Up</span></td></tr>"
            f"<tr>{cell(3)}{cell(4)}{cell(5)}<td rowspan=\"2\" style=\"{op}\"><b>+</b><br/><span style=\"font-size:10px;\">Row Down</span></td></tr>"
            f"<tr>{cell(6)}{cell(7)}{cell(8)}</tr>"
            f"<tr><td colspan=\"2\" style=\"{op}\"><b>0</b><br/><span style=\"font-size:10px;\">Clear</span></td><td style=\"{op}\"><b>.</b></td><td style=\"{op}\"><b>Enter</b><br/><span style=\"font-size:10px;\">Accept RF</span></td></tr>"
            "</table>"
            "</div>"
        )
        self.numpad_legend.setText(html)

    def preview_current(self):
        if not self.model or not self.parts:
            self.pdf_view.set_pdf(None)
            self._update_numpad_legend(None)
            return
        idx = self.table.currentIndex()
        if not idx.isValid():
            idx = self.model.index(0, 0)
            self.table.setCurrentIndex(idx)
        row = idx.row()
        if row < 0 or row >= len(self.parts):
            self._update_numpad_legend(None)
            return
        self._update_numpad_legend(row)

        part = self.parts[row]
        pdf_path = resolve_asset(part.sym, ".pdf")
        if not pdf_path or not os.path.exists(pdf_path):
            self.pdf_view.set_pdf(None)
            return

        self.pdf_view.set_pdf(pdf_path)

    def _assign_kit_by_index(self, row: int, kit_index: int) -> None:
        if not self.model:
            return
        kit_index = max(0, min(8, int(kit_index)))
        kit = CANON_KITS[kit_index]
        kit_idx = self.model.index(row, 1)
        pri_idx = self.model.index(row, 2)
        self.model.setData(kit_idx, kit, Qt.EditRole)
        self.model.setData(pri_idx, KIT_TO_PRIORITY.get(kit, "9"), Qt.EditRole)

    def clear_selected_kits(self) -> None:
        """Clear kit labels on selected rows; set priority to 5 (per request)."""
        if not self.model:
            return
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            idx = self.table.currentIndex()
            if idx.isValid():
                sel = [self.model.index(idx.row(), 0)]
        rows = sorted({i.row() for i in sel if i.isValid()})
        for r in rows:
            kit_idx = self.model.index(r, 1)
            pri_idx = self.model.index(r, 2)
            self.model.setData(kit_idx, "", Qt.EditRole)
            self.model.setData(pri_idx, "5", Qt.EditRole)
        self.preview_current()

    def _require_rpd_loaded(self) -> bool:
        if not self.tree or not self.rpd_path:
            QMessageBox.information(self, "No RPD", "Open an RPD first.")
            return False
        return True

    def _prepare_kits_impl(self) -> int:
        # sanitize / fill kit_text + priority
        self._apply_balance_and_update_kit_texts()

        # Write kit name into Attr 109 (Comments) on each part .sym from RPD paths.
        base_dir = os.path.dirname(self.rpd_path)
        parts_backup_dir = os.path.join(base_dir, BAK_DIRNAME, "parts")
        ensure_dir(parts_backup_dir)
        touched: set[str] = set()
        for p in self.parts:
            kit_name = sanitize_kit_name(p.kit_label)
            if not kit_name:
                continue
            sym_path = os.path.normpath(p.sym or "")
            if not sym_path or sym_path.lower() in touched:
                continue
            if f"\\{KITS_DIRNAME}\\" in sym_path.lower():
                continue
            touched.add(sym_path.lower())
            if os.path.exists(sym_path):
                backup_file(sym_path, parts_backup_dir)
                set_sym_attr_109_comment(sym_path, kit_name)

        # kits from donor
        kits_backup_dir = os.path.join(base_dir, BAK_DIRNAME, "kits")
        ensure_dir(kits_backup_dir)
        kits_to_parts = group_parts_by_kit_with_balance(self.parts)
        self._create_or_update_kits_from_donor(kits_to_parts, kits_backup_dir)
        return len(kits_to_parts)

    def _write_rpd_impl(self) -> str:
        base_dir = os.path.dirname(self.rpd_path)
        bak_dir = os.path.join(base_dir, BAK_DIRNAME)
        ensure_dir(bak_dir)
        bak_path = backup_file(self.rpd_path, bak_dir)
        write_rpd_in_place(self.tree, self.parts, self.rpd_path)
        return bak_path

    def _build_packet_impl(
        self,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
    ) -> Tuple[str, int, int]:
        base_dir = os.path.dirname(self.rpd_path)
        out_dir = os.path.join(base_dir, OUT_DIRNAME)
        ensure_dir(out_dir)
        packet_path = os.path.join(out_dir, f"PrintPacket_QTY_{now_stamp()}.pdf")
        pages, missing = build_watermarked_packet(self.parts, packet_path, progress_cb=progress_cb)
        return packet_path, pages, missing

    def prepare_kits_only(self):
        if not self._require_rpd_loaded():
            return
        try:
            kit_count = self._prepare_kits_impl()
            QMessageBox.information(
                self,
                "Prepare Kits complete",
                f"Kits generated into '{KITS_DIRNAME}' beside part symbols.\n"
                f"Kits touched: {kit_count}"
            )
        except Exception:
            QMessageBox.critical(self, "Prepare Kits failed", traceback.format_exc())

    def write_rpd_only(self):
        if not self._require_rpd_loaded():
            return
        try:
            # Keep the RPD write aligned with current UI edits before writing.
            self._apply_balance_and_update_kit_texts()
            bak_path = self._write_rpd_impl()
            QMessageBox.information(
                self,
                "Write RPD complete",
                f"RPD written in-place.\nBackup: {bak_path}"
            )
        except Exception:
            QMessageBox.critical(self, "Write RPD failed", traceback.format_exc())

    def build_packet_only(self):
        if not self._require_rpd_loaded():
            return
        try:
            progress = QProgressDialog("Building packet...", None, 0, max(1, len(self.parts)), self)
            progress.setWindowTitle("Build Packet")
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setAutoClose(True)
            progress.setAutoReset(True)

            def on_progress(done: int, total: int, status: str) -> None:
                progress.setMaximum(max(1, int(total)))
                progress.setValue(max(0, int(done)))
                progress.setLabelText(f"Building packet...\n{status}")
                QApplication.processEvents()

            packet_path, pages, missing = self._build_packet_impl(progress_cb=on_progress)
            progress.setValue(progress.maximum())
            QMessageBox.information(
                self,
                "Build Packet complete",
                f"Packet: {packet_path}\nPages: {pages}, Missing PDFs: {missing}"
            )
        except Exception:
            QMessageBox.critical(self, "Build Packet failed", traceback.format_exc())

    def run_rf_suggestions(self):
        if not self._require_rpd_loaded() or not self.model:
            return
        try:
            total = len(self.parts)
            progress = QProgressDialog("RF: extracting features...", "Cancel", 0, max(1, total), self)
            progress.setWindowTitle("RF Suggest")
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setAutoClose(True)
            progress.setAutoReset(True)

            feature_rows: List[Dict[str, float]] = []
            for i, p in enumerate(self.parts, start=1):
                if progress.wasCanceled():
                    return
                feature_rows.append(rf_features_for_part(p))
                progress.setMaximum(max(1, total))
                progress.setValue(i)
                progress.setLabelText(f"RF: extracting features...\n{p.part}")
                QApplication.processEvents()

            progress.setLabelText("RF: loading model...")
            QApplication.processEvents()
            model, encoder, feat_names, source = rf_model.train_or_load_rf(
                dataset_path=GLOBAL_DATASET_PATH,
                model_path=RF_MODEL_PATH,
                meta_path=RF_META_PATH,
                feature_cols=RF_FEATURES,
                allowed_labels=CANON_KITS + [BALANCE_KIT],
                force_train=False,
            )
            preds = rf_model.predict_with_rf(model, encoder, feat_names, feature_rows)
            self.model.set_predictions(preds)
            self._update_numpad_legend(self.table.currentIndex().row())
            progress.setValue(progress.maximum())
            QMessageBox.information(
                self,
                "RF Suggest complete",
                f"Predictions updated for {len(preds)} rows.\nModel source: {source}.",
            )
        except Exception:
            QMessageBox.critical(self, "RF Suggest failed", traceback.format_exc())

    def commit_all(self):
        if not self._require_rpd_loaded():
            return
        try:
            self._prepare_kits_impl()
            self._write_rpd_impl()
            progress = QProgressDialog("COMMIT: Building packet...", None, 0, max(1, len(self.parts)), self)
            progress.setWindowTitle("COMMIT")
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setAutoClose(True)
            progress.setAutoReset(True)

            def on_progress(done: int, total: int, status: str) -> None:
                progress.setMaximum(max(1, int(total)))
                progress.setValue(max(0, int(done)))
                progress.setLabelText(f"COMMIT: Building packet...\n{status}")
                QApplication.processEvents()

            packet_path, pages, missing = self._build_packet_impl(progress_cb=on_progress)
            progress.setValue(progress.maximum())

            QMessageBox.information(
                self,
                "COMMIT complete",
                f"Kits generated into '{KITS_DIRNAME}' beside part symbols.\n"
                f"RPD written in-place (backup created).\n"
                f"Packet: {packet_path}\n"
                f"Pages: {pages}, Missing PDFs: {missing}"
            )
        except Exception:
            QMessageBox.critical(self, "COMMIT failed", traceback.format_exc())

    # ---- existing helpers from original file (kept) ----
    def _apply_balance_and_update_kit_texts(self):
        for p in self.parts:
            k = sanitize_kit_name(p.kit_label)
            if not k:
                p.kit_label = ""
                p.kit_text = ""
                p.priority = str(safe_int_1_9(p.priority or "9"))
                continue
            if not is_valid_kit_name(k):
                k = sanitize_kit_name(k)
                if not k:
                    p.kit_label = ""
                    p.kit_text = ""
                    p.priority = str(safe_int_1_9(p.priority or "9"))
                    continue
            p.kit_label = k
            p.kit_text = kit_text_for_rpd(p.sym, k)
            if k in KIT_TO_PRIORITY:
                p.priority = KIT_TO_PRIORITY[k]
            else:
                p.priority = str(safe_int_1_9(p.priority or "9"))

    def _create_or_update_kits_from_donor(self, kits_to_parts: Dict[str, List[PartRow]], backup_dir: str):
        if not os.path.exists(DONOR_TEMPLATE_PATH):
            raise RuntimeError(f"Donor not found: {DONOR_TEMPLATE_PATH}")
        for kit_label, plist in kits_to_parts.items():
            kit_label = sanitize_kit_name(kit_label)
            if not kit_label:
                continue
            if not is_valid_kit_name(kit_label):
                kit_label = sanitize_kit_name(kit_label)
                if not kit_label:
                    continue
            member_syms = [force_l_drive_path(p.sym) for p in plist]
            out_path = kit_file_path_for_part_sym(plist[0].sym, kit_label)
            build_kit_sym_from_donor(
                donor_path=DONOR_TEMPLATE_PATH,
                member_part_syms=member_syms,
                out_kit_sym_path=out_path,
                backup_dir=backup_dir,
            )
