from __future__ import annotations

import traceback
from typing import Callable, List, Optional

from PySide6.QtCore import QObject, QRunnable, Signal

import packet_service
from rpd_io import PartRow


class PacketWorkerSignals(QObject):
    progress = Signal(int, int, str)
    done = Signal(str, int, int)  # packet_path, pages, missing
    canceled = Signal(int, int)  # pages, missing
    empty = Signal(str, int, int)  # message, pages, missing
    error = Signal(str)


class PacketBuildWorker(QRunnable):
    def __init__(
        self,
        *,
        parts: List[PartRow],
        rpd_path: str,
        out_dirname: str,
        resolve_asset_fn: Callable[[str, str], Optional[str]],
        render_mode: str,
        max_workers: Optional[int] = None,
    ) -> None:
        super().__init__()
        self.parts = list(parts or [])
        self.rpd_path = rpd_path
        self.out_dirname = out_dirname
        self.resolve_asset_fn = resolve_asset_fn
        self.render_mode = render_mode
        self.max_workers = max_workers
        self.signals = PacketWorkerSignals()
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            packet_path, pages, missing = packet_service.build_packet(
                self.parts,
                rpd_path=self.rpd_path,
                out_dirname=self.out_dirname,
                resolve_asset_fn=self.resolve_asset_fn,
                progress_cb=lambda done, total, status: self.signals.progress.emit(
                    int(done), int(total), str(status)
                ),
                should_cancel_cb=lambda: self._stop,
                max_workers=self.max_workers,
                render_mode=self.render_mode,
            )
            self.signals.done.emit(str(packet_path), int(pages), int(missing))
        except packet_service.PacketBuildCanceled as exc:
            self.signals.canceled.emit(int(getattr(exc, "pages", 0)), int(getattr(exc, "missing", 0)))
        except packet_service.PacketBuildEmpty as exc:
            self.signals.empty.emit(
                str(exc),
                int(getattr(exc, "pages", 0)),
                int(getattr(exc, "missing", 0)),
            )
        except Exception:
            self.signals.error.emit(traceback.format_exc())
