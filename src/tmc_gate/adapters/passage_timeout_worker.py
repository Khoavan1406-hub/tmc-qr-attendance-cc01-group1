"""Background worker that applies the 15-second passage timeout (§4.2.5, Table 4.23b).

It only drives time: every interval it asks the processor to sweep the configured lanes. All decisions
(deadline passed, cycle still WAITING, scan still OPENED) are taken by the processor inside a transaction,
and the timer never sends closeGate (I-05).
"""

import threading
from collections.abc import Callable, Sequence
from datetime import datetime

from tmc_gate.ports.repositories import IHealthApi


class PassageTimeoutWorker:
    def __init__(
        self,
        sweep: Callable[[list[str]], int],
        lane_ids: Sequence[str],
        interval_sec: float,
        health_api: IHealthApi,
    ) -> None:
        if interval_sec <= 0:
            raise ValueError("interval_sec must be positive")
        self._sweep = sweep
        self._lane_ids = list(lane_ids)
        self._interval_sec = interval_sec
        self._health_api = health_api
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="passage-timeout-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout_sec: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout_sec)
            self._thread = None

    def run_once(self, now: datetime | None = None) -> int:
        """One sweep; used by the thread and by tests that control time."""
        if now is None:
            return self._sweep(self._lane_ids)
        return self._sweep(self._lane_ids, now=now)  # type: ignore[call-arg]

    def _run(self) -> None:
        while not self._stop.wait(self._interval_sec):
            try:
                self.run_once()
            except Exception as exc:  # keep the worker alive; storage may be busy for a moment
                self._health_api.report_event_async(
                    {"alert": "PASSAGE_TIMEOUT_SWEEP_FAILED", "error": type(exc).__name__}
                )
