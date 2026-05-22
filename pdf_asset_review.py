from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import html
import os
import re
from typing import Callable, Optional, Sequence

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover - exercised only on missing runtime dependency
    fitz = None


@dataclass(frozen=True)
class PdfAssetWarning:
    part_name: str
    pdf_path: str
    expected: str
    found_part_name: str
    found_text: str
    scope: str
    evidence: str


@dataclass(frozen=True)
class PdfAssetReadError:
    part_name: str
    pdf_path: str
    error: str


@dataclass(frozen=True)
class PdfAssetReviewResult:
    action_name: str
    rpd_path: str
    report_path: str
    checked_count: int
    missing_pdf_count: int
    warnings: tuple[PdfAssetWarning, ...]
    read_errors: tuple[PdfAssetReadError, ...]
    canceled: bool = False


@dataclass(frozen=True)
class _PartAlias:
    part_name: str
    alias: str
    pattern: re.Pattern[str]


def _make_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _part_name(part: object) -> str:
    value = str(getattr(part, "part", "") or "").strip()
    if value:
        return value
    sym = str(getattr(part, "sym", "") or "").strip()
    stem = os.path.splitext(os.path.basename(sym))[0]
    return stem.strip()


def _alias_pattern(alias: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", re.IGNORECASE)


def _aliases_for_part(part_name: str) -> tuple[str, ...]:
    clean = str(part_name or "").strip()
    if not clean:
        return ()
    aliases: list[str] = [clean]
    match = re.match(r"^(F\d{3,})[-_\s]+(.+)$", clean, flags=re.IGNORECASE)
    if match:
        aliases.append(match.group(2).strip())
    seen: set[str] = set()
    out: list[str] = []
    for alias in aliases:
        key = alias.casefold()
        if not alias or key in seen:
            continue
        seen.add(key)
        out.append(alias)
    return tuple(out)


def _build_aliases(parts: Sequence[object]) -> tuple[_PartAlias, ...]:
    aliases: list[_PartAlias] = []
    seen: set[tuple[str, str]] = set()
    for part in parts:
        name = _part_name(part)
        if not name:
            continue
        for alias in _aliases_for_part(name):
            key = (name.casefold(), alias.casefold())
            if key in seen:
                continue
            seen.add(key)
            aliases.append(_PartAlias(part_name=name, alias=alias, pattern=_alias_pattern(alias)))
    aliases.sort(key=lambda item: len(item.alias), reverse=True)
    return tuple(aliases)


def _contains_any(text: str, aliases: Sequence[_PartAlias], part_name: str) -> tuple[bool, str]:
    wanted = str(part_name or "").casefold()
    for item in aliases:
        if item.part_name.casefold() != wanted:
            continue
        if item.pattern.search(text):
            return True, item.alias
    return False, ""


def _find_other_part(text: str, aliases: Sequence[_PartAlias], part_name: str) -> Optional[_PartAlias]:
    wanted = str(part_name or "").casefold()
    for item in aliases:
        if item.part_name.casefold() == wanted:
            continue
        if item.pattern.search(text):
            return item
    return None


def _title_window_text(text: str) -> str:
    lines = [line.strip() for line in str(text or "").replace("\r", "\n").splitlines()]
    windows: list[str] = []
    for index, line in enumerate(lines):
        if not re.search(r"\btitle\b", line, flags=re.IGNORECASE):
            continue
        start = max(0, index)
        end = min(len(lines), index + 8)
        windows.extend(line for line in lines[start:end] if line)
    return "\n".join(windows)


def _evidence_line(text: str, pattern: re.Pattern[str]) -> str:
    for raw_line in str(text or "").replace("\r", "\n").splitlines():
        line = " ".join(raw_line.split())
        if line and pattern.search(line):
            return line[:220]
    match = pattern.search(str(text or ""))
    if not match:
        return ""
    start = max(0, match.start() - 80)
    end = min(len(text), match.end() + 80)
    return " ".join(text[start:end].split())[:220]


def _extract_pdf_title_text(pdf_path: str) -> str:
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is not available.")
    with fitz.open(pdf_path) as doc:
        chunks: list[str] = []
        metadata = getattr(doc, "metadata", None) or {}
        title = str(metadata.get("title") or "").strip()
        if title:
            chunks.append(f"PDF metadata Title: {title}")
        if doc.page_count > 0:
            chunks.append(str(doc[0].get_text("text") or ""))
        return "\n".join(chunks)


def _inspect_text_for_warning(
    *,
    part_name: str,
    pdf_path: str,
    text: str,
    aliases: Sequence[_PartAlias],
) -> Optional[PdfAssetWarning]:
    title_text = _title_window_text(text)
    if title_text:
        expected_in_title, _expected_alias = _contains_any(title_text, aliases, part_name)
        other_in_title = _find_other_part(title_text, aliases, part_name)
        if other_in_title is not None and not expected_in_title:
            return PdfAssetWarning(
                part_name=part_name,
                pdf_path=pdf_path,
                expected=part_name,
                found_part_name=other_in_title.part_name,
                found_text=other_in_title.alias,
                scope="PDF Title text",
                evidence=_evidence_line(title_text, other_in_title.pattern),
            )

    expected_in_text, _expected_alias = _contains_any(text, aliases, part_name)
    other_in_text = _find_other_part(text, aliases, part_name)
    if other_in_text is not None and not expected_in_text:
        return PdfAssetWarning(
            part_name=part_name,
            pdf_path=pdf_path,
            expected=part_name,
            found_part_name=other_in_text.part_name,
            found_text=other_in_text.alias,
            scope="first-page PDF text",
            evidence=_evidence_line(text, other_in_text.pattern),
        )
    return None


def scan_pdf_asset_titles(
    parts: Sequence[object],
    *,
    action_name: str = "",
    rpd_path: str = "",
    resolve_asset_fn: Callable[[str, str], Optional[str]],
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    should_cancel_cb: Optional[Callable[[], bool]] = None,
) -> PdfAssetReviewResult:
    aliases = _build_aliases(parts)
    warnings: list[PdfAssetWarning] = []
    read_errors: list[PdfAssetReadError] = []
    text_cache: dict[str, tuple[str, str]] = {}
    checked_count = 0
    missing_pdf_count = 0
    total = len(parts)
    canceled = False

    def _should_cancel() -> bool:
        if should_cancel_cb is None:
            return False
        try:
            return bool(should_cancel_cb())
        except Exception:
            return False

    for index, part in enumerate(parts, start=1):
        part_name = _part_name(part)
        if progress_cb is not None:
            progress_cb(index - 1, total, part_name)
        if _should_cancel():
            canceled = True
            break

        sym_path = str(getattr(part, "sym", "") or "").strip()
        pdf_path = str(resolve_asset_fn(sym_path, ".pdf") or "").strip() if sym_path else ""
        if not pdf_path or not os.path.exists(pdf_path):
            missing_pdf_count += 1
            continue

        checked_count += 1
        cache_key = os.path.normcase(os.path.normpath(pdf_path))
        cached = text_cache.get(cache_key)
        if cached is None:
            try:
                cached = (_extract_pdf_title_text(pdf_path), "")
            except Exception as exc:
                cached = ("", str(exc))
            text_cache[cache_key] = cached

        text, error = cached
        if error:
            read_errors.append(PdfAssetReadError(part_name=part_name, pdf_path=pdf_path, error=error))
            continue

        warning = _inspect_text_for_warning(
            part_name=part_name,
            pdf_path=pdf_path,
            text=text,
            aliases=aliases,
        )
        if warning is not None:
            warnings.append(warning)

        if progress_cb is not None:
            progress_cb(index, total, part_name)

    return PdfAssetReviewResult(
        action_name=action_name,
        rpd_path=rpd_path,
        report_path="",
        checked_count=checked_count,
        missing_pdf_count=missing_pdf_count,
        warnings=tuple(warnings),
        read_errors=tuple(read_errors),
        canceled=canceled,
    )


def write_pdf_asset_review_report(report_path: str, result: PdfAssetReviewResult) -> None:
    report_dir = os.path.dirname(os.path.normpath(report_path))
    if report_dir:
        os.makedirs(report_dir, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write(f"Action: {result.action_name or '(unknown)'}\n")
        handle.write(f"RPD: {result.rpd_path or '(unknown)'}\n")
        handle.write(f"Checked PDFs: {result.checked_count}\n")
        handle.write(f"Missing PDFs skipped: {result.missing_pdf_count}\n")
        handle.write(f"PDF read errors: {len(result.read_errors)}\n")
        handle.write("\n")
        handle.write("PDF title/text warnings:\n")
        if result.warnings:
            for warning in result.warnings:
                handle.write(f"  Expected part: {warning.expected}\n")
                handle.write(f"  PDF: {warning.pdf_path}\n")
                handle.write(f"  Found project part text: {warning.found_text} ({warning.found_part_name})\n")
                handle.write(f"  Scope: {warning.scope}\n")
                handle.write(f"  Evidence: {warning.evidence or '(no nearby text captured)'}\n")
                handle.write("  Review: PDF title/text may not match the expected part.\n")
                handle.write("\n")
        else:
            handle.write("  (none)\n\n")

        handle.write("PDF read errors:\n")
        if result.read_errors:
            for error in result.read_errors:
                handle.write(f"  {error.part_name}: {error.pdf_path}: {error.error}\n")
        else:
            handle.write("  (none)\n")


def _report_html(report_text: str) -> str:
    colors = {
        "base": "#111827",
        "muted": "#475569",
        "green": "#15803D",
        "yellow": "#A16207",
        "red": "#B91C1C",
    }
    active = ""
    rows: list[str] = []
    for line in report_text.splitlines():
        stripped = line.strip()
        if stripped.endswith(":"):
            active = ""
            if stripped.startswith("PDF title/text warnings"):
                active = "yellow"
            elif stripped.startswith("PDF read errors"):
                active = "red"
            color = colors.get(active, colors["base"])
            weight = "700" if active else "600"
        elif stripped == "(none)" and active:
            color = colors["green"]
            weight = "700"
        elif stripped and active:
            color = colors[active]
            weight = "700"
        elif stripped:
            color = colors["base"]
            weight = "400"
        else:
            color = colors["muted"]
            weight = "400"
        rows.append(
            "<div style='white-space: pre-wrap; "
            f"color: {color}; font-weight: {weight};'>"
            f"{html.escape(line) or '&nbsp;'}</div>"
        )
    return (
        "<html><body style='font-family: Consolas, monospace; "
        "font-size: 10pt; background: #FFFFFF;'>"
        + "\n".join(rows)
        + "</body></html>"
    )


def _show_review_dialog(parent, result: PdfAssetReviewResult) -> bool:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QCheckBox,
        QDialog,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPushButton,
        QTextEdit,
        QVBoxLayout,
    )

    class PdfAssetReviewDialog(QDialog):
        def __init__(self, review_result: PdfAssetReviewResult, dialog_parent=None):
            super().__init__(dialog_parent)
            self.result = review_result
            self._acknowledged = False
            self.setWindowTitle("Review PDF Asset Warnings")
            self.setWindowModality(Qt.ApplicationModal)
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)

            title = QLabel("Review required before continuing")
            title.setStyleSheet("font-weight: 700; color: #111827;")
            detail = QLabel(
                f"{len(review_result.warnings)} PDF title/text warning(s) were found. "
                "The app cannot decide whether the files are correct; review the report before continuing."
            )
            detail.setWordWrap(True)
            detail.setStyleSheet("font-weight: 700; color: #A16207;")
            path_label = QLabel(f"Report: {review_result.report_path}")
            path_label.setWordWrap(True)

            self.viewer = QTextEdit()
            self.viewer.setReadOnly(True)
            self.viewer.setLineWrapMode(QTextEdit.NoWrap)
            try:
                with open(review_result.report_path, encoding="utf-8") as handle:
                    report_text = handle.read()
            except OSError as exc:
                report_text = f"Could not read report file:\n{exc}"
            self.viewer.setHtml(_report_html(report_text))

            self.chk_ack = QCheckBox(
                "I have reviewed these PDF asset warnings and understand the app cannot decide whether the files are correct."
            )
            self.chk_ack.stateChanged.connect(self._update_ack_button)

            self.btn_open = QPushButton("Open Report File")
            self.btn_open.clicked.connect(self.open_report)
            self.btn_cancel = QPushButton(f"Cancel {review_result.action_name or 'Operation'}")
            self.btn_cancel.clicked.connect(self.reject)
            self.btn_ack = QPushButton("Acknowledge Warnings")
            self.btn_ack.setEnabled(False)
            self.btn_ack.clicked.connect(self.accept)

            btn_row = QHBoxLayout()
            btn_row.addWidget(self.btn_open)
            btn_row.addWidget(self.btn_cancel)
            btn_row.addWidget(self.btn_ack)

            layout = QVBoxLayout()
            layout.addWidget(title)
            layout.addWidget(detail)
            layout.addWidget(path_label)
            layout.addWidget(self.viewer, 1)
            layout.addWidget(self.chk_ack)
            layout.addLayout(btn_row)
            self.setLayout(layout)
            self.resize(920, 680)

        def _update_ack_button(self) -> None:
            self.btn_ack.setEnabled(self.chk_ack.isChecked())

        def open_report(self) -> None:
            try:
                os.startfile(self.result.report_path)  # type: ignore[attr-defined]
            except Exception as exc:
                QMessageBox.warning(self, "Open Report", str(exc))

        def accept(self) -> None:
            if not self.chk_ack.isChecked():
                QMessageBox.warning(
                    self,
                    "Review Required",
                    "Review the report and check the acknowledgement before continuing.",
                )
                return
            self._acknowledged = True
            super().accept()

        def reject(self) -> None:
            if not self._acknowledged:
                choice = QMessageBox.question(
                    self,
                    "Cancel Operation?",
                    "Close without acknowledging these PDF asset warnings?\n\n"
                    f"{self.result.action_name or 'This operation'} will be canceled.",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if choice != QMessageBox.Yes:
                    return
            super().reject()

        def closeEvent(self, event) -> None:
            if self._acknowledged:
                event.accept()
                return
            choice = QMessageBox.question(
                self,
                "Cancel Operation?",
                "Close without acknowledging these PDF asset warnings?\n\n"
                f"{self.result.action_name or 'This operation'} will be canceled.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if choice == QMessageBox.Yes:
                event.accept()
                return
            event.ignore()

    dialog = PdfAssetReviewDialog(result, parent)
    return dialog.exec() == QDialog.Accepted


def review_pdf_assets_for_action(
    *,
    parent,
    action_name: str,
    parts: Sequence[object],
    rpd_path: str,
    resolve_asset_fn: Callable[[str, str], Optional[str]],
    out_dirname: str = "_out",
) -> bool:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QProgressDialog

    total = len(parts)
    if total <= 0:
        return True

    progress = QProgressDialog("Checking PDF title text...", "Cancel", 0, max(1, total), parent)
    progress.setWindowTitle("PDF Asset Review")
    progress.setWindowModality(Qt.WindowModal)
    progress.setMinimumDuration(0)
    progress.setAutoClose(True)
    progress.setAutoReset(True)

    def _on_progress(done: int, total_count: int, part_name: str) -> None:
        progress.setMaximum(max(1, int(total_count)))
        progress.setValue(max(0, int(done)))
        label = str(part_name or "").strip()
        progress.setLabelText(
            "Checking PDF title text..."
            + (f"\n{label}" if label else "")
        )
        QApplication.processEvents()

    result = scan_pdf_asset_titles(
        parts,
        action_name=action_name,
        rpd_path=rpd_path,
        resolve_asset_fn=resolve_asset_fn,
        progress_cb=_on_progress,
        should_cancel_cb=progress.wasCanceled,
    )
    try:
        progress.close()
    except Exception:
        pass

    if result.canceled:
        return False
    if not result.warnings:
        return True

    rpd_dir = os.path.dirname(os.path.normpath(str(rpd_path or "").strip()))
    report_dir = os.path.join(rpd_dir, str(out_dirname or "_out")) if rpd_dir else str(out_dirname or "_out")
    report_path = os.path.join(report_dir, f"PdfAssetReview_{_make_stamp()}.txt")
    result = replace(result, report_path=report_path)
    write_pdf_asset_review_report(report_path, result)
    return _show_review_dialog(parent, result)
