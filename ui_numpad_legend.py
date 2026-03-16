from __future__ import annotations

from typing import Callable, Dict, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QPushButton, QSizePolicy, QWidget


class NumpadLegendWidget(QWidget):
    _KEY_TO_INDEX: Dict[int, int] = {
        1: 0, 2: 1, 3: 2,
        4: 3, 5: 4, 6: 5,
        7: 6, 8: 7, 9: 8,
    }

    def __init__(
        self,
        *,
        canon_kits: Sequence[str],
        on_action: Callable[[str], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._canon_kits = list(canon_kits)
        self._on_action = on_action
        self._kit_buttons: Dict[int, QPushButton] = {}
        self._op_buttons: Dict[str, QPushButton] = {}

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.setStyleSheet("background: #ecf2fa; border: 1px solid #c6d6ea; border-radius: 6px;")

        grid = QGridLayout(self)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)

        for row, keys in enumerate(((7, 8, 9), (4, 5, 6), (1, 2, 3))):
            for col, key in enumerate(keys):
                kit_idx = self._KEY_TO_INDEX[key]
                btn = self._make_button(href=f"assign:{kit_idx}")
                self._kit_buttons[kit_idx] = btn
                grid.addWidget(btn, row, col, 1, 1)

        grid.addWidget(self._make_op_button("move_up", "-\nRow Up"), 0, 3, 1, 1)
        grid.addWidget(self._make_op_button("move_down", "+\nRow Down"), 1, 3, 2, 1)
        grid.addWidget(self._make_op_button("clear", "0\nClear"), 3, 0, 1, 2)
        grid.addWidget(self._make_placeholder_button(".\n"), 3, 2, 1, 1)
        grid.addWidget(self._make_op_button("accept", "Enter\nAccept RF"), 3, 3, 1, 1)

        for col in range(4):
            grid.setColumnStretch(col, 1)
        for row in range(4):
            grid.setRowStretch(row, 1)

        self.set_state(highlight_idx=None, selected_idx=None)

    def _make_button(self, *, href: str) -> QPushButton:
        btn = QPushButton(self)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setAutoDefault(False)
        btn.setDefault(False)
        btn.setMinimumHeight(48)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        btn.clicked.connect(lambda _checked=False, target=href: self._on_action(target))
        return btn

    def _make_op_button(self, href: str, text: str) -> QPushButton:
        btn = self._make_button(href=href)
        btn.setText(text)
        self._op_buttons[href] = btn
        return btn

    def _make_placeholder_button(self, text: str) -> QPushButton:
        btn = QPushButton(text, self)
        btn.setEnabled(False)
        btn.setMinimumHeight(48)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        btn.setStyleSheet(self._op_button_style(enabled=False))
        return btn

    @staticmethod
    def _format_kit_label(label: str) -> str:
        text = str(label or "").strip()
        if not text:
            return ""
        if " " in text:
            return "\n".join(part for part in text.split() if part)
        return text

    @staticmethod
    def _kit_button_style(*, selected: bool, highlighted: bool) -> str:
        border = "#9eb1c2"
        background = "#f6f9fc"
        color = "#16222d"
        if selected and highlighted:
            border = "#d8ba5a"
            background = "#2c4f7b"
            color = "#f4f8ff"
        elif selected:
            border = "#7db2ff"
            background = "#2f6feb"
            color = "#f4f8ff"
        elif highlighted:
            border = "#8f7800"
            background = "#ffe16b"
            color = "#121212"
        return (
            "QPushButton {"
            f"border: 2px solid {border};"
            "border-radius: 5px;"
            f"background: {background};"
            f"color: {color};"
            "padding: 6px 4px;"
            "font-size: 11px;"
            "font-weight: 600;"
            "text-align: center;"
            "}"
        )

    @staticmethod
    def _op_button_style(*, enabled: bool) -> str:
        background = "#eef3f8" if enabled else "#f3f6fa"
        color = "#16222d" if enabled else "#90a0af"
        border = "#9eb1c2" if enabled else "#c9d4df"
        return (
            "QPushButton {"
            f"border: 1px solid {border};"
            "border-radius: 5px;"
            f"background: {background};"
            f"color: {color};"
            "padding: 6px 4px;"
            "font-size: 11px;"
            "font-weight: 600;"
            "text-align: center;"
            "}"
        )

    def set_state(self, *, highlight_idx: Optional[int], selected_idx: Optional[int]) -> None:
        for key, kit_idx in self._KEY_TO_INDEX.items():
            btn = self._kit_buttons[kit_idx]
            label = self._canon_kits[kit_idx] if 0 <= kit_idx < len(self._canon_kits) else ""
            btn.setText(f"{key}\n{self._format_kit_label(label)}")
            btn.setStyleSheet(
                self._kit_button_style(
                    selected=(kit_idx == selected_idx),
                    highlighted=(kit_idx == highlight_idx),
                )
            )

        for href, btn in self._op_buttons.items():
            btn.setStyleSheet(self._op_button_style(enabled=btn.isEnabled()))
