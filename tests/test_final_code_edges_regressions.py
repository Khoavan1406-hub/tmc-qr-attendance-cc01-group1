"""[INTEGRATION] Final audit of dev 52bf3aa (Khoa): stale emergency reset, concurrent assisted open, reset
persistence without resend, strict snapshot decoding. Real create_app() wiring on temporary SQLite files."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
from threading import Barrier, Event

from fastapi.testclient import TestClient
import pytest

from tmc_gate.adapters.turnstile_adapter import ACTION_OPEN, ACTION_RESET
from tmc_gate.api.app import create_app
from tmc_gate.domain.enums import ClosureStatus, CommandOutcome, CommandType, ScanState
from tmc_gate.domain.models import SupportCase


LANE = "LANE-BD-01"
GUARD = {"X-Guard-Session": "demo-session-guard-bd-01"}


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "final-edges.sqlite3")


@pytest.fixture
def gate(db):
    app = create_app(db_path=db, passage_timeout_interval_sec=None)
    with TestClient(app) as client:
        yield app, client
    assert not app.state.uow.connection.in_transaction
    app.state.uow.connection.close()


def actions(app, action):
    return [c for c in app.state.controller_client.sent_commands() if c["action"] == action]


def hold(app):
    cycle = app.state.scan_repo.get_active_cycle_for_lane(LANE)
    return None if cycle is None else cycle.closureStatus


def release(app, case_id):
    result = app.state.processor.requestEmergencyRelease(case_id, LANE)
    assert result.reason == ClosureStatus.EMERGENCY_HOLD.value
    return result


# --- [P1] a reset for an older emergency never clears a newer one ---------------------------------------

def test_late_reset_result_for_old_emergency_does_not_clear_newer_emergency(gate):
    app, _ = gate
    processor = app.state.processor
    release(app, "emg-A")

    in_flight, finish = Event(), Event()
    real_reset = processor._emergency_gate_port.resetEmergency

    def slow_reset(commandId, laneId):
        in_flight.set()
        assert finish.wait(5)
        return CommandOutcome.RESET_CONFIRMED  # the controller confirms reset A ... after B arrived

    processor._emergency_gate_port.resetEmergency = slow_reset
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(processor.requestEmergencyReset, "emg-A", LANE)
        assert in_flight.wait(5)
        release(app, "emg-B")  # a new emergency while reset A is still running
        finish.set()
        result_a = pending.result(5)
    processor._emergency_gate_port.resetEmergency = real_reset

    assert result_a.state != ScanState.OPENED
    assert result_a.reason == "EMERGENCY_SUPERSEDED"
    assert hold(app) == ClosureStatus.EMERGENCY_HOLD
    # Reset A stays in history as confirmed; it never governs the lane again
    assert app.state.scan_repo.get_command(result_a.commandId).outcome == CommandOutcome.RESET_CONFIRMED

    # Only the reset of the emergency that holds the lane ends the hold
    assert processor.requestEmergencyReset("emg-A", LANE).reason == "EMERGENCY_SUPERSEDED"
    assert hold(app) == ClosureStatus.EMERGENCY_HOLD
    assert processor.requestEmergencyReset("emg-B", LANE).reason == "EMERGENCY_RESET_CONFIRMED"
    assert hold(app) == ClosureStatus.OPENED


def test_pending_reset_confirmed_by_query_after_newer_emergency_keeps_hold(gate):
    app, _ = gate
    client = app.state.controller_client
    release(app, "emg-A")
    client.simulated_reset_mode = "TIMEOUT"
    pending = app.state.processor.requestEmergencyReset("emg-A", LANE)
    assert pending.reason == ClosureStatus.EMERGENCY_HOLD.value
    release(app, "emg-B")

    client.simulated_query_status = "CONFIRMED"  # the controller now says reset A was executed
    late = app.state.processor.requestEmergencyReset("emg-A", LANE)
    assert late.commandId == pending.commandId
    assert late.reason == "EMERGENCY_SUPERSEDED"
    assert hold(app) == ClosureStatus.EMERGENCY_HOLD


def test_reset_of_unknown_or_older_emergency_is_refused_before_sending(gate):
    app, _ = gate
    release(app, "emg-A")
    release(app, "emg-B")
    result = app.state.processor.requestEmergencyReset("emg-A", LANE)
    assert (result.state, result.reason) == (ScanState.REJECTED, "EMERGENCY_SUPERSEDED")
    assert actions(app, ACTION_RESET) == []
    assert hold(app) == ClosureStatus.EMERGENCY_HOLD


# --- [P2] the RESET command is stored before sending and is never resent ----------------------------------

def test_reset_command_is_persisted_before_send_and_not_resent_when_undetermined(gate, db):
    app, _ = gate
    processor = app.state.processor
    client = app.state.controller_client
    release(app, "emg-A")

    seen_in_db = []
    real_reset = processor._emergency_gate_port.resetEmergency

    def observing_reset(commandId, laneId):
        seen_in_db.append(app.state.scan_repo.get_command(commandId))
        return real_reset(commandId=commandId, laneId=laneId)

    processor._emergency_gate_port.resetEmergency = observing_reset
    client.simulated_reset_mode = "TIMEOUT"
    first = processor.requestEmergencyReset("emg-A", LANE)
    assert first.reason == ClosureStatus.EMERGENCY_HOLD.value
    stored = seen_in_db[0]
    assert stored is not None and stored.commandType == CommandType.RESET
    assert stored.outcome == CommandOutcome.SENT and stored.supportCaseId == "emg-A"
    assert app.state.scan_repo.get_command(first.commandId).outcome == CommandOutcome.UNDETERMINED

    # Repeats query the stored command; still unknown -> still EMERGENCY_HOLD, nothing new sent
    client.simulated_reset_mode = "RESET_CONFIRMED"
    for _ in range(2):
        again = processor.requestEmergencyReset("emg-A", LANE)
        assert again.commandId == first.commandId
        assert again.reason == ClosureStatus.EMERGENCY_HOLD.value
    assert len(actions(app, ACTION_RESET)) == 1
    assert hold(app) == ClosureStatus.EMERGENCY_HOLD

    # The query finally confirms the original command
    client.simulated_query_status = "CONFIRMED"
    done = processor.requestEmergencyReset("emg-A", LANE)
    assert (done.reason, done.commandId) == ("EMERGENCY_RESET_CONFIRMED", first.commandId)
    assert len(actions(app, ACTION_RESET)) == 1
    assert hold(app) == ClosureStatus.OPENED


def test_concurrent_resets_send_one_command(gate):
    app, _ = gate
    processor = app.state.processor
    release(app, "emg-A")
    start = Barrier(2)

    def reset():
        start.wait(5)
        return processor.requestEmergencyReset("emg-A", LANE)

    with ThreadPoolExecutor(2) as pool:
        results = [f.result(5) for f in [pool.submit(reset), pool.submit(reset)]]
    assert len(actions(app, ACTION_RESET)) == 1
    assert len({r.commandId for r in results}) == 1


def test_controller_error_reset_allows_one_new_reset(gate):
    app, _ = gate
    client = app.state.controller_client
    release(app, "emg-A")
    client.simulated_reset_mode = "ERROR"
    failed = app.state.processor.requestEmergencyReset("emg-A", LANE)
    assert hold(app) == ClosureStatus.EMERGENCY_HOLD
    client.simulated_reset_mode = "RESET_CONFIRMED"
    ok = app.state.processor.requestEmergencyReset("emg-A", LANE)
    assert ok.reason == "EMERGENCY_RESET_CONFIRMED" and ok.commandId != failed.commandId
    assert len(actions(app, ACTION_RESET)) == 2


def test_stored_reset_survives_restart_and_is_not_resent(db):
    app = create_app(db_path=db, passage_timeout_interval_sec=None)
    app.state.processor.requestEmergencyRelease("emg-A", LANE)
    app.state.controller_client.simulated_reset_mode = "TIMEOUT"
    first = app.state.processor.requestEmergencyReset("emg-A", LANE)
    app.state.uow.connection.close()

    restarted = create_app(db_path=db, passage_timeout_interval_sec=None)
    stored = restarted.state.scan_repo.get_command_by_support_case("emg-A", CommandType.RESET.value)
    assert stored is not None and stored.commandId == first.commandId
    again = restarted.state.processor.requestEmergencyReset("emg-A", LANE)
    assert again.commandId == first.commandId
    assert actions(restarted, ACTION_RESET) == []  # the new controller session never receives a second RESET
    restarted.state.uow.connection.close()


# --- [P1] one openGate per assisted support case, even under concurrency ----------------------------------

def test_concurrent_assisted_open_same_support_case_sends_one_open(gate):
    app, client = gate
    with app.state.uow:
        app.state.scan_repo.save_support_case(SupportCase(
            supportCaseId="case-dup", factoryId="BD", laneId=LANE, guardId="GUARD-BD-01",
            reason="battery", occurredAt=datetime.now(UTC), employeeId="EMP-001",
        ))
        app.state.uow.commit()
    processor = app.state.processor
    start = Barrier(2)

    def assisted():
        start.wait(5)
        return processor.requestAssistedOpen("case-dup", LANE)

    with ThreadPoolExecutor(2) as pool:
        results = [f.result(5) for f in [pool.submit(assisted), pool.submit(assisted)]]

    assert len(actions(app, ACTION_OPEN)) == 1
    opened = [r for r in results if r.state == ScanState.OPENED]
    assert len(opened) >= 1 and len({r.commandId for r in opened}) == 1
    assert all(r.state in (ScanState.OPENED, ScanState.PENDING_RECONCILIATION) for r in results), results


# --- [P2] snapshot JSON is decoded without coercion ---------------------------------------------------------

def _tamper_snapshot(db, mutate):
    app = create_app(db_path=db, passage_timeout_interval_sec=None)
    conn = app.state.uow.connection
    row = conn.execute("SELECT snapshot_json FROM eligibility_snapshots").fetchone()
    data = json.loads(row["snapshot_json"])
    mutate(data)
    conn.execute("UPDATE eligibility_snapshots SET snapshot_json = ?", (json.dumps(data),))
    conn.commit()
    conn.close()


def _scan_after_restart(db):
    app = create_app(db_path=db, passage_timeout_interval_sec=None)
    try:
        qr = app.state.qr_issuer.issue_qr_token(employee_id="EMP-001", factory_id="BD")[0]
        return app.state.processor.processScan("scan-tampered", qr, LANE)
    finally:
        app.state.uow.connection.close()


@pytest.mark.parametrize(
    "field, value",
    [
        ("isCompleteAndContinuous", "false"),  # bool("false") would be True
        ("snapshotVersion", 2.5),  # int(2.5) would be 2
        ("snapshotVersion", "3"),
        ("clockErrorBoundSec", "1"),
    ],
)
def test_bad_snapshot_header_values_are_not_coerced(db, field, value):
    def mutate(data):
        data[field] = value

    _tamper_snapshot(db, mutate)
    result = _scan_after_restart(db)
    assert result.state != ScanState.OPENED
    assert result.state == ScanState.CANNOT_PROCESS


def test_bad_employee_active_value_is_not_coerced(db):
    def mutate(data):
        data["employees"]["EMP-001"]["active"] = "false"

    _tamper_snapshot(db, mutate)
    result = _scan_after_restart(db)
    assert result.state != ScanState.OPENED


def test_id_list_stored_as_string_is_refused(db):
    def mutate(data):
        data["employees"]["EMP-001"]["allowedFactories"] = "BD"  # set("BD") would be {"B", "D"}

    _tamper_snapshot(db, mutate)
    with pytest.raises(ValueError):
        create_app(db_path=db, passage_timeout_interval_sec=None)
