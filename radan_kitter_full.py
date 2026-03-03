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
from typing import List, Optional

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
import assets
import kit_service
import packet_service
import rf_service
from pdf_preview import PdfPreviewView
from app_utils import (
    kit_text_for_rpd,
    safe_int_1_9,
    sanitize_kit_name,
)
from ui_parts_table import PartsModel
from ui_numpad_legend import build_numpad_legend_html
from config import (
    BAK_DIRNAME,
    BALANCE_KIT,
    CANON_KITS,
    COMPANY_LOGO_PATH,
    DONOR_TEMPLATE_PATH,
    ENG_RELEASE_MAP,
    GLOBAL_DATASET_PATH,
    KITS_DIRNAME,
    KIT_ABBR,
    KIT_TO_PRIORITY,
    OUT_DIRNAME,
    RF_FEATURES,
    RF_META_PATH,
    RF_MODEL_PATH,
    W_RELEASE_ROOT,
)


# -----------------------------
# Configuration
# -----------------------------

assets.configure_release_mapping(
    w_release_root=W_RELEASE_ROOT,
    eng_release_map=ENG_RELEASE_MAP,
)

PartRow = rpd_io.PartRow


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
        self.pdf_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.pdf_view.setStyleSheet("QGraphicsView { background: #0f1114; }")

        legend_txt = (
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

        action_buttons = [open_btn, prep_kits_btn, write_rpd_btn, packet_btn, rf_suggest_btn, clear_btn]
        for b in action_buttons:
            b.setMinimumHeight(28)
            b.setMaximumHeight(30)

        self.logo_label = QLabel("")
        self.logo_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.logo_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.logo_label.setVisible(False)
        self._set_company_logo()

        # Keep top actions explicit and production-focused.
        self.top_bar = QWidget()
        self.top_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.top_bar.setFixedHeight(38)
        top = QHBoxLayout(self.top_bar)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
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
        lay.setContentsMargins(6, 4, 6, 6)
        lay.setSpacing(4)
        lay.addWidget(self.top_bar, 0)
        lay.addWidget(self.splitter, 1)
        lay.setStretch(0, 0)
        lay.setStretch(1, 1)
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
        target_h = 24
        scaled = pm.scaled(220, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.logo_label.setPixmap(scaled)
        self.logo_label.setMaximumWidth(230)
        self.logo_label.setFixedHeight(target_h + 2)
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
        self.tree, self.parts, _debug = rpd_io.load_rpd(path)
        self.rpd_path = path

        self.model = PartsModel(
            self.parts,
            sanitize_kit_name_fn=sanitize_kit_name,
            kit_text_for_rpd_fn=lambda sym, kit: kit_text_for_rpd(sym, kit, KITS_DIRNAME),
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
        highlight_idx = None
        if self.model is not None and row is not None and 0 <= row < len(self.model.rows):
            sug = sanitize_kit_name(self.model.rows[row].suggested_kit)
            if sug in CANON_KITS:
                highlight_idx = CANON_KITS.index(sug)
        self.numpad_legend.setText(
            build_numpad_legend_html(
                canon_kits=CANON_KITS,
                kit_abbr=KIT_ABBR,
                highlight_idx=highlight_idx,
            )
        )

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
        pdf_path = assets.resolve_asset(part.sym, ".pdf")
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

    def prepare_kits_only(self):
        if not self._require_rpd_loaded():
            return
        try:
            kit_count = kit_service.prepare_kits(
                self.parts,
                rpd_path=self.rpd_path,
                donor_template_path=DONOR_TEMPLATE_PATH,
                bak_dirname=BAK_DIRNAME,
                kits_dirname=KITS_DIRNAME,
                kit_to_priority=KIT_TO_PRIORITY,
            )
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
            kit_service.apply_balance_and_update_kit_texts(
                self.parts,
                kits_dirname=KITS_DIRNAME,
                kit_to_priority=KIT_TO_PRIORITY,
            )
            bak_path = kit_service.write_rpd_with_backup(
                self.tree,
                self.parts,
                rpd_path=self.rpd_path,
                bak_dirname=BAK_DIRNAME,
            )
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

            packet_path, pages, missing = packet_service.build_packet(
                self.parts,
                rpd_path=self.rpd_path,
                out_dirname=OUT_DIRNAME,
                resolve_asset_fn=assets.resolve_asset,
                progress_cb=on_progress,
            )
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

            def on_progress(done: int, total: int, status: str) -> None:
                progress.setMaximum(max(1, total))
                progress.setValue(max(0, done))
                progress.setLabelText(status)
                QApplication.processEvents()

            preds, source = rf_service.run_rf_suggestions(
                self.parts,
                dataset_path=GLOBAL_DATASET_PATH,
                model_path=RF_MODEL_PATH,
                meta_path=RF_META_PATH,
                feature_cols=RF_FEATURES,
                allowed_labels=CANON_KITS + [BALANCE_KIT],
                resolve_asset_fn=assets.resolve_asset,
                progress_cb=on_progress,
                should_cancel_cb=progress.wasCanceled,
            )
            if source == "canceled":
                return
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
