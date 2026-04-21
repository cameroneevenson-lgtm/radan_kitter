from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton

import ui_main_layout


class MainLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_top_bar_restores_print_packet_button_with_assembly_warning(self) -> None:
        window = QMainWindow()
        ui_main_layout.build_main_layout(
            window,
            canon_kits=[f"Kit {index}" for index in range(1, 10)],
            company_logo_path="",
            on_open_rpd=lambda: None,
            on_choose_asset_root=lambda: None,
            on_reset_asset_root=lambda: None,
            on_open_rpd_file=lambda: None,
            on_prepare_kits=lambda: None,
            on_write_rpd=lambda: None,
            on_build_packet=lambda: None,
            on_ml_log=lambda: None,
            on_rf_suggest=lambda: None,
            on_clear_selected=lambda: None,
            on_numpad_legend_action=lambda _href: None,
            on_hot_reload_accept=lambda: None,
            on_hot_reload_reject=lambda: None,
        )
        buttons = {
            button.text(): button
            for button in window.top_bar.findChildren(QPushButton)
            if button.text()
        }

        self.assertIn("Print Packet", buttons)
        self.assertIn(
            "assembly drawing print pack is still broken",
            buttons["Print Packet"].toolTip().lower(),
        )

        window.close()


if __name__ == "__main__":
    unittest.main()
