from __future__ import annotations

import json
import os
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from config import GLOBAL_RUNTIME_LOG_PATH


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _trace_enabled() -> bool:
    flag = str(os.environ.get("RK_RUNTIME_TRACE", "1")).strip().lower()
    return flag not in ("0", "false", "off", "no")


def _stage_profile_enabled() -> bool:
    flag = str(os.environ.get("RK_STAGE_PROFILE", "1")).strip().lower()
    return _trace_enabled() and flag not in ("0", "false", "off", "no")


_LOCK = threading.Lock()


def _append_record(rec: Dict[str, Any]) -> None:
    if not _trace_enabled():
        return
    try:
        log_path = str(GLOBAL_RUNTIME_LOG_PATH or "").strip()
        if not log_path:
            return
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        with _LOCK:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=True) + "\n")
                f.flush()
    except Exception:
        return


def event(feature: str, phase: str, **fields: Any) -> None:
    payload: Dict[str, Any] = {
        "ts_utc": _utc_now_iso(),
        "feature": str(feature or "").strip(),
        "phase": str(phase or "").strip(),
    }
    for k, v in (fields or {}).items():
        payload[str(k)] = v
    _append_record(payload)


class Span:
    def __init__(self, feature: str, **fields: Any) -> None:
        self.feature = str(feature or "").strip() or "unknown"
        self._t0 = time.perf_counter()
        self._closed = False
        self._last_progress_t = 0.0
        self._last_progress_done = -1
        event(self.feature, "start", **fields)

    def progress(self, done: int, total: int, status: str = "") -> None:
        if self._closed:
            return
        now = time.perf_counter()
        done_i = int(done)
        total_i = max(1, int(total))
        should_emit = (
            done_i <= 0
            or done_i >= total_i
            or done_i != self._last_progress_done and (now - self._last_progress_t) >= 0.75
        )
        if not should_emit:
            return
        self._last_progress_done = done_i
        self._last_progress_t = now
        event(self.feature, "progress", done=done_i, total=total_i, status=str(status or ""))

    def success(self, **fields: Any) -> None:
        if self._closed:
            return
        self._closed = True
        elapsed_ms = int((time.perf_counter() - self._t0) * 1000.0)
        event(self.feature, "success", elapsed_ms=elapsed_ms, **fields)

    def skip(self, reason: str = "", **fields: Any) -> None:
        if self._closed:
            return
        self._closed = True
        elapsed_ms = int((time.perf_counter() - self._t0) * 1000.0)
        event(self.feature, "skip", reason=str(reason or ""), elapsed_ms=elapsed_ms, **fields)

    def fail(self, exc: BaseException, **fields: Any) -> None:
        if self._closed:
            return
        self._closed = True
        elapsed_ms = int((time.perf_counter() - self._t0) * 1000.0)
        event(
            self.feature,
            "error",
            elapsed_ms=elapsed_ms,
            error_type=type(exc).__name__,
            error=str(exc),
            traceback=traceback.format_exc(),
            **fields,
        )


def begin(feature: str, **fields: Any) -> Span:
    return Span(feature, **fields)


class Stage:
    def __init__(
        self,
        feature: str,
        stage_name: str,
        *,
        min_elapsed_ms: int = 0,
        emit_start: bool = False,
        **fields: Any,
    ) -> None:
        self.feature = str(feature or "").strip() or "unknown"
        self.stage_name = str(stage_name or "").strip() or "unknown"
        self.min_elapsed_ms = max(0, int(min_elapsed_ms))
        self._fields: Dict[str, Any] = {str(k): v for k, v in (fields or {}).items()}
        self._t0 = time.perf_counter()
        self._closed = False
        self._enabled = _stage_profile_enabled()
        if self._enabled and emit_start:
            event(self.feature, "stage_start", stage=self.stage_name, **self._fields)

    def _payload(self, **fields: Any) -> Dict[str, Any]:
        payload = dict(self._fields)
        for key, value in (fields or {}).items():
            payload[str(key)] = value
        payload["stage"] = self.stage_name
        return payload

    def success(self, **fields: Any) -> None:
        if self._closed:
            return
        self._closed = True
        elapsed_ms = int((time.perf_counter() - self._t0) * 1000.0)
        if not self._enabled or elapsed_ms < self.min_elapsed_ms:
            return
        event(self.feature, "stage", elapsed_ms=elapsed_ms, **self._payload(**fields))

    def fail(self, exc: BaseException, **fields: Any) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._enabled:
            return
        elapsed_ms = int((time.perf_counter() - self._t0) * 1000.0)
        event(
            self.feature,
            "stage_error",
            elapsed_ms=elapsed_ms,
            error_type=type(exc).__name__,
            error=str(exc),
            traceback=traceback.format_exc(),
            **self._payload(**fields),
        )

    def __enter__(self) -> "Stage":
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        if exc is not None:
            self.fail(exc)
            return False
        self.success()
        return False


def stage(
    feature: str,
    stage_name: str,
    *,
    min_elapsed_ms: int = 0,
    emit_start: bool = False,
    **fields: Any,
) -> Stage:
    return Stage(
        feature,
        stage_name,
        min_elapsed_ms=min_elapsed_ms,
        emit_start=emit_start,
        **fields,
    )
