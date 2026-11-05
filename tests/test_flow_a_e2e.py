"""[PACKAGE 3] End-to-end Flow A HTTP tests for GateCoreController, GateCoreFacade, and GateTransactionProcessor."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_pkg3_processor import _build_fixture
from tmc_gate.api.gate_controller import build_gate_core_router


def test_flow_a_http_end_to_end_lifecycle() -> None:
    """Flow A over HTTP: POST /api/gate/scans -> GET /api/gate/scans/{id} -> PASSAGE -> LANE_CLEAR."""
    processor, facade, _, scan_repo, att_repo, _, _, _ = _build_fixture()

    app = FastAPI()
    app.include_router(build_gate_core_router(scan_submission=facade, signal_handler=processor))
    client = TestClient(app)

    # 1. Kiosk submits valid QR scan
    scan_resp = client.post(
        "/api/gate/scans",
        json={"scanId": "scan-http-1", "qrToken": "VALID-HTTP-QR", "laneId": "LANE-BD-01"},
    )
    assert scan_resp.status_code == 200
    body = scan_resp.json()
    assert body["state"] == "OPENED"
    cmd_id = body["commandId"]

    # 2. Kiosk status inquiry after lost response returns stored OPENED without downgrading state
    status_resp = client.get("/api/gate/scans/scan-http-1")
    assert status_resp.status_code == 200
    assert status_resp.json()["state"] == "OPENED"

    # 3. Before PASSAGE, no attendance transaction exists (ADR-02, I-07)
    assert len(att_repo.list_transactions()) == 0

    # 4. Controller reports PASSAGE -> creates 1 AttendanceTransaction
    pas_resp = client.post(
        "/api/controller/signals/passage",
        json={"commandId": cmd_id, "laneId": "LANE-BD-01", "passageEventId": "pas-http-1"},
    )
    assert pas_resp.status_code == 200
    assert len(att_repo.list_transactions()) == 1

    # 5. Controller reports LANE_CLEAR -> sends closeGate once and releases lane
    clr_resp = client.post(
        "/api/controller/signals/lane-clear",
        json={"commandId": cmd_id, "laneId": "LANE-BD-01"},
    )
    assert clr_resp.status_code == 200
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01") is None
