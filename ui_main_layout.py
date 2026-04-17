from __future__ import annotations

import os
from typing import Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from pdf_preview import PdfPreviewView
from ui_numpad_legend import NumpadLegendWidget


def _build_legend_text(canon_kits: Sequence[str]) -> str:
    return (
        f"7: {canon_kits[6]}    8: {canon_kits[7]}    9: {canon_kits[8]}\n"
        f"4: {canon_kits[3]}    5: {canon_kits[4]}    6: {canon_kits[5]}\n"
        f"1: {canon_kits[0]}    2: {canon_kits[1]}    3: {canon_kits[2]}"
    )


def _clamp8(v: float) -> int:
    return max(0, min(255, int(round(v))))


def _boost_logo_tile(tile: QPixmap, *, saturation: float, contrast: float) -> QPixmap:
    img = tile.toImage().convertToFormat(QImage.Format_ARGB32)
    w = img.width()
    h = img.height()
    for y in range(h):
        for x in range(w):
            c = img.pixelColor(x, y)
            a = c.alpha()
            if a <= 0:
                continue
            r = float(c.red())
            g = float(c.green())
            b = float(c.blue())

            # Push chroma and contrast so red/gray remain visible on black.
            gray = 0.299 * r + 0.587 * g + 0.114 * b
            r = gray + (r - gray) * saturation
            g = gray + (g - gray) * saturation
            b = gray + (b - gray) * saturation
            r = (r - 128.0) * contrast + 128.0
            g = (g - 128.0) * contrast + 128.0
            b = (b - 128.0) * contrast + 128.0

            img.setPixelColor(x, y, QColor(_clamp8(r), _clamp8(g), _clamp8(b), a))
    return QPixmap.fromImage(img)


def _trim_transparent_padding(pixmap: QPixmap) -> QPixmap:
    if pixmap.isNull():
        return QPixmap()
    img = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
    w = img.width()
    h = img.height()
    left = w
    top = h
    right = -1
    bottom = -1
    for y in range(h):
        for x in range(w):
            if img.pixelColor(x, y).alpha() <= 0:
                continue
            if x < left:
                left = x
            if y < top:
                top = y
            if x > right:
                right = x
            if y > bottom:
                bottom = y
    if right < left or bottom < top:
        return pixmap
    return pixmap.copy(left, top, right - left + 1, bottom - top + 1)


def _make_tiled_banner_pixmap(
    logo_path: str,
    *,
    height_px: int,
    width_px: int,
    opacity: float = 0.90,
    saturation: float = 1.55,
    contrast: float = 1.30,
) -> QPixmap:
    pm = QPixmap(logo_path) if logo_path else QPixmap()
    if pm.isNull():
        return QPixmap()
    tile = pm.scaledToHeight(max(1, height_px), Qt.SmoothTransformation)
    if tile.isNull():
        return QPixmap()
    tile = _boost_logo_tile(tile, saturation=saturation, contrast=contrast)
    out_w = max(tile.width(), width_px)
    out = QPixmap(out_w, max(1, height_px))
    out.fill(QColor("#000000"))
    p = QPainter(out)
    p.setOpacity(max(0.05, min(1.0, float(opacity))))
    x = 0
    while x < out_w:
        p.drawPixmap(x, 0, tile)
        x += max(1, tile.width())
    p.end()
    return out


def _make_letterbox_texture_pixmap(
    logo_path: str,
    *,
    tile_width_px: int = 2400,
    tile_height_px: int = 140,
    stripe_height_px: int = 42,
) -> QPixmap:
    pm = QPixmap(logo_path) if logo_path else QPixmap()
    if pm.isNull():
        return QPixmap()
    base_tile = pm.scaledToHeight(max(8, int(stripe_height_px)), Qt.SmoothTransformation)
    if base_tile.isNull():
        return QPixmap()
    base_tile = _boost_logo_tile(base_tile, saturation=1.70, contrast=1.38)

    t45 = base_tile.transformed(QTransform().rotate(45.0), Qt.SmoothTransformation)
    t225 = base_tile.transformed(QTransform().rotate(225.0), Qt.SmoothTransformation)
    if t45.isNull():
        t45 = base_tile
    if t225.isNull():
        t225 = base_tile
    t45 = _trim_transparent_padding(t45)
    t225 = _trim_transparent_padding(t225)

    stripe_h = max(base_tile.height(), t45.height(), t225.height())

    out = QPixmap(max(64, int(tile_width_px)), max(int(tile_height_px), stripe_h + 10))
    out.fill(QColor("#000000"))
    p = QPainter(out)
    p.setOpacity(0.98)
    first_tile = t45 if not t45.isNull() else base_tile
    step = max(8, int(first_tile.width() * 0.36))
    x = -(step * 2)
    alt = False
    while x < out.width():
        tile = t45 if not alt else t225
        y = max(0, (out.height() - tile.height()) // 2)
        p.drawPixmap(x, y, tile)
        x += max(8, int(tile.width() * 0.36))
        alt = not alt
    p.end()
    return out


def build_main_layout(
    window: QMainWindow,
    *,
    canon_kits: Sequence[str],
    company_logo_path: str,
    on_open_rpd: Callable[[], None],
    on_choose_asset_root: Callable[[], None],
    on_reset_asset_root: Callable[[], None],
    on_open_rpd_file: Callable[[], None],
    on_prepare_kits: Callable[[], None],
    on_write_rpd: Callable[[], None],
    on_build_packet: Callable[[], None],
    on_ml_log: Callable[[], None],
    on_rf_suggest: Callable[[], None],
    on_clear_selected: Callable[[], None],
    on_numpad_legend_action: Callable[[str], None],
    on_hot_reload_accept: Callable[[], None],
    on_hot_reload_reject: Callable[[], None],
) -> None:
    table = QTableView()
    window.table = table  # type: ignore[attr-defined]
    table.setAlternatingRowColors(True)
    table.setSortingEnabled(True)
    table.setSelectionBehavior(QTableView.SelectRows)
    table.setSelectionMode(QAbstractItemView.ExtendedSelection)
    left_pane_width = 760
    table.setMinimumWidth(left_pane_width)
    table.setMaximumWidth(left_pane_width)
    table.installEventFilter(window)
    table.viewport().installEventFilter(window)
    table.setStyleSheet(
        "QTableView {"
        " font-size: 17px;"
        " font-weight: 500;"
        " color: #0f172a;"
        " background: #fafbfc;"
        " alternate-background-color: #f4f6f8;"
        " gridline-color: #dde3ea;"
        " border: 1px solid #d1d8e0;"
        " border-right: 0px;"
        " selection-background-color: #3b82f6;"
        " selection-color: #ffffff;"
        " }"
        "QTableView::item { padding: 2px 4px; }"
        "QTableView:focus { border: 1px solid #3b82f6; }"
        "QHeaderView::section {"
        " font-size: 17px;"
        " font-weight: 600;"
        " color: #0f172a;"
        " background: #eef2f7;"
        " border: 1px solid #d1d8e0;"
        " padding: 5px 8px;"
        " }"
    )
    table.verticalHeader().setDefaultSectionSize(40)
    table.verticalHeader().setMinimumSectionSize(28)

    pdf_view = PdfPreviewView()
    pdf_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    pdf_view.setStyleSheet("QGraphicsView { background: #000000; }")
    if company_logo_path and os.path.exists(company_logo_path):
        letterbox_texture = _make_letterbox_texture_pixmap(company_logo_path)
        if not letterbox_texture.isNull():
            pdf_view.set_viewport_background(tile=letterbox_texture, fill_color=QColor("#000000"))
        else:
            pdf_view.set_viewport_background(fill_color=QColor("#000000"))
    else:
        pdf_view.set_viewport_background(fill_color=QColor("#000000"))
    window.pdf_view = pdf_view  # type: ignore[attr-defined]

    numpad_legend = NumpadLegendWidget(
        canon_kits=canon_kits,
        on_action=on_numpad_legend_action,
    )
    top_pane_height = max(112, int(numpad_legend.sizeHint().height()))
    numpad_legend.setMinimumHeight(top_pane_height)
    numpad_legend.setMinimumWidth(240)
    numpad_legend.setMaximumWidth(340)
    numpad_legend.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
    window.numpad_legend = numpad_legend  # type: ignore[attr-defined]

    ml_plot_image = QLabel("Run Plot to populate this pane.")
    ml_plot_image.setAlignment(Qt.AlignCenter)
    ml_plot_image.setMinimumHeight(top_pane_height)
    ml_plot_image.setStyleSheet(
        "QLabel {"
        " color: #475569;"
        " background: #ffffff;"
        " border: 1px solid #d7e0eb;"
        " border-radius: 6px;"
        " padding: 4px;"
        " }"
    )
    ml_plot_scroll = QScrollArea()
    ml_plot_scroll.setWidgetResizable(True)
    ml_plot_scroll.setAlignment(Qt.AlignCenter)
    ml_plot_scroll.setFrameShape(QScrollArea.NoFrame)
    ml_plot_scroll.setMinimumHeight(top_pane_height)
    ml_plot_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    ml_plot_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    ml_plot_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    ml_plot_scroll.setWidget(ml_plot_image)
    window.ml_plot_image_label = ml_plot_image  # type: ignore[attr-defined]
    window.ml_plot_scroll = ml_plot_scroll  # type: ignore[attr-defined]


    open_btn = QPushButton("Open RPD")
    open_btn.clicked.connect(on_open_rpd)
    prep_kits_btn = QPushButton("Prepare Kits")
    prep_kits_btn.clicked.connect(on_prepare_kits)
    write_rpd_btn = QPushButton("Write RPD")
    write_rpd_btn.clicked.connect(on_write_rpd)
    ml_log_btn = QPushButton("ML Log")
    ml_log_btn.clicked.connect(on_ml_log)
    rf_suggest_btn = QPushButton("RF Suggest")
    rf_suggest_btn.clicked.connect(on_rf_suggest)
    clear_btn = QPushButton("Clear kits (selected)")
    clear_btn.clicked.connect(on_clear_selected)

    action_buttons = [
        open_btn,
        prep_kits_btn,
        write_rpd_btn,
        ml_log_btn,
        rf_suggest_btn,
        clear_btn,
    ]
    primary_buttons = {open_btn, write_rpd_btn}
    for b in action_buttons:
        b.setMinimumHeight(34)
        b.setMaximumHeight(34)
        weight = "600" if b in primary_buttons else "500"
        b.setStyleSheet(
            "font-size: 17px;"
            f"font-weight: {weight};"
            f"padding: 4px 12px;"
            f"border: 1px solid #cbd5e1;"
            f"border-radius: 5px;"
            f"background: #f8fafc;"
        )

    logo_banner = QLabel("")
    logo_banner.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    logo_banner.setFixedHeight(36)
    logo_banner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    logo_banner.setMinimumWidth(300)
    tiled = _make_tiled_banner_pixmap(company_logo_path, height_px=28, width_px=2600)
    if not tiled.isNull():
        logo_banner.setPixmap(tiled)
        logo_banner.setStyleSheet("QLabel { border: none; background: transparent; }")
        logo_banner.setVisible(True)
    else:
        logo_banner.setVisible(False)
    window.logo_label = logo_banner  # type: ignore[attr-defined]

    top_bar = QWidget()
    top_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    top_bar.setFixedHeight(46)
    top = QHBoxLayout(top_bar)
    top.setContentsMargins(0, 0, 0, 0)
    top.setSpacing(6)
    top.addWidget(open_btn)
    top.addWidget(write_rpd_btn)
    top.addWidget(prep_kits_btn)
    top.addWidget(ml_log_btn)
    top.addWidget(rf_suggest_btn)
    top.addWidget(clear_btn)
    top.addWidget(logo_banner, 1, Qt.AlignRight | Qt.AlignVCenter)
    window.top_bar = top_bar  # type: ignore[attr-defined]

    rpd_indicator = QLabel("Open RPD: (none)")
    rpd_indicator.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    rpd_indicator.setMinimumHeight(24)
    rpd_indicator.setStyleSheet(
        "QLabel {"
        " color: #2a3642;"
        " background: #f2f6fa;"
        " border: 1px solid #d5dee8;"
        " border-radius: 5px;"
        " padding: 2px 8px;"
        " }"
    )
    rpd_indicator.setTextInteractionFlags(Qt.TextSelectableByMouse)
    window.rpd_indicator_label = rpd_indicator  # type: ignore[attr-defined]

    asset_root_bar = QWidget()
    asset_root_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    asset_root_bar.setFixedHeight(36)
    asset_root_row = QHBoxLayout(asset_root_bar)
    asset_root_row.setContentsMargins(0, 0, 0, 0)
    asset_root_row.setSpacing(6)
    asset_root_label = QLabel("PDF/DXF Root: (default)")
    asset_root_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    asset_root_label.setMinimumHeight(24)
    asset_root_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    asset_root_label.setStyleSheet(
        "QLabel {"
        " color: #2a3642;"
        " background: #f2f6fa;"
        " border: 1px solid #d5dee8;"
        " border-radius: 5px;"
        " padding: 2px 8px;"
        " }"
    )
    asset_root_btn = QPushButton("Set PDF/DXF Folder")
    asset_root_reset_btn = QPushButton("Use Default W: Root")
    asset_root_btn.clicked.connect(on_choose_asset_root)
    asset_root_reset_btn.clicked.connect(on_reset_asset_root)
    for button in (asset_root_btn, asset_root_reset_btn):
        button.setMinimumHeight(30)
        button.setMaximumHeight(30)
        button.setStyleSheet(
            "font-size: 15px;"
            "font-weight: 500;"
            "padding: 3px 10px;"
            "border: 1px solid #cbd5e1;"
            "border-radius: 5px;"
            "background: #f8fafc;"
        )
    asset_root_row.addWidget(asset_root_label, 1)
    asset_root_row.addWidget(asset_root_btn, 0)
    asset_root_row.addWidget(asset_root_reset_btn, 0)
    window.asset_root_label = asset_root_label  # type: ignore[attr-defined]
    window.asset_root_button = asset_root_btn  # type: ignore[attr-defined]
    window.asset_root_reset_button = asset_root_reset_btn  # type: ignore[attr-defined]
    window.asset_root_bar = asset_root_bar  # type: ignore[attr-defined]

    hot_reload_bar = QWidget()
    hot_reload_bar.setVisible(False)
    hot_reload_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    hot_reload_bar.setFixedHeight(36)
    hot_reload_bar.setStyleSheet(
        "QWidget { background: #fff4cf; border: 1px solid #d7be6f; border-radius: 6px; }"
        "QLabel { color: #4f3f07; background: transparent; border: none; }"
    )
    hot_reload_row = QHBoxLayout(hot_reload_bar)
    hot_reload_row.setContentsMargins(10, 3, 10, 3)
    hot_reload_row.setSpacing(8)
    hot_reload_label = QLabel("Hot reload requested.")
    hot_reload_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
    hot_reload_accept = QPushButton("Accept Reload")
    hot_reload_reject = QPushButton("Reject Reload")
    hot_reload_accept.setMinimumHeight(26)
    hot_reload_reject.setMinimumHeight(26)
    hot_reload_accept.clicked.connect(on_hot_reload_accept)
    hot_reload_reject.clicked.connect(on_hot_reload_reject)
    hot_reload_row.addWidget(hot_reload_label, 1)
    hot_reload_row.addWidget(hot_reload_accept, 0)
    hot_reload_row.addWidget(hot_reload_reject, 0)
    window.hot_reload_bar = hot_reload_bar  # type: ignore[attr-defined]
    window.hot_reload_label = hot_reload_label  # type: ignore[attr-defined]
    window.hot_reload_accept_btn = hot_reload_accept  # type: ignore[attr-defined]
    window.hot_reload_reject_btn = hot_reload_reject  # type: ignore[attr-defined]

    splitter = QSplitter()
    splitter.setChildrenCollapsible(False)
    splitter.setHandleWidth(0)
    splitter.setStyleSheet("QSplitter::handle { background: transparent; width: 0px; }")
    splitter.addWidget(table)
    right = QWidget()
    right_lay = QVBoxLayout(right)
    right_lay.setContentsMargins(0, 0, 0, 0)
    right_lay.setSpacing(4)
    top_right = QWidget()
    top_right_lay = QHBoxLayout(top_right)
    top_right_lay.setContentsMargins(0, 0, 0, 0)
    top_right_lay.setSpacing(6)
    top_right_lay.addWidget(numpad_legend, 1)
    ml_plot_box = QWidget()
    ml_plot_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    ml_plot_box_lay = QVBoxLayout(ml_plot_box)
    ml_plot_box_lay.setContentsMargins(0, 0, 0, 0)
    ml_plot_box_lay.setSpacing(4)
    ml_plot_box_lay.addWidget(ml_plot_scroll, 1)
    top_right_lay.addWidget(ml_plot_box, 4)
    right_lay.addWidget(top_right, 0)
    right_lay.addWidget(pdf_view, 1)
    splitter.addWidget(right)
    splitter.setStretchFactor(0, 2)
    splitter.setStretchFactor(1, 3)
    splitter.setSizes([760, 1140])
    try:
        handle = splitter.handle(1)
        handle.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        handle.hide()
    except Exception:
        pass
    window.splitter = splitter  # type: ignore[attr-defined]

    root = QWidget()
    lay = QVBoxLayout(root)
    lay.setContentsMargins(6, 4, 6, 6)
    lay.setSpacing(4)
    lay.addWidget(top_bar, 0)
    lay.addWidget(rpd_indicator, 0)
    lay.addWidget(asset_root_bar, 0)
    lay.addWidget(hot_reload_bar, 0)
    lay.addWidget(splitter, 1)
    lay.setStretch(0, 0)
    lay.setStretch(1, 0)
    lay.setStretch(2, 0)
    lay.setStretch(3, 0)
    lay.setStretch(4, 1)
    window.setCentralWidget(root)
    window.resize(1850, 1100)
    window.setMinimumSize(1400, 860)
