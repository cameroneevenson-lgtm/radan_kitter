from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import time
from typing import Dict, Iterable, List, Tuple


IGNORE_DIR_NAMES = {
    ".git",
    "__pycache__",
    "runs",
    "_bak",
    "_out",
}
IGNORE_DIR_PREFIXES = (
    ".venv",
    ".venv_broken_",
    ".venv_store_",
)
WATCH_EXTENSIONS = {".py"}


def _is_ignored_dir(name: str) -> bool:
    if name in IGNORE_DIR_NAMES:
        return True
    return any(name.startswith(p) for p in IGNORE_DIR_PREFIXES)


def _iter_watch_files(root: str) -> Iterable[str]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _is_ignored_dir(d)]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in WATCH_EXTENSIONS:
                continue
            yield os.path.join(dirpath, fn)


def _snapshot(root: str) -> Dict[str, Tuple[int, int]]:
    out: Dict[str, Tuple[int, int]] = {}
    for p in _iter_watch_files(root):
        try:
            st = os.stat(p)
        except OSError:
            continue
        out[p] = (int(st.st_mtime_ns), int(st.st_size))
    return out


def _diff_paths(prev: Dict[str, Tuple[int, int]], cur: Dict[str, Tuple[int, int]]) -> List[str]:
    changed: List[str] = []
    prev_keys = set(prev.keys())
    cur_keys = set(cur.keys())
    for p in sorted(prev_keys ^ cur_keys):
        changed.append(p)
    for p in sorted(prev_keys & cur_keys):
        if prev[p] != cur[p]:
            changed.append(p)
    return changed


def _spawn_app(py_exe: str, main_py: str, app_args: List[str], cwd: str) -> subprocess.Popen:
    cmd = [py_exe, main_py, *app_args]
    return subprocess.Popen(cmd, cwd=cwd)


def _terminate_process(proc: subprocess.Popen, timeout_sec: float = 6.0) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except Exception:
        return
    t0 = time.time()
    while proc.poll() is None and (time.time() - t0) < timeout_sec:
        time.sleep(0.1)
    if proc.poll() is None:
        try:
            proc.kill()
        except Exception:
            pass


def _warn_and_ask_restart(exit_code: int) -> bool:
    """
    Return True if user wants to restart, False to exit launcher.
    """
    try:
        title = "RADAN Kitter - Hot Reload"
        msg = (
            f"The app process exited with code {exit_code}.\n\n"
            "Press Retry to restart, or Cancel to exit."
        )
        MB_RETRYCANCEL = 0x00000005
        MB_ICONWARNING = 0x00000030
        IDRETRY = 4
        res = ctypes.windll.user32.MessageBoxW(0, msg, title, MB_RETRYCANCEL | MB_ICONWARNING)
        return int(res) == IDRETRY
    except Exception:
        # Fallback: no dialog support, exit safely.
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Dev hot-restart launcher for RADAN Kitter.")
    parser.add_argument("app_args", nargs=argparse.REMAINDER, help="Arguments forwarded to main.py")
    parser.add_argument("--interval", type=float, default=0.6, help="Polling interval in seconds.")
    parser.add_argument("--debounce", type=float, default=2.0, help="Quiet-window delay before restart.")
    parser.add_argument(
        "--min-uptime",
        type=float,
        default=1.2,
        help="Minimum app uptime before hot-restart can trigger.",
    )
    ns = parser.parse_args()

    root = os.path.dirname(os.path.abspath(__file__))
    main_py = os.path.join(root, "main.py")
    py_exe = sys.executable
    app_args = list(ns.app_args or [])
    if app_args and app_args[0] == "--":
        app_args = app_args[1:]

    print("Hot restart launcher running.")
    print(f"Python: {py_exe}")
    print(f"Main:   {main_py}")
    print(f"Args:   {app_args}")
    print("Watching .py files. Press Ctrl+C to stop.")

    prev = _snapshot(root)
    proc = _spawn_app(py_exe, main_py, app_args, cwd=root)
    last_spawn_at = time.time()
    pending_restart = False
    last_change_at = 0.0
    pending_changes: List[str] = []

    try:
        while True:
            if proc.poll() is not None:
                if pending_restart:
                    proc = _spawn_app(py_exe, main_py, app_args, cwd=root)
                    last_spawn_at = time.time()
                    pending_restart = False
                    last_change_at = 0.0
                    pending_changes = []
                    continue
                rc = int(proc.returncode or 0)
                if rc == 0:
                    return 0
                print(f"App exited with code {rc}.")
                if _warn_and_ask_restart(rc):
                    proc = _spawn_app(py_exe, main_py, app_args, cwd=root)
                    last_spawn_at = time.time()
                    pending_restart = False
                    last_change_at = 0.0
                    pending_changes = []
                    continue
                return rc

            time.sleep(max(0.2, float(ns.interval)))
            now = time.time()
            cur = _snapshot(root)
            changed = _diff_paths(prev, cur)
            prev = cur

            if changed:
                pending_restart = True
                last_change_at = now
                pending_changes = sorted(set(pending_changes).union(changed))
                short = [os.path.relpath(p, root) for p in changed[:4]]
                suffix = " ..." if len(changed) > 4 else ""
                print(f"Change detected ({len(changed)}): {', '.join(short)}{suffix}")

            quiet_for = (now - last_change_at) if pending_restart else 0.0
            uptime = now - last_spawn_at
            if pending_restart and quiet_for >= max(0.1, float(ns.debounce)) and uptime >= max(0.2, float(ns.min_uptime)):
                batch_count = len(pending_changes)
                print(f"Restarting app after {quiet_for:.1f}s quiet ({batch_count} file(s) batched)...")
                _terminate_process(proc)
                proc = _spawn_app(py_exe, main_py, app_args, cwd=root)
                last_spawn_at = time.time()
                pending_restart = False
                last_change_at = 0.0
                pending_changes = []
    except KeyboardInterrupt:
        print("\nStopping hot restart launcher.")
        _terminate_process(proc)
        return 0
    except Exception as e:
        print(f"Hot restart launcher error: {e}")
        _terminate_process(proc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
