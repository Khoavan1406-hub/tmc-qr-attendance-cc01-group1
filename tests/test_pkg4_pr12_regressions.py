"""[PACKAGE 4] Integration regressions from the PR #12 review (probes by Khoa Văn, 08/11).

1. create_app() shares one SQLite connection, so every repository must share the UnitOfWork lock:
   a request on another thread must never read rows staged by a transaction that later rolls back.
2. A database held by another writer past the remaining budget yields CANNOT_PROCESS (HTTP 200), not HTTP 500;
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
