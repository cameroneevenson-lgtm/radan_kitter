from __future__ import annotations

import os
import subprocess
import time
import traceback
from typing import Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import Qt, QTimer, QThreadPool
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QWidget

import kit_service
import ml_runtime
import packet_service
import packet_runtime
import rf_service
import runtime_trace as rt
import ui_ml_signal_plot
from app_utils import ensure_dir, now_stamp
from config import (
    CANON_KITS,
    GLOBAL_RUNTIME_DIR,
    PACKET_TEMP_MAX_PAGES,
    PACKET_TEMP_FIRST_PAGE_ONLY,
    PACKET_TEMP_LOCAL_OUTPUT_DIR,
    PACKET_TEMP_LOCAL_OUTPUT_ENABLED,
    W_RELEASE_ROOT,
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


def _format_example_lines(title: str, values: Sequence[str], limit: int = 3) -> str:
    items = [str(v or "").strip() for v in list(values or []) if str(v or "").strip()]
    if not items:
        return ""
    shown = items[: max(1, int(limit))]
    out = [title]
    out.extend(f"- {item}" for item in shown)
    remaining = len(items) - len(shown)
    if remaining > 0:
        out.append(f"- ... and {remaining} more")
    return "\n".join(out)


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

    progress = QProgressDialog("Building print packet...", "Cancel", 0, max(1, len(build_parts)), parent)
    progress.setWindowTitle("Print Packet")
    progress.setWindowModality(Qt.WindowModal)
    progress.setMinimumDuration(0)
    progress.setAutoClose(True)
    progress.setAutoReset(True)
    started_at = time.perf_counter()
    last_done = 0
    last_total = max(1, len(build_parts))
    last_status = "Starting print packet build"

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
            f"Building print packet... {int(last_done)}/{int(last_total)} | {elapsed_txt} | Mode: {mode}\n{last_status}"
        )

    ticker = QTimer(progress)
    ticker.setInterval(250)
    ticker.timeout.connect(_refresh_progress_label)
    ticker.start()

    worker = packet_runtime.PacketBuildWorker(
        parts=build_parts,
        rpd_path=rpd_path,
        out_dirname=effective_out_dir,
        resolve_asset_fn=resolve_asset_fn,
        render_mode=mode,
    )
    setattr(parent, "_rk_build_packet_worker", worker)

    def _cleanup() -> None:
        try:
            ticker.stop()
        except Exception:
            pass
        try:
            progress.close()
        except Exception:
            pass
        setattr(parent, "_rk_build_packet_worker", None)
        setattr(parent, "_rk_build_packet_running", False)

    def _on_progress(done: int, total: int, status: str) -> None:
        nonlocal last_done, last_total, last_status
        last_done = int(done)
        last_total = max(1, int(total))
        last_status = str(status)
        _refresh_progress_label()
        span.progress(done, total, status)

    def _on_done(packet_path: str, pages: int, missing: int) -> None:
        progress.setValue(progress.maximum())
        _cleanup()
        _open_output_file_when_ready(packet_path)
        span.success(packet_path=packet_path, pages=int(pages), missing=int(missing))

    def _on_canceled(pages: int, missing: int) -> None:
        _cleanup()
        span.skip(reason="user_canceled", pages=int(pages), missing=int(missing))

    def _on_empty(message: str, pages: int, missing: int) -> None:
        _cleanup()
        span.skip(reason="no_packet_pages", pages=int(pages), missing=int(missing))
        QMessageBox.information(
            parent,
            "Print Packet",
            (
                f"{message}\n"
                f"Missing PDFs: {int(missing)}"
            ),
        )

    def _on_error(tb: str) -> None:
        _cleanup()
        span.fail(RuntimeError("Packet worker failed"))
        QMessageBox.critical(parent, "Print Packet failed", str(tb or "").strip() or "Packet worker failed.")

    worker.signals.progress.connect(_on_progress)
    worker.signals.done.connect(_on_done)
    worker.signals.canceled.connect(_on_canceled)
    worker.signals.empty.connect(_on_empty)
    worker.signals.error.connect(_on_error)
    progress.canceled.connect(worker.request_stop)
    _refresh_progress_label()
    QThreadPool.globalInstance().start(worker)
    return True


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
    on_complete: Optional[Callable[[], None]] = None,
) -> None:
    span = rt.begin("ml_log", rpd_path=rpd_path, part_count=len(parts))
    if not _require_rpd_loaded(parent, tree, rpd_path):
        span.skip(reason="no_rpd")
        return
    total = len(parts)
    progress = QProgressDialog("ML: scanning and logging...", "Cancel", 0, max(1, total), parent)
    progress.setWindowTitle("ML Log")
    progress.setWindowModality(Qt.WindowModal)
    progress.setMinimumDuration(0)
    progress.setAutoClose(True)
    progress.setAutoReset(True)

    worker = ml_runtime.MlScanWorker(
        parts=parts,
        rpd_path=rpd_path,
        delay_ms=0,
        tools_dir=GLOBAL_RUNTIME_DIR,
        global_runs_dir=run_dir,
        canon_kits=CANON_KITS,
        balance_kit=balance_kit,
        signal_cols=list(signal_cols),
        w_release_root=W_RELEASE_ROOT,
        resolve_asset_fn=resolve_asset_fn,
        sanitize_kit_name_fn=sanitize_kit_name_fn,
        now_stamp_fn=now_stamp,
        ensure_dir_fn=ensure_dir,
    )
    setattr(parent, "_rk_ml_log_worker", worker)

    def _cleanup() -> None:
        try:
            progress.close()
        except Exception:
            pass
        setattr(parent, "_rk_ml_log_worker", None)

    def _on_progress(done: int, total_count: int) -> None:
        progress.setMaximum(max(1, int(total_count)))
        progress.setValue(max(0, int(done)))
        progress.setLabelText(f"ML: scanning and logging...\n{int(done)}/{int(total_count)}")
        span.progress(done, total_count, "ml_log")

    def _on_completed(summary: Dict[str, object]) -> None:
        progress.setValue(progress.maximum())
        _cleanup()
        stopped = bool(summary.get("stopped", False))
        title = "ML Log stopped" if stopped else "ML Log complete"
        details = [
            _format_example_lines("Feature warnings:", summary.get("warning_examples", [])),
            _format_example_lines("Missing PDF rows:", summary.get("missing_pdf_examples", [])),
            _format_example_lines("Write errors:", summary.get("error_examples", [])),
        ]
        detail_block = "\n\n".join(line for line in details if line)
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
                f"{'' if not detail_block else '\n\n' + detail_block}"
            ),
        )
        if stopped:
            span.skip(reason="user_canceled", processed_rows=int(summary.get("processed_rows", 0)))
        else:
            span.success(
                stopped=False,
                processed_rows=int(summary.get("processed_rows", 0)),
                written_rows=int(summary.get("written_rows", 0)),
                skipped_duplicates=int(summary.get("skipped_duplicate_rows", 0)),
            )
        if on_complete is not None:
            on_complete()

    def _on_error(tb: str) -> None:
        _cleanup()
        span.fail(RuntimeError("ML log worker failed"))
        QMessageBox.critical(parent, "ML Log failed", str(tb or "").strip() or "ML log worker failed.")

    worker.signals.progress.connect(_on_progress)
    worker.signals.completed.connect(_on_completed)
    worker.signals.error.connect(_on_error)
    progress.canceled.connect(worker.request_stop)
    QThreadPool.globalInstance().start(worker)


def run_ml_recompute_all(
    *,
    parent: QWidget,
    dataset_path: str,
    signal_cols: Sequence[str],
    max_workers: int = 2,
    on_complete: Optional[Callable[[], None]] = None,
) -> None:
    workers = max(1, min(8, int(max_workers)))
    span = rt.begin("ml_recompute_all", dataset_path=dataset_path, workers=workers)
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

        worker = ml_runtime.MlRecomputeWorker(
            dataset_path=dataset_path,
            signal_cols=list(signal_cols),
            max_workers=workers,
        )
        setattr(parent, "_rk_ml_recompute_worker", worker)

        def _cleanup() -> None:
            try:
                ticker.stop()
            except Exception:
                pass
            try:
                progress.close()
            except Exception:
                pass
            setattr(parent, "_rk_ml_recompute_worker", None)

        def _on_progress(done: int, total: int) -> None:
            nonlocal last_done, last_total
            last_done = int(done)
            last_total = max(1, int(total))
            _refresh_progress_label()
            span.progress(done, total, "ml_recompute")

        _refresh_progress_label()

        def _on_done(summary: Dict[str, object]) -> None:
            progress.setValue(progress.maximum())
            elapsed_txt = _fmt_elapsed(time.perf_counter() - started_at)
            _cleanup()
            stopped = bool(summary.get("stopped", False))
            title = "ML recompute stopped" if stopped else "ML recompute complete"
            details = [
                _format_example_lines("Feature error rows:", summary.get("error_examples", [])),
                _format_example_lines("Missing path rows:", summary.get("missing_path_examples", [])),
            ]
            detail_block = "\n\n".join(line for line in details if line)
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
                    f"{'' if not detail_block else '\n\n' + detail_block}"
                ),
            )
            if stopped:
                span.skip(reason="user_canceled", processed_rows=int(summary.get("processed_rows", 0)))
            else:
                span.success(
                    stopped=False,
                    processed_rows=int(summary.get("processed_rows", 0)),
                    updated_rows=int(summary.get("updated_rows", 0)),
                    error_rows=int(summary.get("error_rows", 0)),
                )
            if on_complete is not None:
                on_complete()

        def _on_error(tb: str) -> None:
            _cleanup()
            span.fail(RuntimeError("ML recompute worker failed"))
            QMessageBox.critical(parent, "ML recompute failed", str(tb or "").strip() or "ML recompute worker failed.")

        worker.signals.progress.connect(_on_progress)
        worker.signals.done.connect(_on_done)
        worker.signals.error.connect(_on_error)
        progress.canceled.connect(worker.request_stop)
        QThreadPool.globalInstance().start(worker)
    except Exception as exc:
        span.fail(exc)
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
