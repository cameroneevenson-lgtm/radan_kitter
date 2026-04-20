from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict, Optional


def _normalize_bool_env(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "")).strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def headless_kit_refresh_enabled() -> bool:
    return _normalize_bool_env("RADAN_KITTER_HEADLESS_REFRESH_KITS", default=False)


def _automation_repo_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "radan_automation")


def _refresh_script_path() -> str:
    return os.path.normpath(os.path.join(_automation_repo_dir(), "refresh_document_headless.py"))


def is_headless_refresh_available() -> bool:
    return os.path.exists(_refresh_script_path())


def refresh_document_headless(
    document_path: str,
    *,
    thumbnail_path: Optional[str] = None,
    backend: Optional[str] = None,
    read_only: bool = False,
    skip_save: bool = False,
    timeout_sec: int = 180,
    python_exe: Optional[str] = None,
) -> Dict[str, Any]:
    script_path = _refresh_script_path()
    if not os.path.exists(script_path):
        raise RuntimeError(f"RADAN automation refresh script not found: {script_path}")

    command = [python_exe or sys.executable, script_path, str(document_path)]
    if backend:
        command.extend(["--backend", str(backend)])
    if read_only:
        command.append("--read-only")
    if skip_save:
        command.append("--skip-save")
    if thumbnail_path:
        command.extend(["--thumbnail-path", str(thumbnail_path)])

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=max(1, int(timeout_sec)),
        cwd=_automation_repo_dir(),
        check=False,
    )
    stdout = str(completed.stdout or "").strip()
    stderr = str(completed.stderr or "").strip()
    if completed.returncode != 0:
        detail = stderr or stdout or f"exit code {completed.returncode}"
        raise RuntimeError(f"Headless RADAN refresh failed for {document_path}: {detail}")

    if not stdout:
        return {}

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Headless RADAN refresh returned invalid JSON for {document_path}: {stdout!r}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Headless RADAN refresh returned a non-object payload for {document_path}.")
    return payload
