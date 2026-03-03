# ui_preview_pane.py
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QShortcut
from PySide6.QtWidgets import QWidget, QLabel, QPushButton, QGridLayout, QVBoxLayout, QHBoxLayout

from pdf_preview import PdfPreviewView


class PreviewPaneWidget(QWidget):
    kit_override_requested = Signal(str)  # kit_name

    def __init__(self, kit_names: List[str], kit_to_priority: Dict[str, int], parent=None):
        super().__init__(parent)
        self.kit_names = kit_names
        self.kit_to_priority = kit_to_priority

        self.lbl_header = QLabel("SUGGEST: —      ASSIGNED: —")
        self.lbl_header.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.lbl_outlier = QLabel("")
        self.lbl_outlier.setVisible(False)

        # 3x3 kit buttons
        self.btns: Dict[str, QPushButton] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)

        for idx, kit in enumerate(self.kit_names):
            r, c = divmod(idx, 3)
            b = QPushButton(kit)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=kit: self.kit_override_requested.emit(k))
            self.btns[kit] = b
            grid.addWidget(b, r, c)

        # Numpad mapping for 3x3 kit buttons (physical numpad layout):
        # 7 8 9 -> row 0 (kits 1..3)
        # 4 5 6 -> row 1 (kits 4..6)
        # 1 2 3 -> row 2 (kits 7..9)
        key_to_index = {
            Qt.Key_7: 0, Qt.Key_8: 1, Qt.Key_9: 2,
            Qt.Key_4: 3, Qt.Key_5: 4, Qt.Key_6: 5,
            Qt.Key_1: 6, Qt.Key_2: 7, Qt.Key_3: 8,
        }

        for key, idx in key_to_index.items():
            if 0 <= idx < len(self.kit_names):
                kit = self.kit_names[idx]
                for keypad_only in (True, False):
                    seq = QKeySequence(key | (Qt.KeypadModifier if keypad_only else 0))
                    sc = QShortcut(seq, self)
                    sc.setContext(Qt.WidgetWithChildrenShortcut)
                    sc.activated.connect(lambda k=kit: self.kit_override_requested.emit(k))

        # Renderer-only view
        self.pdf_view = PdfPreviewView()

        layout = QVBoxLayout(self)
        layout.addWidget(self.lbl_header)
        layout.addWidget(self.lbl_outlier)
        layout.addLayout(grid)
        layout.addWidget(self.pdf_view, stretch=1)

        self.setLayout(layout)

    # --- Public API ---

    def set_pdf(self, pdf_path: Optional[str]) -> None:
        self.pdf_view.set_pdf(pdf_path)

    def set_header(
        self,
        suggested: Optional[Tuple[str, float]],   # (kit, conf 0..1) or None
        assigned_kit: Optional[str],
        assigned_priority: Optional[int],
    ) -> None:
        if suggested and suggested[0]:
            sk, conf = suggested
            sug_txt = f"{sk} ({conf:.2f})"
        else:
            sug_txt = "—"

        a = assigned_kit or "—"
        p = assigned_priority if assigned_priority is not None else "—"

        self.lbl_header.setText(f"SUGGEST: {sug_txt}      ASSIGNED: {a}   PRI: {p}")

        # highlight assigned kit
        for kit, b in self.btns.items():
            b.setChecked(kit == assigned_kit)

    def set_outlier_warning(self, text: str) -> None:
        if text:
            self.lbl_outlier.setText(text)
            self.lbl_outlier.setVisible(True)
        else:
            self.lbl_outlier.setText("")
            self.lbl_outlier.setVisible(False)
