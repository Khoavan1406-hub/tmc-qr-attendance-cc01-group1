"""[INTEGRATION] Flow A and Flow B end to end through create_app(): FastAPI routes, real SQLite file
(SqliteUnitOfWork + Sqlite repositories), Ed25519 QR issuer/verifier and the simulated turnstile controller.

Nothing is faked inside the gate: each test talks HTTP to the composed app and then reads the SQLite file
with a separate connection to check what was really committed.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from tmc_gate.api.app import create_app

GUARD = {"X-Guard-Session": "demo-session-guard-bd-01"}


@pytest.fixture
def gate(tmp_path):
    path = tmp_path / "factory.sqlite3"
    app = create_app(db_path=str(path))
    with TestClient(app) as client:
        yield app, client, path
    assert not app.state.uow.connection.in_transaction, "a request left a transaction open"
    app.state.uow.connection.close()


def _qr(client, employee_id="EMP-001"):
    response = client.post("/api/demo/qr-tokens", json={"employeeId": employee_id, "factoryId": "BD"})
    assert response.status_code == 200
    return response.json()["qrToken"]


def _scan(client, scan_id, token, lane="LANE-BD-01"):
    response = client.post("/api/gate/scans", json={"scanId": scan_id, "qrToken": token, "laneId": lane})
    assert response.status_code == 200, response.text
    return response.json()


def _count(path, table, where="1=1", args=()):
    with sqlite3.connect(path) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()[0]


def _actions(app):
    return [(c["action"], c["laneId"]) for c in app.state.controller_client.sent_commands()]


# --- Flow A: QR scan -> OPENED -> PASSAGE -> attendance -> LANE_CLEAR -> close ---

def test_flow_a_full_cycle_is_committed_to_sqlite(gate):
    app, client, path = gate
    token = _qr(client)

    opened = _scan(client, "scan-001", token)
    assert (opened["state"], opened["reason"], opened["degradedFlag"]) == ("OPENED", "OPEN_CONFIRMED", False)
    command_id = opened["commandId"]
    assert _count(path, "attendance_transactions") == 0, "no attendance before PASSAGE"

    # Lost kiosk response: same scanId returns the stored result, no second openGate
    assert _scan(client, "scan-001", token)["state"] == "OPENED"
    assert client.get("/api/gate/scans/scan-001").json()["state"] == "OPENED"

    signal = {"commandId": command_id, "laneId": "LANE-BD-01"}
    assert client.post("/api/controller/signals/passage", json={**signal, "passageEventId": "pas-001"}).status_code == 200
    # The controller repeats the same PASSAGE: still exactly one attendance record
    assert client.post("/api/controller/signals/passage", json={**signal, "passageEventId": "pas-001"}).status_code == 200
    assert _count(path, "attendance_transactions", "scan_id = ?", ("scan-001",)) == 1

    assert client.post("/api/controller/signals/lane-clear", json=signal).status_code == 200
    assert client.post("/api/controller/signals/lane-clear", json=signal).status_code == 200
    assert _actions(app) == [("OPEN", "LANE-BD-01"), ("CLOSE", "LANE-BD-01")], "openGate once, closeGate once"

    # The lane is free again for the next employee
    assert _scan(client, "scan-002", _qr(client, "EMP-002"))["state"] == "OPENED"


def test_flow_a_copied_qr_is_rejected_as_replay_on_another_lane(gate):
    app, client, path = gate
    token = _qr(client)
    assert _scan(client, "scan-first", token)["state"] == "OPENED"

    replay = _scan(client, "scan-copy", token, lane="LANE-BD-02")
    assert (replay["state"], replay["reason"]) == ("REJECTED", "QR_TOKEN_REPLAY_DETECTED")
    assert _actions(app) == [("OPEN", "LANE-BD-01")], "no command for the copied QR"
    assert _count(path, "token_reservations") == 1
    assert _count(path, "scan_attempts", "scan_id = ? AND state = 'REJECTED'", ("scan-copy",)) == 1


def test_flow_a_rejects_bad_tokens_without_any_command(gate):
    app, client, path = gate
    malformed = _scan(client, "scan-bad", "NOT-A-TOKEN")
    assert malformed["state"] == "REJECTED"
    suspended = _scan(client, "scan-suspended", _qr(client, "EMP-SUSPENDED"))
    assert suspended["state"] == "REJECTED"
    assert _actions(app) == []
    assert _count(path, "token_reservations") == 0


def test_flow_a_degraded_and_cannot_process_follow_the_watermark(gate):
    app, client, path = gate
    assert client.post("/api/demo/watermark-age", json={"watermarkAgeSeconds": 300}).json()["operatingMode"] == "DEGRADED"
    degraded = _scan(client, "scan-degraded", _qr(client))
    assert (degraded["state"], degraded["degradedFlag"]) == ("OPENED", True)
    signal = {"commandId": degraded["commandId"], "laneId": "LANE-BD-01"}
    client.post("/api/controller/signals/passage", json={**signal, "passageEventId": "pas-degraded"})
    client.post("/api/controller/signals/lane-clear", json=signal)
    assert _count(path, "attendance_transactions", "scan_id = ? AND degraded_flag = 1", ("scan-degraded",)) == 1

    assert client.post("/api/demo/watermark-age", json={"watermarkAgeSeconds": 1800}).json()["operatingMode"] == "CANNOT_PROCESS"
    blocked = _scan(client, "scan-blocked", _qr(client))
    assert (blocked["state"], blocked["reason"]) == ("CANNOT_PROCESS", "ELIGIBILITY_DATA_UNUSABLE_OR_EXPIRED")
    assert _actions(app) == [("OPEN", "LANE-BD-01"), ("CLOSE", "LANE-BD-01")]


def test_flow_a_undetermined_open_is_queried_once_and_never_resent(gate):
    app, client, path = gate
    client.post("/api/demo/controller-mode", json={"openMode": "TIMEOUT", "queryStatus": "UNDETERMINED"})

    pending = _scan(client, "scan-unknown", _qr(client))
    assert pending["state"] == "PENDING_RECONCILIATION"
    assert _actions(app) == [("OPEN", "LANE-BD-01")], "openGate is never resent after UNDETERMINED"
    # Lane and token stay held until reconciliation
    client.post("/api/demo/controller-mode", json={})
    assert _scan(client, "scan-next", _qr(client, "EMP-002"))["state"] == "LANE_BUSY"
    assert _count(path, "token_reservations") == 1


def test_flow_a_controller_error_releases_lane(gate):
    app, client, _ = gate
    client.post("/api/demo/controller-mode", json={"openMode": "ERROR"})
    failed = _scan(client, "scan-error", _qr(client))
    # The controller confirms it did not open, so the lane is released straight away
    assert failed["state"] == "CONTROLLER_ERROR"
    client.post("/api/demo/controller-mode", json={})
    assert _scan(client, "scan-after-error", _qr(client, "EMP-002"))["state"] == "OPENED"


def test_flow_a_state_survives_an_app_restart_on_the_same_file(tmp_path):
    path = tmp_path / "factory.sqlite3"
    first = create_app(db_path=str(path))
    with TestClient(first) as client:
        opened = _scan(client, "scan-durable", _qr(client))
        signal = {"commandId": opened["commandId"], "laneId": "LANE-BD-01"}
        client.post("/api/controller/signals/passage", json={**signal, "passageEventId": "pas-durable"})
        client.post("/api/controller/signals/lane-clear", json=signal)
    first.state.uow.connection.close()

    second = create_app(db_path=str(path))
    try:
        with TestClient(second) as client:
            status = client.get("/api/gate/scans/scan-durable").json()
            assert status["state"] == "OPENED"
        assert _count(path, "attendance_transactions", "scan_id = ?", ("scan-durable",)) == 1
        assert _count(path, "gate_cycles", "close_command_id IS NOT NULL") == 1
    finally:
        second.state.uow.connection.close()


# --- Flow B: guard intervention (assisted open, emergency release and reset) ---

def test_flow_b_requires_a_signed_in_guard(gate):
    app, client, path = gate
    response = client.post("/api/interventions/assisted-open", json={
        "supportCaseId": "case-nologin", "employeeId": "EMP-001", "laneId": "LANE-BD-01", "reason": "battery",
    })
    assert response.status_code == 401
    assert _actions(app) == []
    assert _count(path, "support_cases") == 0


def test_flow_b_assisted_open_records_case_without_attendance(gate):
    app, client, path = gate
    body = {"supportCaseId": "case-001", "employeeId": "EMP-001", "laneId": "LANE-BD-02", "reason": "phone battery empty"}
    opened = client.post("/api/interventions/assisted-open", headers=GUARD, json=body).json()
    assert (opened["state"], opened["reason"]) == ("OPENED", "OPEN_CONFIRMED")
    # The same request again does not open the gate a second time
    again = client.post("/api/interventions/assisted-open", headers=GUARD, json=body).json()
    assert again["commandId"] == opened["commandId"]
    assert _actions(app) == [("OPEN", "LANE-BD-02")]
    assert _count(path, "support_cases", "support_case_id = ?", ("case-001",)) == 1
    assert _count(path, "attendance_transactions") == 0, "assisted access needs HR approval before attendance"


def test_flow_b_emergency_holds_lane_until_reset_and_close(gate):
    app, client, path = gate
    release = client.post("/api/interventions/emergency-release", headers=GUARD, json={
        "supportCaseId": "emg-001", "laneId": "LANE-BD-01", "reason": "fire alarm",
    }).json()
    assert release["reason"] == "EMERGENCY_HOLD"

    held = _scan(client, "scan-in-hold", _qr(client))
    assert held["state"] == "LANE_BUSY"

    reset = client.post("/api/interventions/emergency-reset", headers=GUARD, json={
        "supportCaseId": "emg-001", "laneId": "LANE-BD-01",
    }).json()
    assert reset["reason"] == "EMERGENCY_RESET_CONFIRMED"
    # Reset leaves the lane open: it is free again only after LANE_CLEAR and a confirmed closeGate
    assert _scan(client, "scan-before-close", _qr(client, "EMP-002"))["state"] == "LANE_BUSY"
    clear = client.post("/api/controller/signals/lane-clear", json={"commandId": release["commandId"], "laneId": "LANE-BD-01"})
    assert clear.status_code == 200
    assert _scan(client, "scan-after-close", _qr(client, "EMP-002"))["state"] == "OPENED"
    assert [a for a, _ in _actions(app)][:3] == ["EMERGENCY_RELEASE", "RESET", "CLOSE"]
    assert _count(path, "support_cases", "support_case_id = ?", ("emg-001",)) == 1
