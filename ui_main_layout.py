from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from pdf_preview import PdfPreviewView


def _build_legend_text(canon_kits: Sequence[str]) -> str:
    return (
        f"7: {canon_kits[6]}    8: {canon_kits[7]}    9: {canon_kits[8]}\n"
        f"4: {canon_kits[3]}    5: {canon_kits[4]}    6: {canon_kits[5]}\n"
        f"1: {canon_kits[0]}    2: {canon_kits[1]}    3: {canon_kits[2]}"
    )


def _make_tiled_banner_pixmap(logo_path: str, *, height_px: int, width_px: int) -> QPixmap:
    pm = QPixmap(logo_path) if logo_path else QPixmap()
    if pm.isNull():
        return QPixmap()
    tile = pm.scaledToHeight(max(1, height_px), Qt.SmoothTransformation)
    if tile.isNull():
        return QPixmap()
    out_w = max(tile.width(), width_px)
    out = QPixmap(out_w, max(1, height_px))
    out.fill(QColor("#000000"))
    p = QPainter(out)
    p.setOpacity(0.36)
    x = 0
    while x < out_w:
        p.drawPixmap(x, 0, tile)
        x += max(1, tile.width())
    p.end()
    return out


def apply_company_logo(logo_label: QLabel, company_logo_path: str) -> None:
    if not company_logo_path:
        logo_label.clear()
        logo_label.setVisible(False)
        return
    pm = QPixmap(company_logo_path)
    if pm.isNull():
        logo_label.clear()
        logo_label.setVisible(False)
        return
    target_h = 24
    scaled = pm.scaled(220, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    logo_label.setPixmap(scaled)
    logo_label.setMaximumWidth(230)
    logo_label.setFixedHeight(target_h + 2)
    logo_label.setVisible(True)


def build_main_layout(
    window: QMainWindow,
    *,
    canon_kits: Sequence[str],
    company_logo_path: str,
    on_open_rpd: Callable[[], None],
    on_prepare_kits: Callable[[], None],
    on_write_rpd: Callable[[], None],
    on_build_packet: Callable[[], None],
    on_ml_log: Callable[[], None],
    on_ml_plot: Callable[[], None],
    on_ml_recompute: Callable[[], None],
    on_rf_suggest: Callable[[], None],
    on_clear_selected: Callable[[], None],
    on_numpad_legend_action: Callable[[str], None],
    on_hot_reload_accept: Callable[[], None],
    on_hot_reload_reject: Callable[[], None],
) -> None:
    accent = "#3b82f6"

    table = QTableView()
    window.table = table  # type: ignore[attr-defined]
    table.setAlternatingRowColors(True)
    table.setSortingEnabled(True)
    table.setSelectionBehavior(QTableView.SelectRows)
    table.setSelectionMode(QAbstractItemView.ExtendedSelection)
    table.setMinimumWidth(460)
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
    pdf_view.setStyleSheet("QGraphicsView { background: #2b3038; }")
    window.pdf_view = pdf_view  # type: ignore[attr-defined]

    numpad_legend = QLabel(_build_legend_text(canon_kits))
    numpad_legend.setWordWrap(True)
    numpad_legend.setAlignment(Qt.AlignCenter)
    numpad_legend.setMinimumHeight(112)
    numpad_legend.setStyleSheet(
        "QLabel {"
        " color: #0f1720;"
        " background: #ecf2fa;"
        " border: 1px solid #c6d6ea;"
        " border-radius: 6px;"
        " padding: 10px 12px;"
        " }"
    )
    numpad_legend.setTextInteractionFlags(Qt.TextBrowserInteraction)
    numpad_legend.setOpenExternalLinks(False)
    numpad_legend.linkActivated.connect(on_numpad_legend_action)
    window.numpad_legend = numpad_legend  # type: ignore[attr-defined]

    open_btn = QPushButton("Open RPD")
    open_btn.clicked.connect(on_open_rpd)
    prep_kits_btn = QPushButton("Prepare Kits")
    prep_kits_btn.clicked.connect(on_prepare_kits)
    write_rpd_btn = QPushButton("Write RPD")
    write_rpd_btn.clicked.connect(on_write_rpd)
    packet_btn = QPushButton("Build Packet")
    packet_btn.clicked.connect(on_build_packet)
    ml_log_btn = QPushButton("ML Log")
    ml_log_btn.clicked.connect(on_ml_log)
    ml_plot_btn = QPushButton("ML Plot")
    ml_plot_btn.clicked.connect(on_ml_plot)
    ml_recompute_btn = QPushButton("ML Recompute All")
    ml_recompute_btn.clicked.connect(on_ml_recompute)
    rf_suggest_btn = QPushButton("RF Suggest")
    rf_suggest_btn.clicked.connect(on_rf_suggest)
    clear_btn = QPushButton("Clear kits (selected)")
    clear_btn.clicked.connect(on_clear_selected)

    action_buttons = [
        open_btn,
        prep_kits_btn,
        write_rpd_btn,
        packet_btn,
        ml_log_btn,
        ml_plot_btn,
        ml_recompute_btn,
        rf_suggest_btn,
        clear_btn,
    ]
    primary_buttons = {open_btn, write_rpd_btn, packet_btn}
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
    top.addWidget(packet_btn)
    top.addWidget(prep_kits_btn)
    top.addWidget(ml_log_btn)
    top.addWidget(ml_plot_btn)
    top.addWidget(ml_recompute_btn)
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
    splitter.addWidget(table)
    right = QWidget()
    right_lay = QVBoxLayout(right)
    right_lay.setContentsMargins(0, 0, 0, 0)
    right_lay.setSpacing(4)
    right_lay.addWidget(numpad_legend, 0)
    right_lay.addWidget(pdf_view, 1)
    splitter.addWidget(right)
    splitter.setStretchFactor(0, 2)
    splitter.setStretchFactor(1, 3)
    splitter.setSizes([760, 1140])
    window.splitter = splitter  # type: ignore[attr-defined]

    root = QWidget()
    lay = QVBoxLayout(root)
    lay.setContentsMargins(6, 4, 6, 6)
    lay.setSpacing(4)
    lay.addWidget(top_bar, 0)
    lay.addWidget(rpd_indicator, 0)
    lay.addWidget(hot_reload_bar, 0)
    lay.addWidget(splitter, 1)
    lay.setStretch(0, 0)
    lay.setStretch(1, 0)
    lay.setStretch(2, 0)
    lay.setStretch(3, 1)
    window.setCentralWidget(root)
    window.resize(1850, 1100)
    window.setMinimumSize(1400, 860)
