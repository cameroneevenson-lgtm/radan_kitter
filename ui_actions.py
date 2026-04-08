from __future__ import annotations

import os
import subprocess
import time
import traceback
from typing import Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QWidget

import kit_service
import ml_pipeline
import packet_service
import rf_service
import runtime_trace as rt
import ui_ml_signal_plot
from config import (
    PACKET_TEMP_MAX_PAGES,
    PACKET_TEMP_FIRST_PAGE_ONLY,
    PACKET_TEMP_LOCAL_OUTPUT_DIR,
    PACKET_TEMP_LOCAL_OUTPUT_ENABLED,
)
from rpd_io import PartRow
from ui_parts_table import PartsModel

def _require_rpd_loaded(parent: QWidget, tree, rpd_path: str) -> bool:
    if not tree or not rpd_path:
        QMessageBox.information(parent, "No RPD", "Open an RPD first.")
        return False
    return True


def _open_output_file_when_ready(
    path: str,
    *,
    initial_delay_ms: int = 2200,
    retry_delay_ms: int = 1200,
    retries: int = 6,
) -> None:
    target = os.path.normpath(str(path or "").strip())
    if not target:
        return
    retries_left = {"count": max(0, int(retries))}

    def _attempt_open() -> None:
        if not os.path.exists(target):
            if retries_left["count"] > 0:
                retries_left["count"] -= 1
                QTimer.singleShot(max(100, int(retry_delay_ms)), _attempt_open)
            return
        try:
            os.startfile(target)  # type: ignore[attr-defined]
            return
        except Exception:
            pass
        if retries_left["count"] > 0:
            retries_left["count"] -= 1
            QTimer.singleShot(max(100, int(retry_delay_ms)), _attempt_open)
            return
        try:
            subprocess.Popen(["explorer.exe", f"/select,{target}"])
        except Exception:
            pass

    QTimer.singleShot(max(0, int(initial_delay_ms)), _attempt_open)


def run_prepare_kits(
    *,
    parent: QWidget,
    tree,
    parts: List[PartRow],
    rpd_path: str,
    donor_template_path: str,
    bak_dirname: str,
    kits_dirname: str,
    kit_to_priority: Dict[str, str],
) -> bool:
    span = rt.begin("prepare_kits", rpd_path=rpd_path, part_count=len(parts))
    if not _require_rpd_loaded(parent, tree, rpd_path):
        span.skip(reason="no_rpd")
        return False
    try:
        progress = QProgressDialog("Preparing kits...", None, 0, max(1, len(parts)), parent)
        progress.setWindowTitle("Prepare Kits")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setAutoReset(True)

        def on_progress(done: int, total: int, status: str) -> None:
            progress.setMaximum(max(1, int(total)))
            progress.setValue(max(0, int(done)))
            progress.setLabelText(f"Preparing kits...\n{status}")
            span.progress(done, total, status)
            QApplication.processEvents()

        kit_count = kit_service.prepare_kits(
            parts,
            rpd_path=rpd_path,
            donor_template_path=donor_template_path,
            bak_dirname=bak_dirname,
            kits_dirname=kits_dirname,
            kit_to_priority=kit_to_priority,
            progress_cb=on_progress,
        )
        progress.setValue(progress.maximum())
        QMessageBox.information(
            parent,
            "Prepare Kits complete",
            f"Kits generated into '{kits_dirname}' beside part symbols.\n"
            f"Kits touched: {kit_count}",
        )
        span.success(kit_count=int(kit_count))
        return True
    except Exception as exc:
        span.fail(exc)
        QMessageBox.critical(parent, "Prepare Kits failed", traceback.format_exc())
        return False


def run_write_rpd(
    *,
    parent: QWidget,
    tree,
    parts: List[PartRow],
    rpd_path: str,
    bak_dirname: str,
    kits_dirname: str,
    kit_to_priority: Dict[str, str],
) -> bool:
    span = rt.begin("write_rpd", rpd_path=rpd_path, part_count=len(parts))
    if not _require_rpd_loaded(parent, tree, rpd_path):
        span.skip(reason="no_rpd")
        return False
    try:
        kit_service.apply_balance_and_update_kit_texts(
            parts,
            kits_dirname=kits_dirname,
            kit_to_priority=kit_to_priority,
        )
        bak_path = kit_service.write_rpd_with_backup(
            tree,
            parts,
            rpd_path=rpd_path,
            bak_dirname=bak_dirname,
        )
        QMessageBox.information(
            parent,
            "Write RPD complete",
            f"RPD written in-place.\nBackup: {bak_path}",
        )
        span.success(backup_path=bak_path)
        return True
    except Exception as exc:
        span.fail(exc)
        QMessageBox.critical(parent, "Write RPD failed", traceback.format_exc())
        return False


def run_build_packet(
    *,
    parent: QWidget,
    tree,
    parts: List[PartRow],
    rpd_path: str,
    out_dirname: str,
    resolve_asset_fn: Callable[[str, str], Optional[str]],
    packet_mode: str = "raster",
) -> bool:
    if bool(getattr(parent, "_rk_build_packet_running", False)):
        return False

    page_cap: Optional[int] = None
    if bool(PACKET_TEMP_FIRST_PAGE_ONLY):
        page_cap = 1
    try:
        cfg_cap = int(PACKET_TEMP_MAX_PAGES)
    except Exception:
        cfg_cap = 0
    if cfg_cap > 0:
        page_cap = cfg_cap if page_cap is None else min(page_cap, cfg_cap)

    ordered_parts = packet_service.sort_packet_parts(parts)
    build_parts = list(ordered_parts[:page_cap]) if page_cap is not None else list(ordered_parts)
    if not build_parts:
        return False
    effective_out_dir = (
        str(PACKET_TEMP_LOCAL_OUTPUT_DIR)
        if bool(PACKET_TEMP_LOCAL_OUTPUT_ENABLED)
        else str(out_dirname)
    )

    mode = str(packet_mode or "raster").strip().lower()
    if mode not in ("raster", "vector"):
        mode = "raster"

    span = rt.begin(
        "build_packet",
        rpd_path=rpd_path,
        part_count=len(build_parts),
        first_page_only=bool(PACKET_TEMP_FIRST_PAGE_ONLY),
        page_cap=(int(page_cap) if page_cap is not None else 0),
        output_dir=effective_out_dir,
        suppress_layer_0=True,
        workers="auto",
        mode=mode,
    )
    if not _require_rpd_loaded(parent, tree, rpd_path):
        span.skip(reason="no_rpd")
        return False
    setattr(parent, "_rk_build_packet_running", True)

    progress: Optional[QProgressDialog] = None
    ticker: Optional[QTimer] = None
    try:
        progress = QProgressDialog("Building packet...", "Cancel", 0, max(1, len(build_parts)), parent)
        progress.setWindowTitle("Build Packet")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setAutoReset(True)
        started_at = time.perf_counter()
        last_done = 0
        last_total = max(1, len(build_parts))
        last_status = "Starting packet build"

        def _fmt_elapsed(seconds: float) -> str:
            sec = max(0, int(seconds))
            h = sec // 3600
            m = (sec % 3600) // 60
            s = sec % 60
            if h > 0:
                return f"{h:02d}:{m:02d}:{s:02d}"
            return f"{m:02d}:{s:02d}"

        def _refresh_progress_label() -> None:
            progress.setMaximum(max(1, int(last_total)))
            progress.setValue(max(0, int(last_done)))
            elapsed_txt = _fmt_elapsed(time.perf_counter() - started_at)
            progress.setLabelText(
                f"Building packet... {int(last_done)}/{int(last_total)} | {elapsed_txt} | Mode: {mode}\n{last_status}"
            )

        ticker = QTimer(progress)
        ticker.setInterval(250)
        ticker.timeout.connect(_refresh_progress_label)
        ticker.start()

        def on_progress(done: int, total: int, status: str) -> None:
            nonlocal last_done, last_total, last_status
            last_done = int(done)
            last_total = max(1, int(total))
            last_status = str(status)
            _refresh_progress_label()
            span.progress(done, total, status)
            QApplication.processEvents()

        _refresh_progress_label()
        packet_path, pages, missing = packet_service.build_packet(
            build_parts,
            rpd_path=rpd_path,
            out_dirname=effective_out_dir,
            resolve_asset_fn=resolve_asset_fn,
            progress_cb=on_progress,
            should_cancel_cb=progress.wasCanceled,
            render_mode=mode,
        )
        ticker.stop()
        progress.setValue(progress.maximum())
        progress.close()
        _open_output_file_when_ready(packet_path)
        span.success(packet_path=packet_path, pages=int(pages), missing=int(missing))
        return True
    except packet_service.PacketBuildCanceled:
        span.skip(reason="user_canceled")
        if progress is not None:
            try:
                progress.cancel()
            except Exception:
                pass
            progress.close()
        return False
    except packet_service.PacketBuildEmpty as exc:
        span.skip(reason="no_packet_pages", missing=int(getattr(exc, "missing", 0) or 0))
        if progress is not None:
            progress.close()
        QMessageBox.information(
            parent,
            "Build Packet",
            (
                f"{exc}\n"
                f"Missing PDFs: {int(getattr(exc, 'missing', 0) or 0)}"
            ),
        )
        return False
    except Exception as exc:
        span.fail(exc)
        if progress is not None:
            progress.close()
        QMessageBox.critical(parent, "Build Packet failed", traceback.format_exc())
        return False
    finally:
        if ticker is not None:
            try:
                ticker.stop()
            except Exception:
                pass
        setattr(parent, "_rk_build_packet_running", False)


def run_rf_suggest(
    *,
    parent: QWidget,
    tree,
    model: Optional[PartsModel],
    parts: List[PartRow],
    rpd_path: str,
    dataset_path: str,
    model_path: str,
    meta_path: str,
    feature_cols: Sequence[str],
    allowed_labels: Sequence[str],
    resolve_asset_fn: Callable[[str, str], Optional[str]],
    refresh_ui_cb: Callable[[], None],
) -> None:
    span = rt.begin("rf_suggest", rpd_path=rpd_path, part_count=len(parts))
    if not _require_rpd_loaded(parent, tree, rpd_path) or model is None:
        span.skip(reason="no_rpd_or_model")
        return
    try:
        total = len(parts)
        progress = QProgressDialog("RF: extracting features...", "Cancel", 0, max(1, total), parent)
        progress.setWindowTitle("RF Suggest")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setAutoReset(True)

        def on_progress(done: int, total: int, status: str) -> None:
            progress.setMaximum(max(1, total))
            progress.setValue(max(0, done))
            progress.setLabelText(status)
            span.progress(done, total, status)
            QApplication.processEvents()

        preds, source = rf_service.run_rf_suggestions(
            parts,
            dataset_path=dataset_path,
            model_path=model_path,
            meta_path=meta_path,
            feature_cols=feature_cols,
            allowed_labels=allowed_labels,
            resolve_asset_fn=resolve_asset_fn,
            progress_cb=on_progress,
            should_cancel_cb=progress.wasCanceled,
        )
        if source == "canceled":
            span.skip(reason="canceled")
            return
        model.set_predictions(preds)
        refresh_ui_cb()
        progress.setValue(progress.maximum())
        predicted_rows = sum(1 for label, _conf in preds if str(label or "").strip())
        skipped_rows = max(0, len(parts) - predicted_rows)
        QMessageBox.information(
            parent,
            "RF Suggest complete",
            (
                f"Predictions updated for {predicted_rows} rows.\n"
                f"Rows skipped (missing PDF): {skipped_rows}.\n"
                f"Model source: {source}."
            ),
        )
        span.success(pred_count=int(predicted_rows), skipped_missing_pdf_rows=int(skipped_rows), source=str(source))
    except Exception as exc:
        span.fail(exc)
        QMessageBox.critical(parent, "RF Suggest failed", traceback.format_exc())


def run_ml_log(
    *,
    parent: QWidget,
    tree,
    parts: List[PartRow],
    rpd_path: str,
    resolve_asset_fn: Callable[[str, str], Optional[str]],
    sanitize_kit_name_fn: Callable[[str], str],
    balance_kit: str,
    run_dir: str,
    signal_cols: Sequence[str],
) -> None:
    span = rt.begin("ml_log", rpd_path=rpd_path, part_count=len(parts))
    if not _require_rpd_loaded(parent, tree, rpd_path):
        span.skip(reason="no_rpd")
        return
    try:
        total = len(parts)
        progress = QProgressDialog("ML: scanning and logging...", "Cancel", 0, max(1, total), parent)
        progress.setWindowTitle("ML Log")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setAutoReset(True)

        def on_progress(done: int, total: int) -> None:
            progress.setMaximum(max(1, int(total)))
            progress.setValue(max(0, int(done)))
            progress.setLabelText(f"ML: scanning and logging...\n{int(done)}/{int(total)}")
            span.progress(done, total, "ml_log")
            QApplication.processEvents()

        summary = ml_pipeline.run_scan_and_log(
            parts=parts,
            rpd_path=rpd_path,
            resolve_asset_fn=resolve_asset_fn,
            sanitize_kit_name_fn=sanitize_kit_name_fn,
            balance_kit=balance_kit,
            run_dir=run_dir,
            delay_ms=0,
            signal_cols=list(signal_cols),
            should_stop=progress.wasCanceled,
            on_progress=on_progress,
        )

        progress.setValue(progress.maximum())
        stopped = bool(summary.get("stopped", False))
        title = "ML Log stopped" if stopped else "ML Log complete"
        QMessageBox.information(
            parent,
            title,
            (
                f"Rows processed: {int(summary.get('processed_rows', 0))}/{int(summary.get('total_rows', 0))}\n"
                f"Rows written: {int(summary.get('written_rows', 0))}\n"
                f"Rows skipped (missing PDF): {int(summary.get('skipped_missing_pdf_rows', 0))}\n"
                f"Rows with feature warnings: {int(summary.get('feature_error_rows', 0))}\n"
                f"Workers: {int(summary.get('workers', 1))}\n"
                f"Duplicates skipped: {int(summary.get('skipped_duplicate_rows', 0))}\n"
                f"Dataset: {summary.get('dataset_path', '')}\n"
                f"Run dir: {summary.get('run_dir', '')}"
            ),
        )
        span.success(
            stopped=bool(stopped),
            processed_rows=int(summary.get("processed_rows", 0)),
            written_rows=int(summary.get("written_rows", 0)),
            skipped_duplicates=int(summary.get("skipped_duplicate_rows", 0)),
        )
    except Exception as exc:
        span.fail(exc)
        QMessageBox.critical(parent, "ML Log failed", traceback.format_exc())


def run_ml_recompute_all(
    *,
    parent: QWidget,
    dataset_path: str,
    signal_cols: Sequence[str],
    max_workers: int = 2,
) -> None:
    workers = max(1, min(8, int(max_workers)))
    span = rt.begin("ml_recompute_all", dataset_path=dataset_path, workers=workers)
    ticker: Optional[QTimer] = None
    try:
        confirm = QMessageBox.question(
            parent,
            "Recompute ML Dataset",
            "Recompute ML signals for ALL rows in the dataset using each row's saved PDF/DXF paths?\n"
            "This includes rows not in the currently opened RPD.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            span.skip(reason="user_declined")
            return

        progress = QProgressDialog("ML: recomputing dataset...", "Cancel", 0, 1, parent)
        progress.setWindowTitle("ML Recompute All")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setAutoReset(True)
        started_at = time.perf_counter()
        last_done = 0
        last_total = 1

        def _fmt_elapsed(seconds: float) -> str:
            sec = max(0, int(seconds))
            h = sec // 3600
            m = (sec % 3600) // 60
            s = sec % 60
            if h > 0:
                return f"{h:02d}:{m:02d}:{s:02d}"
            return f"{m:02d}:{s:02d}"

        def _refresh_progress_label() -> None:
            progress.setMaximum(max(1, int(last_total)))
            progress.setValue(max(0, int(last_done)))
            elapsed_txt = _fmt_elapsed(time.perf_counter() - started_at)
            progress.setLabelText(
                f"ML: recomputing dataset...\n"
                f"{int(last_done)}/{int(last_total)} | {elapsed_txt} | Threads: {workers}"
            )

        ticker = QTimer(progress)
        ticker.setInterval(250)
        ticker.timeout.connect(_refresh_progress_label)
        ticker.start()

        def on_progress(done: int, total: int) -> None:
            nonlocal last_done, last_total
            last_done = int(done)
            last_total = max(1, int(total))
            _refresh_progress_label()
            span.progress(done, total, "ml_recompute")
            QApplication.processEvents()

        _refresh_progress_label()

        summary = ml_pipeline.recompute_dataset_signals(
            dataset_path=dataset_path,
            signal_cols=list(signal_cols),
            should_stop=progress.wasCanceled,
            on_progress=on_progress,
            max_workers=workers,
        )
        ticker.stop()
        progress.setValue(progress.maximum())
        elapsed_txt = _fmt_elapsed(time.perf_counter() - started_at)
        stopped = bool(summary.get("stopped", False))
        title = "ML recompute stopped" if stopped else "ML recompute complete"
        QMessageBox.information(
            parent,
            title,
            (
                f"Rows processed: {int(summary.get('processed_rows', 0))}/{int(summary.get('total_rows', 0))}\n"
                f"Rows updated: {int(summary.get('updated_rows', 0))}\n"
                f"Rows with compute errors: {int(summary.get('error_rows', 0))}\n"
                f"Rows with DXF feature errors: {int(summary.get('dxf_feature_error_rows', 0))}\n"
                f"Rows with PDF feature errors: {int(summary.get('pdf_feature_error_rows', 0))}\n"
                f"Rows missing PDF path/file: {int(summary.get('missing_pdf_rows', 0))}\n"
                f"Rows missing DXF path/file: {int(summary.get('missing_dxf_rows', 0))}\n"
                f"Workers: {int(summary.get('workers', 1))}\n"
                f"Elapsed: {elapsed_txt}\n"
                f"Dataset: {summary.get('dataset_path', '')}"
            ),
        )
        span.success(
            stopped=bool(stopped),
            processed_rows=int(summary.get("processed_rows", 0)),
            updated_rows=int(summary.get("updated_rows", 0)),
            error_rows=int(summary.get("error_rows", 0)),
        )
    except Exception as exc:
        span.fail(exc)
        if ticker is not None:
            try:
                ticker.stop()
            except Exception:
                pass
        QMessageBox.critical(parent, "ML recompute failed", traceback.format_exc())


def run_ml_signal_plot(
    *,
    parent: QWidget,
    dataset_path: str,
    signal_cols: Sequence[str],
) -> None:
    span = rt.begin("ml_signal_plot", dataset_path=dataset_path)
    try:
        # Detached labeled dialog (button action behavior).
        old = getattr(parent, "_rk_ml_signal_plot_dialog", None)
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
            setattr(parent, "_rk_ml_signal_plot_dialog", None)

        dlg = ui_ml_signal_plot.create_polar_dialog(
            parent=parent,
            dataset_path=dataset_path,
            signal_cols=list(signal_cols or []),
        )
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)

        def _clear_ref(*_args) -> None:
            try:
                cur = getattr(parent, "_rk_ml_signal_plot_dialog", None)
                if cur is dlg:
                    setattr(parent, "_rk_ml_signal_plot_dialog", None)
            except Exception:
                pass

        dlg.destroyed.connect(_clear_ref)
        setattr(parent, "_rk_ml_signal_plot_dialog", dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        span.success(signal_count=len(signal_cols or []), embedded=False, labeled=True)
    except FileNotFoundError:
        span.skip(reason="dataset_missing")
        QMessageBox.information(
            parent,
            "ML Plot",
            f"Dataset not found:\n{dataset_path}\n\nRun ML Log first.",
        )
    except Exception as exc:
        span.fail(exc)
        QMessageBox.critical(parent, "ML Plot failed", traceback.format_exc())


def refresh_ml_plot_pane(
    *,
    parent: QWidget,
    dataset_path: str,
    signal_cols: Sequence[str],
) -> None:
    """
    Refresh embedded right-pane plot (no labels), without dialogs.
    Safe to call on startup/open-RPD.
    """
    pane_img = getattr(parent, "ml_plot_image_label", None)
    if pane_img is None:
        return
    try:
        pane_w = int(getattr(pane_img, "width", lambda: 0)())
        pane_h = int(getattr(pane_img, "height", lambda: 0)())
        # Keep render aspect aligned with pane aspect so scaled output fills better.
        target_w = max(640, int(max(1, pane_w) * 3.0))
        target_h = max(140, int(max(1, pane_h) * 3.0))
        pix, _stats = ui_ml_signal_plot.render_plot_pixmap(
            dataset_path=dataset_path,
            signal_cols=list(signal_cols or []),
            width_px=target_w,
            height_px=target_h,
            show_labels=False,
            grid_rows=2,
        )
        setattr(parent, "_rk_ml_plot_pixmap", pix)
        w = max(1, int(getattr(pane_img, "width", lambda: 0)()) - 8)
        h = max(1, int(getattr(pane_img, "height", lambda: 0)()) - 8)
        pane_img.setPixmap(pix.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        pane_img.setText("")
    except Exception:
        # Quiet fail for auto-refresh paths.
        try:
            pane_img.setText("Plot unavailable.")
        except Exception:
            pass
