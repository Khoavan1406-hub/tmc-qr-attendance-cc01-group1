"""[INTEGRATION] Submission audit on dev @ dd7b7fb: three defects reproduced through create_app() on SQLite.

1. An openGate result that arrives after an emergency release must not override EMERGENCY_HOLD
   (no OPENED scan, no attendance from a later PASSAGE, no lane release on CONTROLLER_ERROR).
2. A cycle with no PASSAGE after 15 s is moved to NO_PASSAGE / PENDING_RECONCILIATION by the app itself
   (passage-timeout worker), without sending closeGate.
3. Eligibility rights applied from an update survive a restart together with their dedup mark.
"""

import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tmc_gate.api.app import create_app
from tmc_gate.domain.enums import ClosureStatus, CommandOutcome, PassageStatus, ScanState

LANE = "LANE-BD-01"
GUARD = {"X-Guard-Session": "demo-session-guard-bd-01"}


@pytest.fixture
def make_app(tmp_path):
    apps = []

    def _make(**kwargs):
        kwargs.setdefault("passage_timeout_interval_sec", None)
        app = create_app(db_path=str(tmp_path / "factory.sqlite3"), **kwargs)
        apps.append(app)
        return app

    yield _make
    for app in apps:
        try:
            assert not app.state.uow.connection.in_transaction
            app.state.uow.connection.close()
        except Exception:
            pass


def _token(app, employee_id="EMP-001"):
    return app.state.qr_issuer.issue_qr_token(employee_id=employee_id, factory_id="BD")[0]


def _emergency_during_open(app, monkeypatch, result: CommandOutcome):
    """openGate returns `result`, but a guard's emergency release commits while the command is running."""
    processor = app.state.processor
    adapter = processor._open_gate_port  # noqa: SLF001 - test seam

    def open_then_emergency(commandId, laneId, timeout_ms):
        processor.requestEmergencyRelease("emg-during-open", laneId)
        return result

    monkeypatch.setattr(adapter, "openGate", open_then_emergency)


# --- 1. openGate result after an emergency release ---

@pytest.mark.parametrize("late_result", [CommandOutcome.OPEN_CONFIRMED, CommandOutcome.UNDETERMINED])
def test_late_open_result_after_emergency_creates_no_attendance(make_app, monkeypatch, late_result):
    app = make_app()
    _emergency_during_open(app, monkeypatch, late_result)
    processor, repo = app.state.processor, app.state.scan_repo

    result = processor.processScan("scan-emg", _token(app), LANE)
    assert result.state == ScanState.PENDING_RECONCILIATION
    assert result.reason == "EMERGENCY_RELEASE_DURING_OPEN_CYCLE"
    stored = repo.get_scan("scan-emg")
    assert stored.state == ScanState.PENDING_RECONCILIATION
    assert repo.get_active_cycle_for_lane(LANE).closureStatus == ClosureStatus.EMERGENCY_HOLD
    assert repo.get_command(result.commandId).outcome == late_result  # kept as command history

    processor.onPassage("pas-emg", result.commandId, LANE)
    assert app.state.attendance_repo.get_by_scan_id("scan-emg") is None
    cycle = repo.get_cycle_by_command(result.commandId)
    assert [p.reviewReason for p in app.state.attendance_repo.list_passages_for_cycle(cycle.cycleId)] == ["LATE_PASSAGE"]


def test_controller_error_after_emergency_keeps_the_lane_held(make_app, monkeypatch):
    app = make_app()
    _emergency_during_open(app, monkeypatch, CommandOutcome.CONTROLLER_ERROR)
    processor, repo = app.state.processor, app.state.scan_repo

    result = processor.processScan("scan-emg-error", _token(app), LANE)
    assert result.state == ScanState.PENDING_RECONCILIATION
    active = repo.get_active_cycle_for_lane(LANE)
    assert active is not None and active.closureStatus == ClosureStatus.EMERGENCY_HOLD, "hold released without reset"
    assert processor.processScan("scan-next", _token(app, "EMP-002"), LANE).state == ScanState.LANE_BUSY


def test_emergency_during_assisted_open_keeps_the_hold(make_app, monkeypatch):
    app = make_app()
    _emergency_during_open(app, monkeypatch, CommandOutcome.CONTROLLER_ERROR)
    with TestClient(app) as client:
        body = client.post("/api/interventions/assisted-open", headers=GUARD, json={
            "supportCaseId": "case-emg", "employeeId": "EMP-001", "laneId": LANE, "reason": "battery",
        }).json()
    assert body["state"] == "PENDING_RECONCILIATION"
    assert app.state.scan_repo.get_active_cycle_for_lane(LANE).closureStatus == ClosureStatus.EMERGENCY_HOLD


# --- 2. No PASSAGE within 15 s ---

def test_worker_moves_cycle_without_passage_to_reconciliation(make_app):
    app = make_app()
    processor, repo = app.state.processor, app.state.scan_repo
    opened = processor.processScan("scan-nopass", _token(app), LANE)
    assert opened.state == ScanState.OPENED
    cycle = repo.get_cycle_by_command(opened.commandId)
    worker = app.state.passage_timeout_worker

    assert worker.run_once(now=cycle.observationDeadline - timedelta(milliseconds=1)) == 0, "too early"
    assert worker.run_once(now=cycle.observationDeadline + timedelta(milliseconds=1)) == 1
    assert repo.get_scan("scan-nopass").state == ScanState.PENDING_RECONCILIATION
    assert repo.get_scan("scan-nopass").reason == "MISSING_PASSAGE_AFTER_15S"
    assert repo.get_cycle(cycle.cycleId).passageStatus == PassageStatus.NO_PASSAGE
    assert [c["action"] for c in app.state.controller_client.sent_commands()] == ["OPEN"], "timer never closes"
    assert worker.run_once(now=cycle.observationDeadline + timedelta(seconds=5)) == 0, "applied once"


def test_passage_committed_before_the_timer_wins(make_app):
    app = make_app()
    processor, repo = app.state.processor, app.state.scan_repo
    opened = processor.processScan("scan-pass", _token(app), LANE)
    cycle = repo.get_cycle_by_command(opened.commandId)
    processor.onPassage("pas-ok", opened.commandId, LANE)
    assert processor.handlePassageTimeout(cycle.cycleId, now=cycle.observationDeadline + timedelta(seconds=1)) is False
    assert repo.get_scan("scan-pass").state == ScanState.OPENED
    assert app.state.attendance_repo.get_by_scan_id("scan-pass") is not None


def test_served_app_runs_the_timeout_without_any_signal(make_app):
    app = make_app(passage_timeout_interval_sec=0.05)
    app.state.processor.PASSAGE_OBSERVATION_WINDOW_MS = 200  # short window so the test does not wait 15 s
    with TestClient(app) as client:
        opened = client.post("/api/gate/scans", json={"scanId": "scan-served", "qrToken": _token(app), "laneId": LANE}).json()
        assert opened["state"] == "OPENED"
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if client.get("/api/gate/scans/scan-served").json()["state"] == "PENDING_RECONCILIATION":
                break
            time.sleep(0.05)
        assert client.get("/api/gate/scans/scan-served").json()["state"] == "PENDING_RECONCILIATION"
    cycle = app.state.scan_repo.get_cycle_by_command(opened["commandId"])
    assert cycle.passageStatus == PassageStatus.NO_PASSAGE
    assert [c["action"] for c in app.state.controller_client.sent_commands()] == ["OPEN"]


# --- 3. Rights survive a restart together with the dedup mark ---

def test_applied_rights_survive_restart_and_replayed_update_stays_applied(make_app):
    first = make_app()
    current = first.state.snapshot_repo.get_snapshot()
    employees = dict(current.employees)
    employees["EMP-001"] = replace(employees["EMP-001"], active=False)
    update = replace(current, snapshotVersion=2, confirmedWatermark=datetime.now(UTC), employees=employees)
    assert first.state.gate_core_facade.applyEligibilityChanges(update, eventId="evt-lock-emp-001") is True
    first.state.uow.connection.close()

    second = make_app()
    restored = second.state.snapshot_repo.get_snapshot()
    assert restored.snapshotVersion == 2
    assert restored.employees["EMP-001"].active is False
    assert second.state.gate_core_facade.applyEligibilityChanges(update, eventId="evt-lock-emp-001") is True
    assert second.state.snapshot_repo.get_snapshot().employees["EMP-001"].active is False

    blocked = second.state.processor.processScan("scan-after-restart", _token(second), LANE)
    assert blocked.state == ScanState.REJECTED
    assert second.state.processor.processScan("scan-other", _token(second, "EMP-002"), LANE).state == ScanState.OPENED


def test_rolled_back_snapshot_write_is_not_visible(make_app):
    app = make_app()
    repo, uow = app.state.snapshot_repo, app.state.uow
    before = repo.get_snapshot()
    with uow:
        repo.save_snapshot(replace(before, snapshotVersion=99))
        uow.rollback()
    assert repo.get_snapshot().snapshotVersion == before.snapshotVersion


def test_status_read_after_deadline_never_reports_opened(make_app):
    app = make_app()  # worker thread disabled: the read itself must apply the timeout
    app.state.processor.PASSAGE_OBSERVATION_WINDOW_MS = 100
    with TestClient(app) as client:
        opened = client.post("/api/gate/scans", json={"scanId": "scan-read", "qrToken": _token(app), "laneId": LANE}).json()
        assert opened["state"] == "OPENED"
        time.sleep(0.2)
        status = client.get("/api/gate/scans/scan-read").json()
    assert (status["state"], status["reason"]) == ("PENDING_RECONCILIATION", "MISSING_PASSAGE_AFTER_15S")
    assert app.state.scan_repo.get_cycle_by_command(opened["commandId"]).passageStatus == PassageStatus.NO_PASSAGE
    assert [c["action"] for c in app.state.controller_client.sent_commands()] == ["OPEN"]
