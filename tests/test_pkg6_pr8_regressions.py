"""PR #8 review regressions (Codex, 06/11): guard identity over HTTP, unsaved support cases, reused supportCaseId.

All hardware and persistence are fakes; SQLite behaviour is covered by Package 4.
"""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from test_pkg6_intervention import BD_GUARD_SESSION, LA_GUARD_SESSION, _build_pkg6_fixture, StubAuthorizationApi
from test_pkg3_processor import FakeAuditAndHealth
from tmc_gate.application.intervention import InterventionService
from tmc_gate.domain.enums import ClosureStatus, CommandOutcome, ScanState
from tmc_gate.domain.models import ScanResult
from tmc_gate.ports.inbound import IInterventionRequest

LANE = "LANE-BD-01"
GUARD = "GUARD-BD-01"


@pytest.mark.parametrize("endpoint", ["assisted-open", "emergency-release", "emergency-reset"])
def test_intervention_http_requires_trusted_guard_identity(endpoint):
    service, _, _, _, client, _, repo, _, turnstile, _ = _build_pkg6_fixture()
    if endpoint == "emergency-reset":
        service.trigger_emergency_release("original-emergency", GUARD, LANE, "Verified fire alarm")
    before_open_count = len(turnstile.open_calls)
    # No authentication, no guardId, no lane, no employee: the request model supplies defaults.
    response = client.post(f"/api/interventions/{endpoint}", json={"supportCaseId": "anonymous-request"})
    body = response.json()
    denied = response.status_code in (401, 403, 422) or body.get("state") in ("REJECTED", "CANNOT_PROCESS")
    assert denied, f"Anonymous {endpoint} returned {response.status_code}: {body}"
    assert len(turnstile.open_calls) == before_open_count
    if endpoint == "emergency-reset":
        assert repo.get_active_cycle_for_lane(LANE).closureStatus == ClosureStatus.EMERGENCY_HOLD


@pytest.mark.parametrize("operation", ["assisted", "emergency", "record-only"])
def test_failed_case_storage_must_not_open_or_report_success(operation):
    port = Mock(spec=IInterventionRequest)
    port.recordIntervention.return_value = False
    port.requestAssistedOpen.return_value = ScanResult("case-save-failed", ScanState.OPENED, LANE)
    port.requestEmergencyRelease.return_value = ScanResult("case-save-failed", ScanState.OPENED, LANE)
    service = InterventionService("BD", port, StubAuthorizationApi(), FakeAuditAndHealth())
    if operation == "assisted":
        result = service.record_and_open_assisted("case-save-failed", GUARD, "EMP-001", LANE, "Battery depleted")
        assert port.requestAssistedOpen.call_count == 0, "M3 still requested opening after recordIntervention returned False"
        assert result.state != ScanState.OPENED
    elif operation == "emergency":
        result = service.trigger_emergency_release("case-save-failed", GUARD, LANE, "Fire alarm")
        assert port.requestEmergencyRelease.call_count == 0, "M3 still requested release after recordIntervention returned False"
        assert result.state != ScanState.OPENED
    else:
        assert service.record_assisted_case("case-save-failed", GUARD, "EMP-001", LANE, "Battery depleted") is False


def test_reusing_support_case_id_cannot_replace_original_employee_evidence():
    service, _, _, _, _, _, repo, _, turnstile, _ = _build_pkg6_fixture()
    first = service.record_and_open_assisted("same-case", GUARD, "EMP-001", LANE, "Phone battery depleted")
    assert first.state == ScanState.OPENED
    original = deepcopy(repo.get_support_case("same-case"))
    second = service.record_and_open_assisted("same-case", GUARD, "EMP-SUSPENDED", LANE, "Changed employee")
    assert repo.get_support_case("same-case") == original, f"Case evidence was overwritten and response remained {second.state}"
    assert len(turnstile.open_calls) == 1


def test_m3_uses_3000ms_assisted_timeout_from_current_gate_core():
    service, _, _, _, _, _, _, attendance, turnstile, _ = _build_pkg6_fixture()
    result = service.record_and_open_assisted("timeout-case", GUARD, "EMP-001", LANE, "Phone unavailable")
    assert result.state == ScanState.OPENED
    assert turnstile.open_timeouts == [3000]
    assert attendance.list_transactions() == []


def test_m3_does_not_use_removed_m1_reset_alias():
    port = Mock(spec=IInterventionRequest)
    assert not hasattr(port, "resetEmergency")
    port.requestEmergencyReset.return_value = ScanResult("reset-case", ScanState.OPENED, LANE, reason="EMERGENCY_RESET_CONFIRMED")
    service = InterventionService("BD", port, StubAuthorizationApi(), FakeAuditAndHealth())
    result = service.reset_emergency("reset-case", GUARD, LANE)
    assert result.reason == "EMERGENCY_RESET_CONFIRMED"
    port.requestEmergencyReset.assert_called_once()


@pytest.mark.parametrize("outcome", [CommandOutcome.CONTROLLER_ERROR, CommandOutcome.UNDETERMINED])
def test_unconfirmed_reset_keeps_hold_with_new_processor(outcome):
    service, _, _, _, _, _, repo, _, turnstile, _ = _build_pkg6_fixture()
    service.trigger_emergency_release("emergency-case", GUARD, LANE, "Verified fire alarm")
    turnstile.reset_outcome = outcome
    service.reset_emergency("emergency-case", GUARD, LANE)
    assert repo.get_active_cycle_for_lane(LANE).closureStatus == ClosureStatus.EMERGENCY_HOLD


def test_body_guard_id_is_ignored_and_session_guard_is_used():
    service, _, auth, _, client, _, repo, _, turnstile, _ = _build_pkg6_fixture()
    response = client.post(
        "/api/interventions/assisted-open",
        headers=LA_GUARD_SESSION,
        json={"supportCaseId": "spoofed", "guardId": GUARD, "employeeId": "EMP-001", "laneId": LANE, "reason": "Spoof"},
    )
    assert response.status_code == 200
    assert response.json()["reason"] == "GUARD_UNAUTHORIZED"
    assert auth.calls[-1][0] == "GUARD-LA-01"
    assert turnstile.open_calls == []


def test_unknown_guard_session_is_rejected_with_401():
    _, _, _, _, client, _, repo, _, turnstile, _ = _build_pkg6_fixture()
    response = client.post(
        "/api/interventions/emergency-release",
        headers={"X-Guard-Session": "made-up"},
        json={"supportCaseId": "emg-x", "laneId": LANE, "reason": "Test"},
    )
    assert response.status_code == 401
    assert repo.get_support_case("emg-x") is None
    assert repo.get_active_cycle_for_lane(LANE) is None


def test_resending_identical_support_case_is_idempotent():
    service, _, _, _, client, _, repo, _, turnstile, _ = _build_pkg6_fixture()
    body = {"supportCaseId": "retry-case", "employeeId": "EMP-001", "laneId": LANE, "reason": "Battery depleted"}
    first = client.post("/api/interventions/assisted-open", headers=BD_GUARD_SESSION, json=body)
    second = client.post("/api/interventions/assisted-open", headers=BD_GUARD_SESSION, json=body)
    assert first.json()["state"] == "OPENED"
    assert second.json()["commandId"] == first.json()["commandId"]
    assert len(turnstile.open_calls) == 1


def test_emergency_cannot_reuse_assisted_support_case_id():
    service, _, _, _, _, _, repo, _, turnstile, _ = _build_pkg6_fixture()
    service.record_and_open_assisted("shared-id", GUARD, "EMP-001", LANE, "Battery depleted")
    original = deepcopy(repo.get_support_case("shared-id"))
    result = service.trigger_emergency_release("shared-id", GUARD, LANE, "Fire alarm")
    assert result.state == ScanState.REJECTED
    assert result.reason == "SUPPORT_CASE_NOT_RECORDED"
    assert repo.get_support_case("shared-id") == original
