from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QRadioButton,
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
    table = QTableView()
    window.table = table  # type: ignore[attr-defined]
    table.setAlternatingRowColors(True)
    table.setSortingEnabled(True)
    table.setSelectionBehavior(QTableView.SelectRows)
    table.setSelectionMode(QAbstractItemView.ExtendedSelection)
    table.setMinimumWidth(460)
    table.installEventFilter(window)
    table.viewport().installEventFilter(window)

    pdf_view = PdfPreviewView()
    pdf_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    pdf_view.setStyleSheet("QGraphicsView { background: #0f1114; }")
    window.pdf_view = pdf_view  # type: ignore[attr-defined]

    numpad_legend = QLabel(_build_legend_text(canon_kits))
    numpad_legend.setWordWrap(True)
    numpad_legend.setAlignment(Qt.AlignCenter)
    numpad_legend.setMinimumHeight(92)
    numpad_legend.setStyleSheet(
        "QLabel {"
        " color: #0f1720;"
        " background: #e8eef5;"
        " border: 1px solid #c5d1de;"
        " border-radius: 6px;"
        " padding: 6px 8px;"
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
    packet_mode_group = QButtonGroup(window)
    mode_lbl = QLabel("mode")
    mode_lbl.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
    packet_mode_raster = QRadioButton("Raster")
    packet_mode_vector = QRadioButton("Vector")
    packet_mode_group.addButton(packet_mode_raster, 1)
    packet_mode_group.addButton(packet_mode_vector, 2)
    packet_mode_vector.setChecked(True)
    window.packet_mode_group = packet_mode_group  # type: ignore[attr-defined]
    packet_mode_box = QWidget()
    packet_mode_row = QHBoxLayout(packet_mode_box)
    packet_mode_row.setContentsMargins(0, 0, 0, 0)
    packet_mode_row.setSpacing(4)
    packet_mode_row.addWidget(mode_lbl)
    packet_mode_row.addWidget(packet_mode_raster)
    packet_mode_row.addWidget(packet_mode_vector)

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
    for b in action_buttons:
        b.setMinimumHeight(28)
        b.setMaximumHeight(30)
    packet_mode_box.setMinimumHeight(28)
    packet_mode_box.setMaximumHeight(30)

    logo_label = QLabel("")
    logo_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    logo_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    logo_label.setVisible(False)
    apply_company_logo(logo_label, company_logo_path)
    window.logo_label = logo_label  # type: ignore[attr-defined]

    top_bar = QWidget()
    top_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    top_bar.setFixedHeight(38)
    top = QHBoxLayout(top_bar)
    top.setContentsMargins(0, 0, 0, 0)
    top.setSpacing(6)
    top.addWidget(open_btn)
    top.addWidget(write_rpd_btn)
    top.addWidget(packet_btn)
    top.addWidget(packet_mode_box)
    top.addWidget(prep_kits_btn)
    top.addWidget(ml_log_btn)
    top.addWidget(ml_plot_btn)
    top.addWidget(ml_recompute_btn)
    top.addWidget(rf_suggest_btn)
    top.addWidget(clear_btn)
    top.addStretch(1)
    top.addWidget(logo_label, 0, Qt.AlignRight | Qt.AlignVCenter)
    window.top_bar = top_bar  # type: ignore[attr-defined]

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
    lay.addWidget(hot_reload_bar, 0)
    lay.addWidget(splitter, 1)
    lay.setStretch(0, 0)
    lay.setStretch(1, 0)
    lay.setStretch(2, 1)
    window.setCentralWidget(root)
    window.resize(1850, 1100)
    window.setMinimumSize(1400, 860)
