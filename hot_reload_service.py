from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from typing import Callable, Dict, Optional


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_request(request_path: str) -> Optional[dict]:
    try:
        if request_path and os.path.exists(request_path):
            with open(request_path, "r", encoding="utf-8") as f:
                request = json.load(f)
            if isinstance(request, dict):
                return request
    except Exception:
        return None
    return None


def write_response(
    response_path: str,
    request_id: str,
    action: str,
    *,
    now_utc_iso_fn: Callable[[], str] = now_utc_iso,
) -> None:
    rid = str(request_id or "").strip()
    normalized_action = str(action or "").strip().lower()
    if not rid or normalized_action not in ("accept", "reject"):
        return
    os.makedirs(os.path.dirname(response_path) or ".", exist_ok=True)
    payload = {
        "request_id": rid,
        "action": normalized_action,
        "ts_utc": now_utc_iso_fn(),
    }
    with open(response_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def remaining_seconds(request: dict, *, now_epoch: Optional[float] = None) -> int:
    timeout_total = max(1.0, float(request.get("decision_timeout_sec", 30.0) or 30.0))
    ts_epoch = float(request.get("ts_epoch", 0.0) or 0.0)
    if ts_epoch <= 0.0:
        return int(timeout_total)
    now_ts = time.time() if now_epoch is None else float(now_epoch)
    elapsed = max(0.0, now_ts - ts_epoch)
    return max(0, int(math.ceil(timeout_total - elapsed)))


def format_prompt_message(request: dict, *, now_epoch: Optional[float] = None) -> str:
    count = int(request.get("change_count", 0) or 0)
    remaining_sec = remaining_seconds(request, now_epoch=now_epoch)
    files = request.get("files", []) or []
    preview = ""
    if isinstance(files, list) and files:
        short = ", ".join(str(x) for x in files[:3])
        if len(files) > 3:
            short = f"{short}, ..."
        preview = f" [{short}]"
    return (
        f"Hot reload requested ({count} file(s) changed). "
        f"Accept or reject in {remaining_sec}s (auto-reload after timeout).{preview}"
    )


def request_id(request: Optional[dict]) -> str:
    if not isinstance(request, dict):
        return ""
    return str(request.get("request_id") or "").strip()
