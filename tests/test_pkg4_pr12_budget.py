"""[PACKAGE 4] PR #12 review round 3: one scan has one budget, shared across A2, A4 and rejection/replay audit.

Run with the PR12 or combined src on PYTHONPATH. No Package 2 dependency.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
import time

import pytest

from tmc_gate.adapters.sqlite_repos import create_sqlite_connection
from tmc_gate.api.app import create_app
from tmc_gate.domain.enums import Direction, OperatingMode, ScanState
from tmc_gate.domain.models import DecisionResult, TokenClaims


LANE = "LANE-BD-01"


class Decision:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def evaluate(self, qrToken, laneId, now=None):
        instant = now or datetime.now(UTC)
        return DecisionResult(
            allowed=self.allowed, operatingMode=OperatingMode.NORMAL, snapshotVersion=1,
            reason="ALLOWED" if self.allowed else "INVALID_QR_SIGNATURE",
            claims=TokenClaims("same-token", "EMP-001", "BD", instant, instant + timedelta(seconds=60)),
            direction=Direction.ENTRY,
        )


@pytest.fixture
def app_and_path(tmp_path):
    path = tmp_path / "factory.sqlite3"
    app = create_app(db_path=str(path))
    app.state.processor._access_decision = Decision()
    try:
        yield app, path
    finally:
        app.state.uow.rollback()
        app.state.uow.connection.close()


@pytest.mark.parametrize("path", ["accept", "reject", "replay"])
def test_later_storage_wait_only_receives_the_remaining_scan_budget(app_and_path, monkeypatch, path):
    app, _ = app_and_path
    processor, uow = app.state.processor, app.state.uow
    lane = LANE
    if path == "replay":
        assert processor.processScan("first", "QR", lane).state == ScanState.OPENED
        lane = "LANE-BD-02"
    elif path == "reject":
        processor._access_decision = Decision(allowed=False)

    # Clock values advance when storage waits finish, rather than on every clock read.
    # Implementations may read this clock as often as necessary.
    clock_ms = [0]
    monkeypatch.setattr(processor, "_monotonic_ms", lambda: clock_ms[0])
    original_begin = uow.begin
    timeouts = []
    final_wait_number = 3 if path == "replay" else 2

    def elapsed_begin(timeout_ms=None):
        timeouts.append(timeout_ms)
        if len(timeouts) == final_wait_number:
            raise TimeoutError("another writer still owns storage")
        original_begin(timeout_ms=timeout_ms)
        # Total consumed before the final wait is 80 ms in every case.
        clock_ms[0] += 40 if path == "replay" else 80

    monkeypatch.setattr(uow, "begin", elapsed_begin)
    now = datetime.now(UTC)
    result = processor.processScan(
        "budget-scan", "QR", lane, now=now, deadline=now + timedelta(milliseconds=100),
    )
    assert result.state == ScanState.CANNOT_PROCESS
    assert len(timeouts) == final_wait_number
    assert timeouts[-1] <= 20, f"{path}: 80/100 ms spent, but next wait receives {timeouts[-1]} ms; all waits={timeouts}"


def test_two_real_sqlite_waits_share_one_request_deadline(app_and_path, monkeypatch):
    app, path = app_and_path
    processor, uow = app.state.processor, app.state.uow
    blocker = create_sqlite_connection(str(path))
    original_rollback = uow.rollback
    entered_initial_wait = Event()
    reacquired = Event()
    original_begin = uow.begin
    first_call = [True]
    waits = []

    def observed_begin(timeout_ms=None):
        waits.append(timeout_ms)
        if first_call[0]:
            first_call[0] = False
            entered_initial_wait.set()
        return original_begin(timeout_ms=timeout_ms)

    def rollback_then_reacquire():
        original_rollback()
        if not reacquired.is_set():
            blocker.execute("BEGIN IMMEDIATE")
            reacquired.set()

    monkeypatch.setattr(uow, "begin", observed_begin)
    monkeypatch.setattr(uow, "rollback", rollback_then_reacquire)
    blocker.execute("BEGIN IMMEDIATE")

    def submit():
        now = datetime.now(UTC)
        started = time.monotonic()
        result = processor.processScan(
            "real-budget", "QR", LANE, now=now, deadline=now + timedelta(milliseconds=300),
        )
        return result, (time.monotonic() - started) * 1000

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(submit)
            assert entered_initial_wait.wait(2)
            time.sleep(0.18)  # Consume part of the real 300 ms deadline on the first lock.
            blocker.rollback()
            result, elapsed_ms = future.result(timeout=2)
        assert reacquired.is_set()
        assert result.state == ScanState.CANNOT_PROCESS
        assert not app.state.controller_client.sent_commands()
        assert elapsed_ms < 430, f"300 ms deadline took {elapsed_ms:.0f} ms across waits={waits}"
    finally:
        monkeypatch.setattr(uow, "rollback", original_rollback)
        blocker.rollback()
        blocker.close()


@pytest.mark.parametrize("path", ["accept", "reject"])
def test_spent_budget_starts_no_new_storage_wait(app_and_path, monkeypatch, path):
    app, _ = app_and_path
    processor, uow = app.state.processor, app.state.uow
    if path == "reject":
        processor._access_decision = Decision(allowed=False)
    clock_ms = [0]
    monkeypatch.setattr(processor, "_monotonic_ms", lambda: clock_ms[0])
    original_begin = uow.begin
    timeouts = []

    def begin_that_uses_whole_budget(timeout_ms=None):
        timeouts.append(timeout_ms)
        original_begin(timeout_ms=timeout_ms)
        clock_ms[0] += 100  # the duplicate check consumed the full 100 ms

    monkeypatch.setattr(uow, "begin", begin_that_uses_whole_budget)
    now = datetime.now(UTC)
    result = processor.processScan(
        "spent-budget", "QR", LANE, now=now, deadline=now + timedelta(milliseconds=100),
    )
    assert timeouts == [100], "no second wait may start once the budget is spent"
    assert result.state == ScanState.CANNOT_PROCESS
    assert result.reason == "REQUEST_DEADLINE_EXPIRED_BEFORE_COMMAND"
    assert not app.state.controller_client.sent_commands()
    assert not uow.connection.in_transaction
    monkeypatch.setattr(uow, "begin", original_begin)
    assert processor._scan_repo.get_scan("spent-budget") is None
