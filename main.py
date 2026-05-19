# main.py
# Controlled-migration launcher:
# - Run the full working legacy app today
# - Modularize subsystem-by-subsystem without breaking production behavior

import sys
import os
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from config import ENG_RELEASE_MAP, W_RELEASE_ROOT
from radan_kitter import Main
from startup.assets_setup import configure_assets
from startup.window_placement import (
    bring_to_front as _bring_to_front,
    place_maximized_on_screen2 as _place_maximized_on_screen2,
)


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    configure_assets(
        w_release_root=W_RELEASE_ROOT,
        eng_release_map=ENG_RELEASE_MAP,
    )
    w = Main()
    _place_maximized_on_screen2(w)
    is_hot_reload = str(os.environ.get("RK_HOT_RELOAD_ACTIVE", "")).strip().lower() in ("1", "true", "yes", "on")
    if is_hot_reload:
        # Avoid repeated foreground steals on hot-reload restarts.
        QTimer.singleShot(800, lambda: _place_maximized_on_screen2(w))
    else:
        QTimer.singleShot(0, lambda: _bring_to_front(w))
        QTimer.singleShot(250, lambda: _bring_to_front(w))
        QTimer.singleShot(800, lambda: _place_maximized_on_screen2(w))
        QTimer.singleShot(900, lambda: _bring_to_front(w))
    # Main() already checks sys.argv for .rpd in _startup_open()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
