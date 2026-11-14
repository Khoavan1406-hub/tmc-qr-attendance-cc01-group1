"""[INTEGRATION] PR #15 review round 2 (Khoa): assisted open after emergency+reset, passage timeout after CLOSED,
assisted open without passage, snapshot read bounded by the scan deadline. Temporary SQLite files only."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import UTC, datetime, timedelta
import inspect
from threading import Event

from fastapi.testclient import TestClient
import pytest

from tmc_gate.api.app import create_app
from tmc_gate.domain.enums import CommandOutcome


LANE = "LANE-BD-01"
GUARD = {"X-Guard-Session": "demo-session-guard-bd-01"}


@pytest.fixture
def gate(tmp_path):
    kwargs = {"passage_timeout_interval_sec": None} if "passage_timeout_interval_sec" in inspect.signature(create_app).parameters else {}
    app = create_app(db_path=str(tmp_path / "review.sqlite3"), **kwargs)
    with TestClient(app) as client:
        yield app, client
    assert not app.state.uow.connection.in_transaction
    app.state.uow.connection.close()


def token(app):
    return app.state.qr_issuer.issue_qr_token(employee_id="EMP-001", factory_id="BD")[0]


def open_scan(app, scan_id):
    opened = app.state.processor.processScan(scan_id, token(app), LANE)
    assert opened.state.value == "OPENED"
    return opened


def assisted(client):
    response = client.post("/api/interventions/assisted-open", headers=GUARD, json={
        "supportCaseId": "case-assist", "employeeId": "EMP-001", "laneId": LANE, "reason": "battery"
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_sweep_keeps_tracking_missing_passage_after_lane_is_closed(gate):
    app, client = gate
    opened = open_scan(app, "closed-before-passage")
    repo = app.state.scan_repo
    cycle = repo.get_cycle_by_command(opened.commandId)
    app.state.processor.onLaneClear(opened.commandId, LANE)
    assert repo.get_cycle(cycle.cycleId).closureStatus.value == "CLOSED"
    assert repo.get_active_cycle_for_lane(LANE) is None

    # The passage timer remains independent of closure; advance only this persisted deadline.
    with app.state.uow:
        closed = repo.get_cycle(cycle.cycleId)
        closed.observationDeadline = datetime.now(UTC) - timedelta(seconds=1)
        repo.update_cycle(closed, releaseLane=True)
        app.state.uow.commit()
    swept = app.state.passage_timeout_worker.run_once()
    status = client.get("/api/gate/scans/closed-before-passage").json()["state"]
    after = repo.get_cycle(cycle.cycleId)
    assert (status, after.passageStatus.value) == ("PENDING_RECONCILIATION", "NO_PASSAGE"), (
        f"sweep={swept}; scan={status}; passage={after.passageStatus.value}; closure={after.closureStatus.value}"
    )
    assert repo.get_active_cycle_for_lane(LANE) is None


def test_assisted_open_without_passage_is_also_timed_out(gate):
    app, client = gate
    opened = assisted(client)
    assert opened["state"] == "OPENED"
    cycle = app.state.scan_repo.get_cycle_by_command(opened["commandId"])
    assert cycle.scanId is None
    swept = app.state.passage_timeout_worker.run_once(now=cycle.observationDeadline + timedelta(seconds=1))
    after = app.state.scan_repo.get_cycle(cycle.cycleId)
    assert after.passageStatus.value == "NO_PASSAGE", f"sweep={swept}; assisted passage={after.passageStatus.value}"
    assert app.state.attendance_repo.list_transactions() == []
    assert [c["action"] for c in app.state.controller_client.sent_commands()] == ["OPEN"]


def test_assisted_open_error_after_emergency_reset_does_not_release_lane(gate, monkeypatch):
    app, client = gate
    processor = app.state.processor

    def release_and_reset_before_open_reply(commandId, laneId, timeout_ms):
        emergency = processor.requestEmergencyRelease("emergency-during-assisted", laneId)
        assert emergency.state.value == "OPENED"
        reset = processor.requestEmergencyReset("emergency-during-assisted", laneId)
        assert reset.reason == "EMERGENCY_RESET_CONFIRMED"
        return CommandOutcome.CONTROLLER_ERROR

    monkeypatch.setattr(processor._open_gate_port, "openGate", release_and_reset_before_open_reply)
    reply = assisted(client)
    cycle = app.state.scan_repo.get_cycle_by_command(reply["commandId"])
    active = app.state.scan_repo.get_active_cycle_for_lane(LANE)
    assert active is not None and active.cycleId == cycle.cycleId, (
        f"late assisted result={reply['state']}; active cycle={active}; "
        f"history closure={cycle.closureStatus.value}; reset must still wait for LANE_CLEAR and CLOSE"
    )


def test_snapshot_read_does_not_wait_past_scan_deadline(gate, monkeypatch):
    app, _ = gate
    about_to_read, holding, release = Event(), Event(), Event()
    original = app.state.snapshot_repo.get_snapshot

    def read_after_other_request_started():
        about_to_read.set()
        assert holding.wait(2), "holder did not start"
        return original()

    def hold_transaction():
        assert about_to_read.wait(2), "snapshot read did not start"
        app.state.uow.begin()
        holding.set()
        try:
            assert release.wait(5), "probe cleanup did not run"
        finally:
            app.state.uow.rollback()

    monkeypatch.setattr(app.state.snapshot_repo, "get_snapshot", read_after_other_request_started)
    qr = token(app)
    blocked = False
    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(hold_transaction)
        started = datetime.now(UTC)
        request = pool.submit(app.state.processor.processScan, "snapshot-lock", qr, LANE,
                              started, started + timedelta(milliseconds=100))
        try:
            assert holding.wait(2), "scan never reached snapshot read"
            try:
                request.result(timeout=0.4)
            except FutureTimeout:
                blocked = True
        finally:
            release.set()
        result = request.result(timeout=3)
        holder.result(timeout=3)
    assert not blocked, f"100 ms scan remained blocked for >400 ms at get_snapshot; returned {result.state.value} only after release"
    assert result.state.value == "CANNOT_PROCESS"
    assert app.state.controller_client.sent_commands() == []
