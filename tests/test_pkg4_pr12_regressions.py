"""[PACKAGE 4] Integration regressions from the PR #12 review (probes by Khoa Văn, 08/11).

1. create_app() shares one SQLite connection, so every repository must share the UnitOfWork lock:
   a request on another thread must never read rows staged by a transaction that later rolls back.
2. A database held by another writer past the remaining budget yields CANNOT_PROCESS (HTTP 200), not HTTP 500,
   on every storage step of processScan (duplicate read, REJECTED / replay audit, A4 claim);
   no token is claimed, no command is sent, and no other request's transaction is rolled back.

The QR check is replaced by an allow-all AccessDecision so this file does not depend on Package 2.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from fastapi.testclient import TestClient

from tmc_gate.adapters.sqlite_repos import create_sqlite_connection
from tmc_gate.api.app import create_app
from tmc_gate.application.access_decision import AccessDecision
from tmc_gate.domain.enums import Direction, OperatingMode, ScanState
from tmc_gate.domain.models import DecisionResult, ScanAttempt, TokenClaims

LANE = "LANE-BD-01"


class _AllowAll(AccessDecision):
    def __init__(self) -> None:
        pass

    def evaluate(self, qrToken: str, laneId: str, now: datetime | None = None) -> DecisionResult:
        t = now or datetime.now(UTC)
        return DecisionResult(
            allowed=True,
            operatingMode=OperatingMode.NORMAL,
            snapshotVersion=1,
            reason="ALLOWED",
            claims=TokenClaims(f"tok-{qrToken}", "EMP-001", "BD", t, t + timedelta(seconds=60)),
            direction=Direction.ENTRY,
        )


@pytest.fixture
def real_app(tmp_path):
    path = tmp_path / "factory.sqlite3"
    app = create_app(db_path=str(path))
    app.state.processor._access_decision = _AllowAll()
    try:
        yield app, path
    finally:
        app.state.uow.rollback()
        app.state.uow.connection.close()


def test_busy_sqlite_returns_cannot_process_instead_of_http_500(real_app) -> None:
    app, path = real_app
    competing_writer = create_sqlite_connection(str(path))
    competing_writer.execute("BEGIN IMMEDIATE")
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/api/gate/scans", json={"scanId": "busy-scan", "qrToken": "QR-1", "laneId": LANE})
        assert response.status_code == 200, (response.status_code, response.text)
        assert response.json()["state"] == "CANNOT_PROCESS"
        assert not app.state.controller_client.sent_commands()
        assert not app.state.uow.connection.in_transaction
        assert competing_writer.in_transaction  # the other writer's transaction was not touched
    finally:
        competing_writer.rollback()
        competing_writer.close()
    # Nothing was claimed: the same token works once the database is free
    assert app.state.uow.connection.execute("SELECT COUNT(*) FROM token_reservations").fetchone()[0] == 0
    retry = TestClient(app).post("/api/gate/scans", json={"scanId": "busy-scan-2", "qrToken": "QR-1", "laneId": LANE})
    assert retry.json()["state"] == "OPENED"


def test_rollback_from_a_thread_that_never_began_is_a_no_op(real_app) -> None:
    app, _ = real_app
    uow = app.state.uow
    uow.begin()
    app.state.outbox_repo.append_event({"eventId": "keep-me", "eventType": "TEST"})
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(uow.rollback).result(timeout=2)  # must not discard the owner's writes
        with pytest.raises(RuntimeError):
            pool.submit(uow.commit).result(timeout=2)
    assert uow.connection.in_transaction
    uow.commit()
    assert [e["eventId"] for e in app.state.outbox_repo.list_pending_events()] == ["keep-me"]


@pytest.mark.parametrize("read_kind", ["scan-status", "committed-outbox"])
def test_app_does_not_expose_another_threads_uncommitted_writes(real_app, read_kind: str) -> None:
    app, path = real_app
    state = app.state
    started = Event()
    observer = create_sqlite_connection(str(path))
    state.uow.begin()
    state.scan_repo.save_scan(ScanAttempt(
        scanId="rolled-back-scan", factoryId="BD", laneId=LANE,
        state=ScanState.ACCEPTED, createdAt=datetime.now(UTC), reason="ACCEPTED",
    ))
    state.outbox_repo.append_event({"eventId": "rolled-back-event", "eventType": "TEST"})
    assert observer.execute("SELECT COUNT(*) FROM scan_attempts").fetchone()[0] == 0

    def read_through_app():
        started.set()
        if read_kind == "scan-status":
            return state.gate_core_facade.getScanStatus("rolled-back-scan")
        return state.gate_core_facade.readCommittedEvents()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(read_through_app)
            assert started.wait(2)
            with pytest.raises(FutureTimeout):
                future.result(timeout=0.2)  # the reader waits for the open transaction
            state.uow.rollback()
            observed = future.result(timeout=2)
        assert observed == (None if read_kind == "scan-status" else []), f"{read_kind} exposed rolled-back data"
    finally:
        observer.close()
        if state.uow.connection.in_transaction:
            state.uow.rollback()


# --- Review round 2 (08/11): every storage wait in processScan is bounded and never ends in HTTP 500 ---


class _RejectAll(_AllowAll):
    def evaluate(self, qrToken: str, laneId: str, now: datetime | None = None) -> DecisionResult:
        return DecisionResult(allowed=False, operatingMode=OperatingMode.NORMAL, snapshotVersion=1,
                              reason="INVALID_QR_SIGNATURE")


def test_rejected_scan_handles_storage_busy_without_http_500(real_app) -> None:
    app, path = real_app
    app.state.processor._access_decision = _RejectAll()
    blocker = create_sqlite_connection(str(path))
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/api/gate/scans", json={"scanId": "rej-busy", "qrToken": "BAD", "laneId": LANE})
        assert response.status_code == 200, (response.status_code, response.text)
        assert response.json()["state"] == "CANNOT_PROCESS"
        assert not app.state.controller_client.sent_commands()
        assert not app.state.uow.connection.in_transaction
        assert blocker.in_transaction
    finally:
        blocker.rollback()
        blocker.close()


def test_rejected_scan_is_stored_once_storage_is_free(real_app) -> None:
    app, _ = real_app
    app.state.processor._access_decision = _RejectAll()
    response = TestClient(app).post("/api/gate/scans", json={"scanId": "rej-ok", "qrToken": "BAD", "laneId": LANE})
    assert response.json()["state"] == "REJECTED"
    assert app.state.scan_repo.get_scan("rej-ok").state == ScanState.REJECTED


def test_replay_audit_handles_storage_busy_without_http_500(real_app, monkeypatch) -> None:
    app, path = real_app
    blocker = create_sqlite_connection(str(path))
    uow = app.state.uow
    original_rollback = uow.rollback
    competing_writer_started = Event()

    def rollback_then_other_writer():
        original_rollback()
        if not competing_writer_started.is_set():
            blocker.execute("BEGIN IMMEDIATE")
            competing_writer_started.set()

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            first = client.post("/api/gate/scans", json={"scanId": "original", "qrToken": "QR-R", "laneId": LANE})
            assert first.json()["state"] == "OPENED"
            # Another connection takes the write lock between replay detection (A4 rollback) and the replay audit
            monkeypatch.setattr(uow, "rollback", rollback_then_other_writer)
            replay = client.post("/api/gate/scans", json={"scanId": "replay", "qrToken": "QR-R", "laneId": "LANE-BD-02"})
        assert competing_writer_started.is_set()
        assert replay.status_code == 200, (replay.status_code, replay.text)
        assert replay.json()["state"] == "CANNOT_PROCESS"
        assert len(app.state.controller_client.sent_commands()) == 1
        assert not uow.connection.in_transaction
    finally:
        monkeypatch.setattr(uow, "rollback", original_rollback)
        blocker.rollback()
        blocker.close()


def test_initial_scan_read_obeys_deadline_while_another_transaction_holds_lock(real_app) -> None:
    app, _ = real_app
    uow = app.state.uow
    started = Event()
    uow.begin()  # another request's transaction on this process

    def scan_with_short_deadline():
        now = datetime.now(UTC)
        started.set()
        return app.state.processor.processScan("wait-read", "QR-D", LANE, now=now, deadline=now + timedelta(milliseconds=100))

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(scan_with_short_deadline)
            assert started.wait(2)
            result = future.result(timeout=0.5)  # five times the 100 ms budget
    finally:
        uow.rollback()
    assert result.state == ScanState.CANNOT_PROCESS
    assert not app.state.controller_client.sent_commands()
    # The other request's transaction was not rolled back by the waiting scan
    assert uow.connection.execute("SELECT COUNT(*) FROM token_reservations").fetchone()[0] == 0
