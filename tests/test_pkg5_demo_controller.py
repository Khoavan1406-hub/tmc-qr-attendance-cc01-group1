"""[PACKAGE 5] Demo helper endpoints (/api/demo/*) and the demo_mode flag."""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tmc_gate.adapters.turnstile_adapter import ControllerApiClient
from tmc_gate.api.app import create_app
from tmc_gate.api.demo_controller import build_demo_router
from tmc_gate.domain.enums import Direction
from tmc_gate.domain.models import AttendanceTransaction
from tmc_gate.domain.snapshot import EligibilitySnapshot

DEMO_PATHS = {
    "/api/demo/qr-tokens",
    "/api/demo/watermark-age",
    "/api/demo/controller-mode",
    "/api/demo/attendance-transactions",
}


def _paths(app: FastAPI) -> set[str]:
    return set(app.openapi()["paths"])


class _SnapshotHolder:
    def __init__(self) -> None:
        self.snapshot = EligibilitySnapshot(
            factoryId="BD",
            snapshotVersion=3,
            confirmedWatermark=datetime.now(UTC),
            clockErrorBoundSec=0.5,
            lanes={"LANE-BD-01": Direction.ENTRY},
        )

    def get_snapshot(self) -> EligibilitySnapshot:
        return self.snapshot

    def save_snapshot(self, snapshot: EligibilitySnapshot) -> None:
        self.snapshot = snapshot


class _FakeIssuer:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, int | None]] = []

    def issue_qr_token(self, employee_id: str, factory_id: str, lifetime_sec: int | None = None, **_: object):
        if self.fail:
            raise NotImplementedError("TODO [Package 2]")
        self.calls.append((employee_id, factory_id, lifetime_sec))
        return "payload.signature", {"jti": "tok-1", "sub": employee_id, "aud": factory_id}


class _FakeAttendance:
    def __init__(self, transactions: list[AttendanceTransaction] | None = None, fail: bool = False) -> None:
        self.transactions = transactions or []
        self.fail = fail

    def list_transactions(self) -> list[AttendanceTransaction]:
        if self.fail:
            raise NotImplementedError("TODO [Package 4]")
        return self.transactions


def _demo_client(issuer=None, attendance=None, snapshot=None, controller=None) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_demo_router(
            qr_issuer=issuer or _FakeIssuer(),
            snapshot_repo=snapshot or _SnapshotHolder(),
            attendance_repo=attendance or _FakeAttendance(),
            controller_client=controller or ControllerApiClient(),
        )
    )
    return TestClient(app)


# --- demo_mode flag ---


def test_demo_router_is_mounted_only_in_demo_mode() -> None:
    assert DEMO_PATHS <= _paths(create_app(demo_mode=True))
    assert DEMO_PATHS.isdisjoint(_paths(create_app(demo_mode=False)))

    client = TestClient(create_app(demo_mode=False))
    assert client.post("/api/demo/controller-mode", json={}).status_code == 404
    assert client.get("/api/demo/attendance-transactions").status_code == 404


# --- QR issuance ---


def test_issue_demo_qr_returns_token_and_claims() -> None:
    issuer = _FakeIssuer()
    resp = _demo_client(issuer=issuer).post(
        "/api/demo/qr-tokens", json={"employeeId": "EMP-002", "factoryId": "BD", "lifetimeSec": 60}
    )

    assert resp.status_code == 200
    assert resp.json() == {"qrToken": "payload.signature", "claims": {"jti": "tok-1", "sub": "EMP-002", "aud": "BD"}}
    assert issuer.calls == [("EMP-002", "BD", 60)]


def test_issue_demo_qr_reports_501_while_issuer_is_not_implemented() -> None:
    resp = _demo_client(issuer=_FakeIssuer(fail=True)).post("/api/demo/qr-tokens", json={})
    assert resp.status_code == 501


def test_issue_demo_qr_rejects_non_positive_lifetime() -> None:
    assert _demo_client().post("/api/demo/qr-tokens", json={"lifetimeSec": 0}).status_code == 422


# --- Watermark age (WAN outage simulation) ---


@pytest.mark.parametrize(
    ("age_sec", "expected"),
    [
        (10, "NORMAL"),
        (60, "NORMAL"),
        (61, "DEGRADED"),
        (300, "DEGRADED"),
        (1799, "DEGRADED"),
        (1800, "CANNOT_PROCESS"),
        (2700, "CANNOT_PROCESS"),
    ],
)
def test_watermark_age_boundaries(age_sec: int, expected: str) -> None:
    holder = _SnapshotHolder()
    resp = _demo_client(snapshot=holder).post("/api/demo/watermark-age", json={"watermarkAgeSeconds": age_sec})

    assert resp.status_code == 200
    assert resp.json()["operatingMode"] == expected
    assert holder.snapshot.snapshotVersion == 3  # only the watermark is aged, rights are untouched


@pytest.mark.parametrize(("bound_sec", "expected"), [(1.999, "NORMAL"), (2.0, "CANNOT_PROCESS")])
def test_clock_error_bound_of_2s_or_more_cannot_process(bound_sec: float, expected: str) -> None:
    resp = _demo_client().post(
        "/api/demo/watermark-age", json={"watermarkAgeSeconds": 10, "clockErrorBoundSec": bound_sec}
    )
    assert resp.json()["operatingMode"] == expected


def test_watermark_age_works_on_the_real_app_snapshot() -> None:
    app = create_app(demo_mode=True)
    resp = TestClient(app).post("/api/demo/watermark-age", json={"watermarkAgeSeconds": 2700})

    assert resp.status_code == 200
    assert resp.json()["operatingMode"] == "CANNOT_PROCESS"
    assert app.state.snapshot_repo.get_snapshot().getOperatingMode(datetime.now(UTC)).value == "CANNOT_PROCESS"


def test_watermark_age_rejects_negative_age() -> None:
    assert _demo_client().post("/api/demo/watermark-age", json={"watermarkAgeSeconds": -1}).status_code == 422


# --- Controller simulator mode ---


def test_controller_mode_updates_the_simulator() -> None:
    app = create_app(demo_mode=True)
    resp = TestClient(app).post(
        "/api/demo/controller-mode",
        json={
            "openMode": "RECEIPT_ONLY",
            "closeMode": "TIMEOUT",
            "emergencyMode": "ERROR",
            "resetMode": "NETWORK_ERROR",
            "queryStatus": "CONFIRMED",
            "latencyMs": 900,
        },
    )

    assert resp.status_code == 200
    controller = app.state.controller_client
    assert controller.simulated_open_mode == "RECEIPT_ONLY"
    assert controller.simulated_close_mode == "TIMEOUT"
    assert controller.simulated_emergency_mode == "ERROR"
    assert controller.simulated_reset_mode == "NETWORK_ERROR"
    assert controller.simulated_query_status == "CONFIRMED"
    assert controller.simulated_latency_ms == 900


def test_controller_mode_rejects_values_outside_the_contract() -> None:
    client = _demo_client()
    assert client.post("/api/demo/controller-mode", json={"openMode": "CLOSED"}).status_code == 422
    assert client.post("/api/demo/controller-mode", json={"latencyMs": -5}).status_code == 422


# --- Attendance listing ---


def test_attendance_transactions_are_serialized() -> None:
    tx = AttendanceTransaction(
        transactionId="att-1",
        scanId="scan-1",
        passageEventId="pas-1",
        employeeId="EMP-001",
        factoryId="BD",
        laneId="LANE-BD-01",
        direction=Direction.ENTRY,
        occurredAt=datetime(2026, 11, 7, 1, 0, tzinfo=UTC),
    )
    resp = _demo_client(attendance=_FakeAttendance([tx])).get("/api/demo/attendance-transactions")

    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["scanId"] == "scan-1"
    assert body[0]["direction"] == "ENTRY"
    assert body[0]["occurredAt"].startswith("2026-11-07T01:00:00")


def test_attendance_transactions_report_501_while_repository_is_not_implemented() -> None:
    resp = _demo_client(attendance=_FakeAttendance(fail=True)).get("/api/demo/attendance-transactions")
    assert resp.status_code == 501
