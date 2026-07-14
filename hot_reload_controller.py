# hot_reload_controller.py
# Owns the dev-only hot-reload request/response polling state that used to
# live directly on radan_kitter.Main (the production main window). This
# mirrors the HotReloadController shape used by the sibling
# fabrication_flow_dashboard/truck_nest_explorer apps: a small controller
# that the window composes and delegates the banner-polling + accept/reject
# button handlers to, instead of owning that state and logic itself.
#
# hot_reload_service.py already holds the pure request/response file I/O and
# message formatting; this controller adds the Qt-facing state machine on
# top of it (current request id, latched accept/reject action, banner text).

from __future__ import annotations

from typing import Any

import hot_reload_service


class HotReloadController:
    def __init__(self, window: Any, request_path: str, response_path: str) -> None:
        self.window = window
        self.request_path = request_path
        self.response_path = response_path
        self.request_id: str = ""
        self.pending_action: str = ""

    # ----------------- banner -----------------

    def _set_prompt(self, *, visible: bool, message: str, enable_buttons: bool) -> None:
        window = self.window
        try:
            window.hot_reload_label.setText(str(message or "Hot reload requested."))
            window.hot_reload_accept_btn.setEnabled(bool(enable_buttons))
            window.hot_reload_reject_btn.setEnabled(bool(enable_buttons))
            window.hot_reload_bar.setVisible(bool(visible))
        except Exception:
            pass

    def _write_response(self, request_id: str, action: str) -> None:
        hot_reload_service.write_response(self.response_path, request_id, action)

    # ----------------- public API (called by Main) -----------------

    def poll(self) -> None:
        """Called on a timer tick to check for a new/updated reload request."""
        request = hot_reload_service.load_request(self.request_path)
        rid = hot_reload_service.request_id(request)
        if not rid:
            self.request_id = ""
            self.pending_action = ""
            self._set_prompt(
                visible=False,
                message="Hot reload requested.",
                enable_buttons=True,
            )
            return

        # New request resets local action latch.
        if rid != self.request_id:
            self.request_id = rid
            self.pending_action = ""

        if self.pending_action == "accept":
            self._set_prompt(
                visible=True,
                message="Hot reload accepted. Waiting for restart...",
                enable_buttons=False,
            )
            return
        if self.pending_action == "reject":
            self._set_prompt(
                visible=False,
                message="Hot reload requested.",
                enable_buttons=True,
            )
            return

        msg = hot_reload_service.format_prompt_message(request)
        self._set_prompt(visible=True, message=msg, enable_buttons=True)

    def accept(self) -> None:
        """Called by the window's Accept Reload button handler."""
        rid = str(self.request_id or "").strip()
        if not rid:
            return
        try:
            self._write_response(rid, "accept")
            self.pending_action = "accept"
            self._set_prompt(
                visible=True,
                message="Hot reload accepted. Waiting for restart...",
                enable_buttons=False,
            )
        except Exception:
            self._warn("Failed to write reload accept response.")

    def reject(self) -> None:
        """Called by the window's Reject Reload button handler."""
        rid = str(self.request_id or "").strip()
        if not rid:
            return
        try:
            self._write_response(rid, "reject")
            self.pending_action = "reject"
            self._set_prompt(
                visible=False,
                message="Hot reload requested.",
                enable_buttons=True,
            )
        except Exception:
            self._warn("Failed to write reload reject response.")

    def _warn(self, message: str) -> None:
        try:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self.window, "Hot Reload", message)
        except Exception:
            pass
