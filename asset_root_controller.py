# asset_root_controller.py
# Owns the PDF/DXF asset-root override UI that used to live directly on
# radan_kitter.Main: refreshing the root indicator label/tooltips, and the
# Choose/Reset button handlers. Composed by Main (see radan_kitter.py),
# mirroring the same window-delegate shape as HotReloadController -
# Main still exposes choose_asset_root/reset_asset_root as thin
# pass-throughs since ui_main_layout.build_main_layout wires those buttons
# directly to those method names on the window.

from __future__ import annotations

import os
import traceback
from typing import Any

from PySide6.QtWidgets import QFileDialog, QMessageBox

import assets


class AssetRootController:
    def __init__(self, window: Any) -> None:
        self.window = window

    def refresh_indicator(self) -> None:
        window = self.window
        lbl = getattr(window, "asset_root_label", None)
        choose_btn = getattr(window, "asset_root_button", None)
        reset_btn = getattr(window, "asset_root_reset_button", None)
        if lbl is None:
            return

        state = assets.get_asset_root_state()
        root = os.path.normpath(str(state.get("root") or "").strip())
        source = str(state.get("source") or "default").strip().lower() or "default"
        override_active = bool(state.get("override_active", False))
        root_hint = root or "(none)"
        source_hint = "default W: root"
        if source == "env":
            source_hint = "env override root"
        elif source != "default":
            source_hint = "saved override root"
        lookup_tip = (
            f"Current root: {root_hint}\n"
            f"Source: {source_hint}\n\n"
            "Lookup order:\n"
            "1. Matching release folders under the current root\n"
            "2. Same folder as the .sym file\n"
            "3. The .sym file's Parts subfolder\n\n"
            "Preview, packet, RF suggest, and ML log all use this fast lookup.\n"
            "No recursive crawl is done after these checks."
        )
        if not root:
            text = "PDF/DXF Root: (none)"
            tip = lookup_tip
        elif source == "default":
            text = f"PDF/DXF Root: {root} (default)"
            tip = lookup_tip
        elif source == "env":
            text = f"PDF/DXF Root: {root} (env override)"
            tip = lookup_tip + "\n\nSet by RADAN_KITTER_ASSET_ROOT."
        else:
            text = f"PDF/DXF Root: {root} (saved override)"
            tip = lookup_tip
        try:
            lbl.setText(text)
            lbl.setToolTip(tip)
        except Exception:
            pass
        if choose_btn is not None:
            try:
                choose_btn.setToolTip(
                    tip + "\n\nChoose a different root if the PDFs or DXFs live somewhere else."
                )
            except Exception:
                pass
        if reset_btn is not None:
            try:
                reset_btn.setEnabled(override_active)
                reset_btn.setToolTip(
                    tip + "\n\nReset back to the default W: release root."
                )
            except Exception:
                pass

    def choose(self) -> None:
        window = self.window
        state = assets.get_asset_root_state()
        start_dir = os.path.normpath(str(state.get("root") or "").strip())
        path = QFileDialog.getExistingDirectory(
            window,
            "Select PDF/DXF Root Folder",
            start_dir,
        )
        if not path:
            return

        try:
            assets.set_asset_root_override(path, persist=True, source="saved")
        except Exception:
            QMessageBox.critical(window, "Set PDF/DXF Root failed", traceback.format_exc())
            return

        self.refresh_indicator()
        window.preview_current()

    def reset(self) -> None:
        window = self.window
        try:
            assets.set_asset_root_override(None, persist=True, source="default")
        except Exception:
            QMessageBox.critical(window, "Reset PDF/DXF Root failed", traceback.format_exc())
            return

        self.refresh_indicator()
        window.preview_current()
