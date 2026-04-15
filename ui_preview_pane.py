from __future__ import annotations

import os
from typing import Callable, List, Optional, Sequence

from PySide6.QtWidgets import QTableView

from pdf_preview import PdfPreviewView
from rpd_io import PartRow
from ui_parts_table import PartsModel
from ui_numpad_legend import NumpadLegendWidget


class PreviewCoordinator:
    def __init__(
        self,
        *,
        table: QTableView,
        pdf_view: PdfPreviewView,
        numpad_legend: NumpadLegendWidget,
        resolve_asset_fn: Callable[[str, str], Optional[str]],
        canon_kits: Sequence[str],
        sanitize_kit_name_fn: Callable[[str], str],
    ) -> None:
        self._table = table
        self._pdf_view = pdf_view
        self._numpad_legend = numpad_legend
        self._resolve_asset = resolve_asset_fn
        self._canon_kits = list(canon_kits)
        self._sanitize_kit_name = sanitize_kit_name_fn

    def update_numpad_legend(self, model: Optional[PartsModel], row: Optional[int]) -> None:
        highlight_idx = None
        selected_idx = None
        if model is not None and row is not None and 0 <= row < len(model.rows):
            selected = self._sanitize_kit_name(model.rows[row].kit_label)
            if selected in self._canon_kits:
                selected_idx = self._canon_kits.index(selected)
            sug = self._sanitize_kit_name(model.rows[row].suggested_kit)
            if sug in self._canon_kits:
                highlight_idx = self._canon_kits.index(sug)
        self._numpad_legend.set_state(
            highlight_idx=highlight_idx,
            selected_idx=selected_idx,
        )

    def preview_current(self, model: Optional[PartsModel], parts: List[PartRow]) -> None:
        if model is None or not parts:
            self._pdf_view.set_pdf(None)
            self.update_numpad_legend(model, None)
            return

        idx = self._table.currentIndex()
        if not idx.isValid():
            idx = model.index(0, 0)
            self._table.setCurrentIndex(idx)
        row = idx.row()
        if row < 0 or row >= len(parts):
            self.update_numpad_legend(model, None)
            return

        self.update_numpad_legend(model, row)
        part = parts[row]
        pdf_path = self._resolve_asset(part.sym, ".pdf")
        if not pdf_path or not os.path.exists(pdf_path):
            self._pdf_view.set_pdf(None)
            return
        self._pdf_view.set_pdf(pdf_path)
