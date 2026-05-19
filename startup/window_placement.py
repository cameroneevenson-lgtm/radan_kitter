from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication


def bring_to_front(window) -> None:
    window.setWindowState((window.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
    window.raise_()
    window.activateWindow()

    try:
        window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        window.show()
        window.setWindowFlag(Qt.WindowStaysOnTopHint, False)
        window.show()
        window.raise_()
        window.activateWindow()
    except Exception:
        pass

    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = int(window.winId())
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def target_screen():
    screens = QGuiApplication.screens()
    if len(screens) >= 2:
        return screens[1]
    return QGuiApplication.primaryScreen()


def lock_to_screen_maximized(window, screen) -> None:
    if screen is None:
        window.showMaximized()
        return
    try:
        g = screen.availableGeometry()
        window.setMinimumSize(g.size())
        window.setMaximumSize(g.size())
        window.move(g.topLeft())
    except Exception:
        pass
    window.showMaximized()


def place_maximized_on_screen2(window) -> None:
    screen = target_screen()
    if screen is None:
        lock_to_screen_maximized(window, QGuiApplication.primaryScreen())
        return

    handle = window.windowHandle()
    if handle is not None:
        try:
            handle.setScreen(screen)
        except Exception:
            pass
    try:
        window.move(screen.geometry().topLeft())
    except Exception:
        pass
    lock_to_screen_maximized(window, screen)
