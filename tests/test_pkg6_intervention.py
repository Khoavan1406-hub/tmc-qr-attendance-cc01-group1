"""[PACKAGE 6] Unit & integration tests for M3 Gate Intervention (Flow B: Assisted Access & Emergency Release, ADR-07).

Scope note: M3 Gate Intervention and M1 Gate Core run real logic; HR approval and Resolution Feed are simulated.
Not called 'E2E Flow B' as central HR resolution and event relay are simulated out of factory scope.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from test_pkg3_processor import _build_fixture
from tmc_gate.adapters.demo_guard_sessions import DemoGuardSessionStub
from tmc_gate.api.gate_controller import build_gate_core_router
from tmc_gate.api.intervention_controller import build_intervention_router
from tmc_gate.application.intervention import GateInterventionFacade, InterventionService
from tmc_gate.domain.enums import ClosureStatus, CommandOutcome, ScanState
from tmc_gate.ports.repositories import IAuthorizationApi

BD_GUARD_SESSION = {"X-Guard-Session": "demo-session-guard-bd-01"}
LA_GUARD_SESSION = {"X-Guard-Session": "demo-session-guard-la-01"}


class StubAuthorizationApi(IAuthorizationApi):
    """Checks guard role and factory permissions for M3 Gate Intervention (§7.1.4, Table 7.2)."""

    def __init__(self, authorized_guards: dict[str, set[str]] | None = None) -> None:
        self.authorized_guards = authorized_guards or {
            "GUARD-BD-01": {"BD"},
            "GUARD-LA-01": {"LA"},
        }
        self.calls: list[tuple[str, str, str, str]] = []

    def is_authorized(self, actorId: str, role: str, action: str, factoryId: str) -> bool:
        self.calls.append((actorId, role, action, factoryId))
        return role == "SECURITY_GUARD" and factoryId in self.authorized_guards.get(actorId, set())


def _build_pkg6_fixture() -> tuple[
    InterventionService,
    GateInterventionFacade,
    StubAuthorizationApi,
    FastAPI,
    TestClient,
    object,  # processor
    object,  # scan_repo
    object,  # att_repo
    object,  # turnstile
    object,  # obs (audit)
]:
    processor, facade, uow, scan_repo, att_repo, turnstile, outbox, obs = _build_fixture()
    auth_stub = StubAuthorizationApi()

    service = InterventionService(
        factory_id="BD",
        intervention_port=facade,
        auth_api=auth_stub,
        audit_api=obs,
    )
    intervention_facade = GateInterventionFacade(service=service)

    app = FastAPI()
    app.include_router(build_gate_core_router(scan_submission=facade, signal_handler=processor))
    app.include_router(build_intervention_router(facade=intervention_facade, guard_identity=DemoGuardSessionStub()))
    client = TestClient(app)

    return (
        service,
        intervention_facade,
        auth_stub,
        app,
        client,
        processor,
        scan_repo,
        att_repo,
        turnstile,
        obs,
    )


def test_assisted_access_requires_guard_authorization_and_creates_no_automatic_attendance() -> None:
    """Verify:
    - Unauthorized guard is rejected server-side (`GUARD_UNAUTHORIZED`).
    - Guard authorized only for another factory is rejected (`GUARD_UNAUTHORIZED`).
    - Authorized guard records SupportCase and opens the gate.
    - Assisted open never creates automatic attendance from opening alone (ADR-07, I-04),
      even when physical PASSAGE signal subsequently arrives.
    - Endpoints on GateInterventionController behave identically over HTTP.
    """
    (
        service,
        facade,
        auth_stub,
        app,
        client,
        processor,
        scan_repo,
        att_repo,
        turnstile,
        obs,
    ) = _build_pkg6_fixture()

    # 1. Unauthorized guard is rejected server-side
    unauth_result = service.record_and_open_assisted(
        supportCaseId="case-unauth-01",
        guardId="UNKNOWN-GUARD",
        employeeId="EMP-001",
        laneId="LANE-BD-01",
        reason="Employee badge lost",
    )
    assert unauth_result.state == ScanState.REJECTED
    assert unauth_result.reason == "GUARD_UNAUTHORIZED"
    assert scan_repo.get_support_case("case-unauth-01") is None
    assert any(a.get("action") == "GUARD_UNAUTHORIZED" for a in obs.audits)

    # 2. Guard assigned to factory LA cannot authorize in factory BD
    other_factory_result = service.record_and_open_assisted(
        supportCaseId="case-la-guard",
        guardId="GUARD-LA-01",
        employeeId="EMP-001",
        laneId="LANE-BD-01",
        reason="Visiting guard attempt",
    )
    assert other_factory_result.state == ScanState.REJECTED
    assert other_factory_result.reason == "GUARD_UNAUTHORIZED"

    # 3. Authorized guard in factory BD opens gate successfully
    auth_result = service.record_and_open_assisted(
        supportCaseId="case-valid-01",
        guardId="GUARD-BD-01",
        employeeId="EMP-001",
        laneId="LANE-BD-01",
        reason="Smartphone battery died",
    )
    assert auth_result.state == ScanState.OPENED
    assert auth_result.commandId is not None
    cmd_id = auth_result.commandId

    # Verify SupportCase is recorded
    case = scan_repo.get_support_case("case-valid-01")
    assert case is not None
    assert case.guardId == "GUARD-BD-01"
    assert case.employeeId == "EMP-001"
    assert case.isEmergency is False

    # 4. Mandatory ADR-07 / I-04 Check: Opening gate creates NO automatic attendance transaction
    assert len(att_repo.list_transactions()) == 0

    # 5. Subsequent PASSAGE signal does NOT create attendance transaction without HR approval
    processor.onPassage(passageEventId="pas-ast-01", commandId=cmd_id, laneId="LANE-BD-01")
    assert len(att_repo.list_transactions()) == 0

    # 6. LANE_CLEAR safely closes and releases lane
    processor.onLaneClear(commandId=cmd_id, laneId="LANE-BD-01")
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01") is None

    # 7. Verification over HTTP controller endpoints
    # No guard session: rejected before M3 runs, even if the body names a valid guard.
    http_anonymous = client.post(
        "/api/interventions/assisted-open",
        json={
            "supportCaseId": "case-http-anon",
            "guardId": "GUARD-BD-01",
            "employeeId": "EMP-001",
            "laneId": "LANE-BD-01",
            "reason": "Test anonymous via HTTP",
        },
    )
    assert http_anonymous.status_code == 401
    assert scan_repo.get_support_case("case-http-anon") is None

    # Signed-in guard of factory LA: authenticated but not authorized for BD.
    http_unauth = client.post(
        "/api/interventions/assisted-open",
        headers=LA_GUARD_SESSION,
        json={
            "supportCaseId": "case-http-unauth",
            "employeeId": "EMP-001",
            "laneId": "LANE-BD-01",
            "reason": "Test unauth via HTTP",
        },
    )
    assert http_unauth.status_code == 200
    assert http_unauth.json()["state"] == "REJECTED"
    assert http_unauth.json()["reason"] == "GUARD_UNAUTHORIZED"

    http_ok = client.post(
        "/api/interventions/assisted-open",
        headers=BD_GUARD_SESSION,
        json={
            "supportCaseId": "case-http-ok",
            "employeeId": "EMP-001",
            "laneId": "LANE-BD-01",
            "reason": "Screen broken",
        },
    )
    assert http_ok.status_code == 200
    assert http_ok.json()["state"] == "OPENED"
    assert http_ok.json()["commandId"] is not None
    assert len(att_repo.list_transactions()) == 0


def test_emergency_release_hold_blocks_scans_and_unconfirmed_reset_stays_in_hold() -> None:
    """Verify:
    - `emergencyRelease` requires guard authorization.
    - `emergencyRelease` places lane in `EMERGENCY_HOLD` (blocking new QR scans).
    - While in `EMERGENCY_HOLD`, new QR scans are blocked with `LANE_BUSY`.
    - If `resetEmergency` is not confirmed (`UNDETERMINED` / `CONTROLLER_ERROR`), lane does NOT exit `EMERGENCY_HOLD`.
    - Once `RESET_CONFIRMED` is returned, lane exits `EMERGENCY_HOLD` and normal closing rules resume.
    - Controller endpoints for emergency actions behave correctly over HTTP.
    """
    (
        service,
        facade,
        auth_stub,
        app,
        client,
        processor,
        scan_repo,
        att_repo,
        turnstile,
        obs,
    ) = _build_pkg6_fixture()

    # 1. Unauthorized emergency release attempt is rejected
    unauth_emg = service.trigger_emergency_release(
        supportCaseId="emg-unauth",
        guardId="ROGUE-GUARD",
        laneId="LANE-BD-01",
        reason="False alarm",
    )
    assert unauth_emg.state == ScanState.REJECTED
    assert unauth_emg.reason == "GUARD_UNAUTHORIZED"

    # 2. Authorized emergency release places lane in EMERGENCY_HOLD
    emg_result = service.trigger_emergency_release(
        supportCaseId="emg-valid-01",
        guardId="GUARD-BD-01",
        laneId="LANE-BD-01",
        reason="Fire alarm triggered in Sector 4",
    )
    assert emg_result.state == ScanState.OPENED
    assert emg_result.reason == ClosureStatus.EMERGENCY_HOLD.value
    emg_cmd_id = emg_result.commandId

    # Verify emergency SupportCase recorded
    case = scan_repo.get_support_case("emg-valid-01")
    assert case is not None
    assert case.isEmergency is True

    # Check lane status is EMERGENCY_HOLD
    active_cycle = scan_repo.get_active_cycle_for_lane("LANE-BD-01")
    assert active_cycle is not None
    assert active_cycle.closureStatus == ClosureStatus.EMERGENCY_HOLD

    # 3. New QR scans at kiosk are BLOCKED while lane is in EMERGENCY_HOLD
    scan_attempt_resp = client.post(
        "/api/gate/scans",
        json={"scanId": "scan-blocked-by-emg", "qrToken": "VALID-TOKEN", "laneId": "LANE-BD-01"},
    )
    assert scan_attempt_resp.status_code == 200
    scan_body = scan_attempt_resp.json()
    assert scan_body["state"] == "LANE_BUSY"
    assert scan_body["reason"] == "LANE_HAS_ACTIVE_OR_UNRESOLVED_CYCLE"

    # LANE_CLEAR signal arriving during EMERGENCY_HOLD does NOT close or release the lane
    processor.onLaneClear(commandId=emg_cmd_id, laneId="LANE-BD-01")
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01").closureStatus == ClosureStatus.EMERGENCY_HOLD

    # 4. Unauthorized reset is rejected
    unauth_rst = service.reset_emergency(
        supportCaseId="emg-valid-01",
        guardId="STRANGER",
        laneId="LANE-BD-01",
    )
    assert unauth_rst.state == ScanState.REJECTED
    assert unauth_rst.reason == "GUARD_UNAUTHORIZED"
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01").closureStatus == ClosureStatus.EMERGENCY_HOLD

    # 5. Controller fails to confirm reset (e.g. CONTROLLER_ERROR) -> lane stays in EMERGENCY_HOLD
    turnstile.reset_outcome = CommandOutcome.CONTROLLER_ERROR
    failed_reset = service.reset_emergency(
        supportCaseId="emg-valid-01",
        guardId="GUARD-BD-01",
        laneId="LANE-BD-01",
    )
    assert failed_reset.state == ScanState.UNDETERMINED
    assert failed_reset.reason == ClosureStatus.EMERGENCY_HOLD.value
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01").closureStatus == ClosureStatus.EMERGENCY_HOLD

    # 6. Controller timeout/undetermined reset -> lane STILL stays in EMERGENCY_HOLD
    turnstile.reset_outcome = CommandOutcome.UNDETERMINED
    undetermined_reset = service.reset_emergency(
        supportCaseId="emg-valid-01",
        guardId="GUARD-BD-01",
        laneId="LANE-BD-01",
    )
    assert undetermined_reset.state == ScanState.UNDETERMINED
    assert undetermined_reset.reason == ClosureStatus.EMERGENCY_HOLD.value
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01").closureStatus == ClosureStatus.EMERGENCY_HOLD

    # 7. The guard repeats the reset: the stored UNDETERMINED command is queried (never resent) and the
    #    controller now reports RESET_CONFIRMED -> lane exits EMERGENCY_HOLD
    turnstile.reset_outcome = CommandOutcome.CONTROLLER_ERROR  # would show if a new RESET were sent
    turnstile.query_outcome = CommandOutcome.RESET_CONFIRMED
    http_reset = client.post(
        "/api/interventions/emergency-reset",
        headers=BD_GUARD_SESSION,
        json={
            "supportCaseId": "emg-valid-01",
            "laneId": "LANE-BD-01",
        },
    )
    assert http_reset.status_code == 200
    reset_body = http_reset.json()
    assert reset_body["state"] == "OPENED"
    assert reset_body["reason"] == "EMERGENCY_RESET_CONFIRMED"
    assert reset_body["commandId"] == undetermined_reset.commandId
    assert undetermined_reset.commandId != failed_reset.commandId  # CONTROLLER_ERROR allowed one new RESET

    # Lane is now OPENED (exited EMERGENCY_HOLD)
    cleared_cycle = scan_repo.get_active_cycle_for_lane("LANE-BD-01")
    assert cleared_cycle is not None
    assert cleared_cycle.closureStatus == ClosureStatus.OPENED

    # 8. Controller reports LANE_CLEAR for the emergency cycle -> lane successfully closes and releases
    processor.onLaneClear(commandId=emg_cmd_id, laneId="LANE-BD-01")
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01") is None


def test_assisted_access_facade_separate_record_and_authorize() -> None:
    """Verify separate recordAssistedAccess (Flow B3-B4) and authorizeAssistedOpen on GateInterventionFacade."""
    service, facade, auth_stub, _, _, _, scan_repo, _, _, _ = _build_pkg6_fixture()

    # Step 1: recordAssistedAccess records case without opening
    rec_ok = facade.recordAssistedAccess(
        supportCaseId="case-split-01",
        guardId="GUARD-BD-01",
        employeeId="EMP-001",
        laneId="LANE-BD-01",
        reason="Two-step assisted flow",
    )
    assert rec_ok is True
    assert scan_repo.get_support_case("case-split-01") is not None
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01") is None

    # Step 2: authorizeAssistedOpen opens the free lane for recorded case
    open_res = facade.authorizeAssistedOpen(
        supportCaseId="case-split-01",
        guardId="GUARD-BD-01",
        laneId="LANE-BD-01",
    )
    assert open_res.state == ScanState.OPENED
    assert open_res.commandId is not None


def test_emergency_reset_on_normal_lane_without_hold_is_rejected() -> None:
    """Verify requesting emergency reset on a lane that is not in EMERGENCY_HOLD is rejected."""
    service, _, _, _, _, _, _, _, _, _ = _build_pkg6_fixture()

    res = service.reset_emergency(
        supportCaseId="emg-idle",
        guardId="GUARD-BD-01",
        laneId="LANE-BD-01",
    )
    assert res.state == ScanState.REJECTED
    assert res.reason == "LANE_NOT_IN_EMERGENCY_HOLD"
