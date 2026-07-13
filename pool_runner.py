# pool_runner.py
# Shared ThreadPoolExecutor driver used by every "threaded vs. single-threaded
# work queue with cooperative cancellation" spot in the codebase
# (pdf_packet.build_watermarked_packet, ml_pipeline.run_scan_and_log,
# ml_pipeline.recompute_dataset_signals). Extracted because the same
# submit/wait/resubmit loop and cancel-aware pool teardown were copy-pasted
# near-verbatim in all three places.
#
# This module intentionally does NOT own task construction or result
# handling - callers keep their own `_submit_next()` closures (task shapes
# differ per caller) and their own completion callbacks. It only centralizes
# the boilerplate that was identical everywhere:
#   - submit an initial batch, then keep the pool full as futures complete
#   - poll for completions so a should_cancel callback can be checked
#     periodically instead of blocking indefinitely
#   - tear the pool down correctly depending on whether the run was
#     canceled (cancel pending futures + non-blocking shutdown) or finished
#     normally (blocking shutdown)

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, Callable, Dict, Optional

DEFAULT_POLL_INTERVAL_S = 0.2


def run_pooled(
    pool: ThreadPoolExecutor,
    in_flight: Dict[Future, Any],
    submit_next: Callable[[], bool],
    handle_completed: Callable[[Any, Future], None],
    *,
    max_workers: int,
    total_items: int,
    should_cancel: Optional[Callable[[], bool]] = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL_S,
) -> bool:
    """
    Drive a ThreadPoolExecutor-based pipeline with completion-driven
    resubmission and cooperative cancellation.

    The caller owns ``pool`` and ``in_flight`` (future -> per-task context)
    and supplies:
      - submit_next(): submits the next queued item into ``pool``, records
        it in ``in_flight`` (future -> context), and returns True if
        something was submitted, False once the queue is exhausted.
      - handle_completed(context, future): called once per completed
        future, after it has already been popped from ``in_flight``.
        Responsible for pulling future.result() (and handling any exception
        it raises) plus whatever progress/logging/bookkeeping the caller
        needs. May be called in completion order, not submission order -
        callers that need ordered output (see pdf_packet.py) must buffer
        internally, same as before this was extracted.

    Returns True if ``should_cancel`` reported True before every item
    finished running, False if every item ran to completion.

    Does NOT shut down ``pool`` - callers remain responsible for teardown
    (see ``shutdown_pool``), since the shutdown call has to happen in the
    caller's own try/finally around whatever else it does with ``pool``.
    """

    def _cancel_requested() -> bool:
        if should_cancel is None:
            return False
        try:
            return bool(should_cancel())
        except Exception:
            return False

    for _ in range(min(max_workers, total_items)):
        submit_next()

    canceled = False
    while in_flight:
        if _cancel_requested():
            canceled = True
            break
        done_set, _ = wait(set(in_flight.keys()), timeout=poll_interval, return_when=FIRST_COMPLETED)
        if not done_set:
            continue
        for fut in done_set:
            context = in_flight.pop(fut)
            handle_completed(context, fut)
            if not canceled:
                submit_next()

    return canceled


def shutdown_pool(pool: ThreadPoolExecutor, in_flight: Dict[Future, Any], canceled: bool) -> None:
    """
    Tear down a pool after run_pooled() returns (or after any other bail-out
    path that leaves ``pool`` needing cleanup).

    On cancellation: cancel any still-pending futures and shut the pool down
    without waiting for running tasks to drain (falls back to a plain
    shutdown(wait=False) on Python versions without cancel_futures).

    Otherwise: shut down and wait for any in-flight work to finish normally.
    """
    if canceled:
        for fut in list(in_flight.keys()):
            try:
                fut.cancel()
            except Exception:
                pass
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            pool.shutdown(wait=False)
    else:
        pool.shutdown(wait=True)
