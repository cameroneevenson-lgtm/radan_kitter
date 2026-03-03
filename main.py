# main.py
# Controlled-migration launcher:
# - Run the full working legacy app today
# - Modularize subsystem-by-subsystem without breaking production behavior

import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QGuiApplication
from radan_kitter_full import Main

def _bring_to_front(w: Main) -> None:
    # Qt-level focus attempt.
    w.setWindowState((w.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
    w.raise_()
    w.activateWindow()

    # Top-most toggle is a reliable fallback on Windows.
    try:
        w.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        w.show()
        w.setWindowFlag(Qt.WindowStaysOnTopHint, False)
        w.show()
        w.raise_()
        w.activateWindow()
    except Exception:
        pass

    # Native foreground request (Windows).
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = int(w.winId())
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _target_screen():
    screens = QGuiApplication.screens()
    if len(screens) >= 2:
        return screens[1]  # "screen 2" by index
    return QGuiApplication.primaryScreen()


def _place_fullscreen_on_screen2(w: Main) -> None:
    screen = _target_screen()
    if screen is None:
        w.showFullScreen()
        return

    # Attach window to target screen first, then enter fullscreen.
    # Avoid explicit setGeometry() here: on Windows, frame margins + min size can
    # trigger noisy QWindowsWindow::setGeometry warnings.
    handle = w.windowHandle()
    if handle is not None:
        try:
            handle.setScreen(screen)
        except Exception:
            pass
    try:
        w.move(screen.geometry().topLeft())
    except Exception:
        pass
    w.showFullScreen()

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    w = Main()
    _place_fullscreen_on_screen2(w)
    QTimer.singleShot(0, lambda: _bring_to_front(w))
    QTimer.singleShot(250, lambda: _bring_to_front(w))
    QTimer.singleShot(800, lambda: _place_fullscreen_on_screen2(w))
    QTimer.singleShot(900, lambda: _bring_to_front(w))
    # Main() already checks sys.argv for .rpd in _startup_open()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
