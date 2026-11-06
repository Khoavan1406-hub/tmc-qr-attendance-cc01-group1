"""PR #8 second review (Codex, 06/11, commit 5ce4502): transactions closed on read-only paths,
demo guard sessions only in demo mode, one emergencyRelease per supportCaseId.

Business repositories and controllers are fakes. SQLite is used only to check the IUnitOfWork
context-manager lifecycle, so this is not a Package 4 integration test.
"""

from dataclasses import replace
from datetime import UTC, datetime
import importlib
import sqlite3
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from test_pkg3_processor import _build_fixture
from test_pkg6_intervention import _build_pkg6_fixture
from tmc_gate.domain.enums import ScanState
from tmc_gate.domain.enums import CommandOutcome
from tmc_gate.domain.models import ScanResult, SupportCase
from tmc_gate.ports.repositories import IUnitOfWork


class SqliteBoundaryUow(IUnitOfWork):
    """Real BEGIN/COMMIT/ROLLBACK; inherit the port's __exit__ unchanged."""

    def __init__(self, conn):
        self.conn = conn

    def begin(self, timeout_ms=None):
        self.conn.execute("BEGIN IMMEDIATE")

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()


@pytest.mark.parametrize("conflicting", [False, True], ids=["identical-retry", "conflicting-retry"])
def test_existing_support_case_closes_its_transaction(conflicting):
    processor, *_ = _build_fixture()
    conn = sqlite3.connect(":memory:", isolation_level=None)
    processor._uow = SqliteBoundaryUow(conn)
    original = SupportCase(
        supportCaseId="review-case",
        factoryId="BD",
        laneId="LANE-BD-01",
        guardId="GUARD-BD-01",
        reason="Phone battery depleted",
        occurredAt=datetime.now(UTC),
        employeeId="EMP-001",
        isEmergency=False,
    )
    try:
        assert processor.recordIntervention(original) is True
        assert conn.in_transaction is False
        retry = replace(original, employeeId="EMP-002") if conflicting else replace(original)
        assert processor.recordIntervention(retry) is (not conflicting)
        transaction_left_open = conn.in_transaction
        # The next operation must be able to start its own transaction.
        next_error = None
        try:
            assert processor.recordIntervention(replace(original, supportCaseId="next-case")) is True
        except sqlite3.OperationalError as exc:
            next_error = str(exc)
        assert not transaction_left_open, (
            f"recordIntervention returned with BEGIN IMMEDIATE still open; next request: {next_error}"
        )
        assert next_error is None
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.parametrize("demo_mode", [True, False], ids=["demo-enabled", "demo-disabled"])
def test_fixed_demo_guard_session_is_not_accepted_outside_demo(demo_mode):
    app_module = importlib.import_module("tmc_gate.api.app")
    app = app_module.create_app(demo_mode=demo_mode)
    # Isolate identity handling from unfinished Package 4/5 adapters. No gate
    # operation is executed: the facade method is replaced with a recorder.
    with patch.object(
        app_module.GateInterventionFacade,
        "recordAssistedAccessAndOpen",
        return_value=ScanResult("review-case", ScanState.REJECTED, "LANE-BD-01", reason="PROBE_ONLY"),
    ) as facade_call:
        with TestClient(app) as client:
            response = client.post(
                "/api/interventions/assisted-open",
                headers={"X-Guard-Session": "demo-session-guard-bd-01"},
                json={
                    "supportCaseId": "review-case",
                    "employeeId": "EMP-001",
                    "laneId": "LANE-BD-01",
                    "reason": "Phone battery depleted",
                },
            )
        if demo_mode:
            assert response.status_code == 200
            assert facade_call.call_args.kwargs["guardId"] == "GUARD-BD-01"
        else:
            assert facade_call.call_count == 0, (
                f"demo_mode=False still accepts a fixed demo session: HTTP {response.status_code}, "
                f"guardId={facade_call.call_args.kwargs['guardId']}"
            )
            assert response.status_code in (401, 403, 404, 503)


@pytest.mark.parametrize("outcome", [CommandOutcome.EMERGENCY_RELEASED, CommandOutcome.UNDETERMINED])
def test_identical_emergency_case_does_not_send_release_twice(outcome):
    """The PR docs promise idempotency for reused supportCaseId, including emergency cases."""
    service, _, _, _, _, _, _, _, turnstile, _ = _build_pkg6_fixture()
    with patch.object(turnstile, "emergencyRelease", return_value=outcome) as release:
        first = service.trigger_emergency_release("same-emergency", "GUARD-BD-01", "LANE-BD-01", "Fire alarm")
        second = service.trigger_emergency_release("same-emergency", "GUARD-BD-01", "LANE-BD-01", "Fire alarm")
        assert release.call_count == 1, (
            f"Same case sent emergencyRelease {release.call_count} times: "
            f"first={first.commandId}, second={second.commandId}, outcome={outcome.value}"
        )
        assert second.commandId == first.commandId


def test_repeated_emergency_after_undetermined_queries_instead_of_resending():
    service, _, _, _, _, _, repo, _, turnstile, _ = _build_pkg6_fixture()
    with patch.object(turnstile, "emergencyRelease", return_value=CommandOutcome.UNDETERMINED) as release:
        first = service.trigger_emergency_release("emg-q", "GUARD-BD-01", "LANE-BD-01", "Fire alarm")
        assert first.state == ScanState.UNDETERMINED
        turnstile.query_outcome = CommandOutcome.EMERGENCY_RELEASED
        second = service.trigger_emergency_release("emg-q", "GUARD-BD-01", "LANE-BD-01", "Fire alarm")
    assert release.call_count == 1
    assert second.commandId == first.commandId
    assert second.state == ScanState.OPENED
    assert repo.get_command(first.commandId).outcome == CommandOutcome.EMERGENCY_RELEASED


def test_demo_disabled_with_injected_identity_provider_uses_it():
    from tmc_gate.adapters.demo_guard_sessions import DemoGuardSessionStub

    app_module = importlib.import_module("tmc_gate.api.app")
    app = app_module.create_app(demo_mode=False, guard_identity=DemoGuardSessionStub({"real-session": "GUARD-BD-01"}))
    with patch.object(
        app_module.GateInterventionFacade,
        "recordAssistedAccessAndOpen",
        return_value=ScanResult("c", ScanState.REJECTED, "LANE-BD-01", reason="PROBE_ONLY"),
    ) as facade_call:
        with TestClient(app) as client:
            body = {"supportCaseId": "c", "employeeId": "EMP-001", "laneId": "LANE-BD-01", "reason": "r"}
            fixed_demo = client.post("/api/interventions/assisted-open", headers={"X-Guard-Session": "demo-session-guard-bd-01"}, json=body)
            injected = client.post("/api/interventions/assisted-open", headers={"X-Guard-Session": "real-session"}, json=body)
    assert fixed_demo.status_code == 401
    assert injected.status_code == 200
    assert facade_call.call_count == 1
