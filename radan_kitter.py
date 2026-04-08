# radan_kitter.py  (Production-ready, Option A locked + Donor Kits + RF Predict + GLOBAL ML DEDUPE)
#
# THIS VERSION UPDATES (per your request)
# - ML signals are delegated to ml_pipeline schema columns.
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
#
from __future__ import annotations

import os
import subprocess
import sys
import traceback
from typing import List, Optional

import xml.etree.ElementTree as ET

from PySide6.QtCore import QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMessageBox

import assets
import hot_reload_service
from app_utils import (
    kit_text_for_rpd,
    safe_int_1_9,
    sanitize_kit_name,
)
from ui_parts_table import PartsModel
from ui_numpad_controller import NumpadController
from ui_preview_pane import PreviewCoordinator
from rpd_io import PartRow
import runtime_trace as rt
import ui_actions
import ui_main_events
import ui_main_layout
import ui_table_loader
from config import (
    BAK_DIRNAME,
    BALANCE_KIT,
    CANON_KITS,
    COMPANY_LOGO_PATH,
    DEFAULT_RPD_OPEN_DIR,
    DONOR_TEMPLATE_PATH,
    GLOBAL_DATASET_PATH,
    GLOBAL_RUNS_DIR,
    GLOBAL_RUNTIME_DIR,
    HOT_RELOAD_REQUEST_PATH,
    HOT_RELOAD_RESPONSE_PATH,
    KITS_DIRNAME,
    KIT_ABBR,
    KIT_TO_PRIORITY,
    OUT_DIRNAME,
    RF_FEATURES,
    RF_META_PATH,
    RF_MODEL_PATH,
)


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

    def __init__(self):
        super().__init__()
        self.setWindowTitle("RADAN Kitter")

        self.tree: Optional[ET.ElementTree] = None
        self.parts: List[PartRow] = []
        self.rpd_path: str = ""
        self.model: Optional[PartsModel] = None
        self.numpad_controller: Optional[NumpadController] = None

        ui_main_layout.build_main_layout(
            self,
            canon_kits=CANON_KITS,
            company_logo_path=COMPANY_LOGO_PATH,
            on_open_rpd=self.open_rpd,
            on_choose_asset_root=self.choose_asset_root,
            on_reset_asset_root=self.reset_asset_root,
            on_open_rpd_file=self.open_current_rpd_file,
            on_prepare_kits=self.prepare_kits_only,
            on_write_rpd=self.write_rpd_only,
            on_build_packet=self.build_packet_only,
            on_ml_log=self.run_ml_log,
            on_ml_plot=self.run_ml_signal_plot,
            on_ml_recompute=self.run_ml_recompute_all,
            on_rf_suggest=self.run_rf_suggestions,
            on_clear_selected=self.clear_selected_kits,
            on_numpad_legend_action=self._on_numpad_legend_action,
            on_hot_reload_accept=self.on_hot_reload_accept,
            on_hot_reload_reject=self.on_hot_reload_reject,
        )

        self.preview_coordinator = PreviewCoordinator(
            table=self.table,
            pdf_view=self.pdf_view,
            numpad_legend=self.numpad_legend,
            resolve_asset_fn=assets.resolve_asset,
            canon_kits=CANON_KITS,
            kit_abbr=KIT_ABBR,
            sanitize_kit_name_fn=sanitize_kit_name,
        )
        self._update_numpad_legend(None)
        self._refresh_open_rpd_indicator()
        self._refresh_asset_root_indicator()
        QTimer.singleShot(0, self._refresh_ml_plot_pane)

        self._preview_timer = QTimer()
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self.preview_current)
        self.splitter.splitterMoved.connect(lambda *_: self._preview_timer.start(0))

        self._hot_reload_request_id: str = ""
        self._hot_reload_pending_action: str = ""
        self._set_hot_reload_prompt(
            visible=False,
            message="Hot reload requested.",
            enable_buttons=True,
        )
        self._hot_reload_timer = QTimer(self)
        self._hot_reload_timer.setInterval(400)
        self._hot_reload_timer.timeout.connect(self._poll_hot_reload_request)
        self._hot_reload_timer.start()

        # Trigger preview on mouse + keyboard navigation
        self.table.clicked.connect(lambda *_: self._preview_timer.start(25))
        self.numpad_controller = NumpadController(
            table=self.table,
            get_model=lambda: self.model,
            canon_kits=CANON_KITS,
            kit_to_priority=KIT_TO_PRIORITY,
            sanitize_kit_name_fn=sanitize_kit_name,
            preview_current_cb=self.preview_current,
        )
        self.numpad_controller.install_shortcuts(self)

        # Open RPD passed as first CLI argument (SendTo)
        if len(sys.argv) > 1 and sys.argv[1].lower().endswith('.rpd'):
            try:
                self._load_rpd_path(sys.argv[1])
            except Exception:
                QMessageBox.critical(self, 'Open RPD failed', traceback.format_exc())

    # ----------------- UI events -----------------

    def keyPressEvent(self, e: QKeyEvent) -> None:
        ctl = getattr(self, "numpad_controller", None)
        if ctl is not None and ui_main_events.handle_main_keypress(e, numpad_controller=ctl):
            return
        super().keyPressEvent(e)

    def eventFilter(self, obj, event):
        ctl = getattr(self, "numpad_controller", None)
        if ctl is not None and ui_main_events.handle_space_event_filter(
            obj,
            event,
            table=self.table,
            numpad_controller=ctl,
        ):
            return True
        return super().eventFilter(obj, event)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.model and self.parts:
            self._preview_timer.start(60)

    # ----------------- Core actions -----------------

    def open_rpd(self):
        span = rt.begin("open_rpd_dialog")
        start_dir = os.path.dirname(os.path.normpath(self.rpd_path)) if str(self.rpd_path or "").strip() else ""
        if not start_dir or not os.path.isdir(start_dir):
            start_dir = DEFAULT_RPD_OPEN_DIR if os.path.isdir(DEFAULT_RPD_OPEN_DIR) else ""
        path, _ = QFileDialog.getOpenFileName(self, "Open RPD", start_dir, "RADAN Project (*.rpd)")
        if not path:
            span.skip(reason="user_canceled")
            return
        try:
            self._load_rpd_path(path)
            span.success(rpd_path=path)
        except Exception as exc:
            span.fail(exc, rpd_path=path)
            raise

    def open_current_rpd_file(self) -> None:
        p = os.path.normpath(str(self.rpd_path or "").strip())
        if not p or not os.path.exists(p):
            QMessageBox.information(self, "Open RPD File", "No loaded RPD file.")
            return
        try:
            os.startfile(p)  # type: ignore[attr-defined]
            return
        except Exception:
            pass
        try:
            subprocess.Popen(["explorer.exe", f"/select,{p}"])
        except Exception:
            QMessageBox.warning(self, "Open RPD File", f"Could not open:\n{p}")

    def _load_rpd_path(self, path: str) -> None:
        span = rt.begin("load_rpd_path", rpd_path=path)
        try:
            self.tree, self.parts, self.model = ui_table_loader.load_rpd_into_table(
                path,
                table=self.table,
                canon_kits=CANON_KITS,
                kit_to_priority=KIT_TO_PRIORITY,
                sanitize_kit_name_fn=sanitize_kit_name,
                kit_text_for_rpd_fn=lambda sym, kit: kit_text_for_rpd(sym, kit, KITS_DIRNAME),
                safe_int_1_9_fn=safe_int_1_9,
                on_model_data_changed=lambda: self._update_numpad_legend(self.table.currentIndex().row()),
                on_selection_changed=lambda: self._preview_timer.start(25),
            )
            self.rpd_path = path
            self._refresh_open_rpd_indicator()
            self.preview_current()
            self._refresh_ml_plot_pane()
            span.success(part_count=len(self.parts))
        except Exception as exc:
            span.fail(exc)
            raise

    def _refresh_open_rpd_indicator(self) -> None:
        p = os.path.normpath(str(self.rpd_path or "").strip())
        lbl = getattr(self, "rpd_indicator_label", None)
        if not p:
            self.setWindowTitle("RADAN Kitter")
            if lbl is not None:
                try:
                    lbl.setText("Open RPD: (none)")
                    lbl.setToolTip("")
                except Exception:
                    pass
            return

        base = os.path.basename(p)
        self.setWindowTitle(f"RADAN Kitter - {base}")
        if lbl is not None:
            try:
                lbl.setText(f"Open RPD: {p}")
                lbl.setToolTip(p)
            except Exception:
                pass

    def _refresh_asset_root_indicator(self) -> None:
        lbl = getattr(self, "asset_root_label", None)
        reset_btn = getattr(self, "asset_root_reset_button", None)
        if lbl is None:
            return

        state = assets.get_asset_root_state()
        root = os.path.normpath(str(state.get("root") or "").strip())
        source = str(state.get("source") or "default").strip().lower() or "default"
        override_active = bool(state.get("override_active", False))
        if not root:
            text = "PDF/DXF Root: (none)"
            tip = ""
        elif source == "default":
            text = f"PDF/DXF Root: {root} (default)"
            tip = root
        elif source == "env":
            text = f"PDF/DXF Root: {root} (env override)"
            tip = f"{root}\nSet by RADAN_KITTER_ASSET_ROOT"
        else:
            text = f"PDF/DXF Root: {root} (saved override)"
            tip = root
        try:
            lbl.setText(text)
            lbl.setToolTip(tip)
        except Exception:
            pass
        if reset_btn is not None:
            try:
                reset_btn.setEnabled(override_active)
            except Exception:
                pass

    def choose_asset_root(self) -> None:
        state = assets.get_asset_root_state()
        start_dir = os.path.normpath(str(state.get("root") or "").strip())
        path = QFileDialog.getExistingDirectory(
            self,
            "Select PDF/DXF Root Folder",
            start_dir,
        )
        if not path:
            return

        try:
            assets.set_asset_root_override(path, persist=True, source="saved")
        except Exception:
            QMessageBox.critical(self, "Set PDF/DXF Root failed", traceback.format_exc())
            return

        self._refresh_asset_root_indicator()
        self.preview_current()

    def reset_asset_root(self) -> None:
        try:
            assets.set_asset_root_override(None, persist=True, source="default")
        except Exception:
            QMessageBox.critical(self, "Reset PDF/DXF Root failed", traceback.format_exc())
            return

        self._refresh_asset_root_indicator()
        self.preview_current()

    def _update_numpad_legend(self, row: Optional[int]) -> None:
        self.preview_coordinator.update_numpad_legend(self.model, row)

    def _on_numpad_legend_action(self, href: str) -> None:
        ui_main_events.dispatch_numpad_legend_action(
            href,
            on_assign=self.numpad_controller.on_assign,
            on_clear=self.numpad_controller.on_clear,
            on_accept=self.numpad_controller.on_accept_suggestion,
            on_move=self.numpad_controller.on_move,
        )

    def preview_current(self):
        self.preview_coordinator.preview_current(self.model, self.parts)

    def clear_selected_kits(self) -> None:
        ui_main_events.clear_selected_kits(
            table=self.table,
            model=self.model,
            preview_current_cb=self.preview_current,
        )

    def prepare_kits_only(self):
        ui_actions.run_prepare_kits(
            parent=self,
            tree=self.tree,
            parts=self.parts,
            rpd_path=self.rpd_path,
            donor_template_path=DONOR_TEMPLATE_PATH,
            bak_dirname=BAK_DIRNAME,
            kits_dirname=KITS_DIRNAME,
            kit_to_priority=KIT_TO_PRIORITY,
        )

    def write_rpd_only(self):
        ui_actions.run_write_rpd(
            parent=self,
            tree=self.tree,
            parts=self.parts,
            rpd_path=self.rpd_path,
            bak_dirname=BAK_DIRNAME,
            kits_dirname=KITS_DIRNAME,
            kit_to_priority=KIT_TO_PRIORITY,
        )

    def build_packet_only(self):
        ui_actions.run_build_packet(
            parent=self,
            tree=self.tree,
            parts=self.parts,
            rpd_path=self.rpd_path,
            out_dirname=OUT_DIRNAME,
            resolve_asset_fn=assets.resolve_asset,
            packet_mode="vector",
        )

    def run_rf_suggestions(self):
        ui_actions.run_rf_suggest(
            parent=self,
            tree=self.tree,
            model=self.model,
            parts=self.parts,
            rpd_path=self.rpd_path,
            dataset_path=GLOBAL_DATASET_PATH,
            model_path=RF_MODEL_PATH,
            meta_path=RF_META_PATH,
            feature_cols=RF_FEATURES,
            allowed_labels=CANON_KITS + [BALANCE_KIT],
            resolve_asset_fn=assets.resolve_asset,
            refresh_ui_cb=lambda: self._update_numpad_legend(self.table.currentIndex().row()),
        )

    def run_ml_log(self):
        ui_actions.run_ml_log(
            parent=self,
            tree=self.tree,
            parts=self.parts,
            rpd_path=self.rpd_path,
            resolve_asset_fn=assets.resolve_asset,
            sanitize_kit_name_fn=sanitize_kit_name,
            balance_kit=BALANCE_KIT,
            run_dir=GLOBAL_RUNS_DIR,
            signal_cols=RF_FEATURES,
        )
        self._refresh_ml_plot_pane()

    def run_ml_recompute_all(self):
        ui_actions.run_ml_recompute_all(
            parent=self,
            dataset_path=GLOBAL_DATASET_PATH,
            signal_cols=RF_FEATURES,
            max_workers=2,
        )
        self._refresh_ml_plot_pane()

    def run_ml_signal_plot(self):
        ui_actions.run_ml_signal_plot(
            parent=self,
            dataset_path=GLOBAL_DATASET_PATH,
            signal_cols=RF_FEATURES,
        )
        self._refresh_ml_plot_pane()

    def _refresh_ml_plot_pane(self) -> None:
        ui_actions.refresh_ml_plot_pane(
            parent=self,
            dataset_path=GLOBAL_DATASET_PATH,
            signal_cols=RF_FEATURES,
        )

    def _set_hot_reload_prompt(self, *, visible: bool, message: str, enable_buttons: bool) -> None:
        try:
            self.hot_reload_label.setText(str(message or "Hot reload requested."))
            self.hot_reload_accept_btn.setEnabled(bool(enable_buttons))
            self.hot_reload_reject_btn.setEnabled(bool(enable_buttons))
            self.hot_reload_bar.setVisible(bool(visible))
        except Exception:
            pass

    def _write_hot_reload_response(self, request_id: str, action: str) -> None:
        hot_reload_service.write_response(HOT_RELOAD_RESPONSE_PATH, request_id, action)

    def _poll_hot_reload_request(self) -> None:
        request = hot_reload_service.load_request(HOT_RELOAD_REQUEST_PATH)
        rid = hot_reload_service.request_id(request)
        if not rid:
            self._hot_reload_request_id = ""
            self._hot_reload_pending_action = ""
            self._set_hot_reload_prompt(
                visible=False,
                message="Hot reload requested.",
                enable_buttons=True,
            )
            return

        # New request resets local action latch.
        if rid != self._hot_reload_request_id:
            self._hot_reload_request_id = rid
            self._hot_reload_pending_action = ""

        if self._hot_reload_pending_action == "accept":
            self._set_hot_reload_prompt(
                visible=True,
                message="Hot reload accepted. Waiting for restart...",
                enable_buttons=False,
            )
            return
        if self._hot_reload_pending_action == "reject":
            self._set_hot_reload_prompt(
                visible=False,
                message="Hot reload requested.",
                enable_buttons=True,
            )
            return

        msg = hot_reload_service.format_prompt_message(request)
        self._set_hot_reload_prompt(visible=True, message=msg, enable_buttons=True)

    def on_hot_reload_accept(self) -> None:
        rid = str(self._hot_reload_request_id or "").strip()
        if not rid:
            return
        try:
            self._write_hot_reload_response(rid, "accept")
            self._hot_reload_pending_action = "accept"
            self._set_hot_reload_prompt(
                visible=True,
                message="Hot reload accepted. Waiting for restart...",
                enable_buttons=False,
            )
        except Exception:
            QMessageBox.warning(self, "Hot Reload", "Failed to write reload accept response.")

    def on_hot_reload_reject(self) -> None:
        rid = str(self._hot_reload_request_id or "").strip()
        if not rid:
            return
        try:
            self._write_hot_reload_response(rid, "reject")
            self._hot_reload_pending_action = "reject"
            self._set_hot_reload_prompt(
                visible=False,
                message="Hot reload requested.",
                enable_buttons=True,
            )
        except Exception:
            QMessageBox.warning(self, "Hot Reload", "Failed to write reload reject response.")
