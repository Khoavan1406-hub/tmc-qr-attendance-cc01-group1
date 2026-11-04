"""[PACKAGE 3] Comprehensive unit & API tests for GateTransactionProcessor, GateCoreFacade, and GateCoreController.

Demonstrates Dependency Inversion Principle (DIP): tests M1 Gate Core coordination logic and all 9 edge cases
using lightweight in-memory port doubles, completely independent of Packages 1, 2, 4, 5, and 6.
"""

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tmc_gate.api.gate_controller import build_gate_core_router
from tmc_gate.application.access_decision import AccessDecision
from tmc_gate.application.facade import GateCoreFacade
from tmc_gate.application.processor import GateTransactionProcessor
from tmc_gate.domain.enums import (
    ClosureStatus,
    CommandOutcome,
    Direction,
    OperatingMode,
    PassageStatus,
    ScanState,
)
from tmc_gate.domain.models import (
    AttendanceTransaction,
    DecisionResult,
    GateCommand,
    GateCycle,
    Lane,
    PassageEvent,
    ScanAttempt,
    SupportCase,
    TokenClaims,
    TokenReservation,
)
from tmc_gate.domain.snapshot import EligibilitySnapshot
from tmc_gate.ports.repositories import (
    IAttendanceRepository,
    IAuditApi,
    IHealthApi,
    IOutboxRepository,
    IScanRepository,
    ISnapshotRepository,
)
from tmc_gate.ports.turnstile import ICloseGate, IEmergencyGate, IOpenGate

# --- Lightweight In-Memory Port Doubles for Isolated Package 3 Testing (DIP) ---


class FakeAccessDecision(AccessDecision):
    """Test double for AccessDecision returning configurable DecisionResult."""

    def __init__(self) -> None:
        self.next_result: DecisionResult | None = None

    def evaluate(self, qrToken: str, laneId: str, now: datetime | None = None) -> DecisionResult:
        if self.next_result is not None:
            return self.next_result
        t = now or datetime.now(UTC)
        if qrToken.startswith("INVALID"):
            return DecisionResult(
                allowed=False,
                operatingMode=OperatingMode.NORMAL,
                snapshotVersion=1,
                reason="INVALID_QR_SIGNATURE",
            )
        if qrToken.startswith("CANNOT_PROCESS"):
            return DecisionResult(
                allowed=False,
                operatingMode=OperatingMode.CANNOT_PROCESS,
                snapshotVersion=1,
                reason="ELIGIBILITY_DATA_UNUSABLE_OR_EXPIRED",
            )
        mode = OperatingMode.DEGRADED if qrToken.startswith("DEGRADED:") else OperatingMode.NORMAL
        return DecisionResult(
            allowed=True,
            operatingMode=mode,
            snapshotVersion=1,
            reason="ALLOWED",
            claims=TokenClaims(
                tokenId=f"tok-{qrToken}",
                employeeId="EMP-001",
                factoryId="BD",
                issuedAt=t,
                expiresAt=t + timedelta(seconds=60),
            ),
            direction=Direction.ENTRY,
        )


class FakeScanRepo(IScanRepository):
    """In-memory implementation of IScanRepository for testing Package 3."""

    def __init__(self) -> None:
        self.lanes: dict[str, Lane] = {
            "LANE-BD-01": Lane("LANE-BD-01", "ST-01", "BD", Direction.ENTRY),
            "LANE-BD-02": Lane("LANE-BD-02", "ST-01", "BD", Direction.EXIT),
        }
        self.scans: dict[str, ScanAttempt] = {}
        self.tokens: dict[str, TokenReservation] = {}
        self.cycles: dict[str, GateCycle] = {}
        self.commands: dict[str, GateCommand] = {}
        self.cases: dict[str, SupportCase] = {}
        self.outbox: list[dict[str, object]] = []
        self.simulate_storage_failure: bool = False

    def get_scan(self, scanId: str) -> ScanAttempt | None:
        return self.scans.get(scanId)

    def save_scan(self, scan: ScanAttempt, audit_payload: dict[str, object] | None = None) -> None:
        self.scans[scan.scanId] = scan

    def get_lane(self, laneId: str) -> Lane | None:
        return self.lanes.get(laneId)

    def get_active_cycle_for_lane(self, laneId: str) -> GateCycle | None:
        lane = self.lanes.get(laneId)
        if lane is None or lane.activeCycleId is None:
            return None
        return self.cycles.get(lane.activeCycleId)

    def atomic_reserve_lane_claim_token_and_accept(
        self,
        scan: ScanAttempt,
        reservation: TokenReservation,
        cycle: GateCycle,
        command: GateCommand,
    ) -> tuple[bool, str]:
        if self.simulate_storage_failure:
            return False, "STORAGE_FAILURE"
        lane = self.lanes.get(scan.laneId)
        if lane is None:
            return False, "STORAGE_FAILURE"
        if lane.activeCycleId is not None:
            return False, "LANE_BUSY"
        if reservation.tokenId in self.tokens:
            return False, "TOKEN_ALREADY_CLAIMED"

        self.tokens[reservation.tokenId] = reservation
        self.cycles[cycle.cycleId] = cycle
        lane.activeCycleId = cycle.cycleId
        self.scans[scan.scanId] = scan
        self.commands[command.commandId] = command
        return True, "ACCEPTED"

    def update_opening_outcome(
        self,
        scanId: str,
        cycleId: str,
        commandId: str,
        scanState: str,
        commandOutcome: str,
        releaseLane: bool,
        outboxEvent: dict[str, object] | None = None,
        auditPayload: dict[str, object] | None = None,
    ) -> None:
        if scanId in self.scans:
            self.scans[scanId].state = ScanState(scanState)
            self.scans[scanId].reason = commandOutcome
        if commandId in self.commands:
            self.commands[commandId].outcome = CommandOutcome(commandOutcome)
        if releaseLane and cycleId in self.cycles:
            self.cycles[cycleId].closureStatus = ClosureStatus.CLOSED
            lane = self.lanes.get(self.cycles[cycleId].laneId)
            if lane and lane.activeCycleId == cycleId:
                lane.activeCycleId = None
        if outboxEvent is not None:
            self.outbox.append(outboxEvent)

    def get_cycle_by_command(self, commandId: str) -> GateCycle | None:
        cmd = self.commands.get(commandId)
        if cmd is None:
            return None
        return self.cycles.get(cmd.cycleId)

    def get_cycle(self, cycleId: str) -> GateCycle | None:
        return self.cycles.get(cycleId)

    def update_cycle(
        self,
        cycle: GateCycle,
        releaseLane: bool = False,
        command: GateCommand | None = None,
    ) -> None:
        self.cycles[cycle.cycleId] = cycle
        if command is not None:
            self.commands[command.commandId] = command
        lane = self.lanes.get(cycle.laneId)
        if lane is not None:
            lane.activeCycleId = None if releaseLane else cycle.cycleId

    def save_support_case(self, case: SupportCase) -> None:
        self.cases[case.supportCaseId] = case

    def get_support_case(self, supportCaseId: str) -> SupportCase | None:
        return self.cases.get(supportCaseId)


class FakeAttendanceRepo(IAttendanceRepository):
    """In-memory implementation of IAttendanceRepository enforcing unique passageEventId and scanId."""

    def __init__(self, scan_repo: FakeScanRepo) -> None:
        self._scan_repo = scan_repo
        self.passages: dict[str, PassageEvent] = {}
        self.by_scan: dict[str, AttendanceTransaction] = {}

    def store_passage_and_attendance_atomic(
        self,
        passage: PassageEvent,
        transaction: AttendanceTransaction | None,
        cycle: GateCycle,
        outboxEvent: dict[str, object],
        auditPayload: dict[str, object],
    ) -> bool:
        if passage.passageEventId in self.passages:
            return False
        self.passages[passage.passageEventId] = passage
        self._scan_repo.cycles[cycle.cycleId] = cycle
        if transaction is not None and transaction.scanId not in self.by_scan:
            self.by_scan[transaction.scanId] = transaction
        self._scan_repo.outbox.append(outboxEvent)
        return True

    def get_by_scan_id(self, scanId: str) -> AttendanceTransaction | None:
        return self.by_scan.get(scanId)

    def list_transactions(self) -> list[AttendanceTransaction]:
        return list(self.by_scan.values())


class FakeTurnstilePort(IOpenGate, ICloseGate, IEmergencyGate):
    """Configurable test double for ITurnstileControl ports."""

    def __init__(self) -> None:
        self.open_outcome: CommandOutcome = CommandOutcome.OPEN_CONFIRMED
        self.close_outcome: CommandOutcome = CommandOutcome.CLOSED
        self.open_calls: list[tuple[str, str]] = []
        self.close_calls: list[tuple[str, str]] = []

    def openGate(self, commandId: str, laneId: str) -> CommandOutcome:
        self.open_calls.append((commandId, laneId))
        return self.open_outcome

    def closeGate(self, commandId: str, laneId: str) -> CommandOutcome:
        self.close_calls.append((commandId, laneId))
        return self.close_outcome

    def emergencyRelease(self, commandId: str, laneId: str) -> CommandOutcome:
        return CommandOutcome.EMERGENCY_RELEASED

    def resetEmergency(self, commandId: str, laneId: str) -> CommandOutcome:
        return CommandOutcome.RESET_CONFIRMED


class DummyOutboxRepo(IOutboxRepository):
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def append_event(self, event: dict[str, object]) -> None:
        self.events.append(event)

    def list_pending_events(self) -> list[dict[str, object]]:
        return list(self.events)


class DummySnapshotRepo(ISnapshotRepository):
    def __init__(self) -> None:
        self.snapshot = EligibilitySnapshot("BD", 1, datetime.now(UTC))

    def get_snapshot(self) -> EligibilitySnapshot:
        return self.snapshot

    def save_snapshot(self, snapshot: EligibilitySnapshot) -> None:
        self.snapshot = snapshot


class DummyAuditAndHealth(IAuditApi, IHealthApi):
    def __init__(self) -> None:
        self.audits: list[dict[str, object]] = []
        self.alerts: list[dict[str, object]] = []

    def append_async(self, record: dict[str, object]) -> None:
        self.audits.append(record)

    def report_event_async(self, metric_or_alert: dict[str, object]) -> None:
        self.alerts.append(metric_or_alert)


def _build_fixture() -> tuple[
    GateTransactionProcessor,
    GateCoreFacade,
    FakeAccessDecision,
    FakeScanRepo,
    FakeAttendanceRepo,
    FakeTurnstilePort,
    DummyOutboxRepo,
    DummyAuditAndHealth,
]:
    decision = FakeAccessDecision()
    scan_repo = FakeScanRepo()
    att_repo = FakeAttendanceRepo(scan_repo)
    turnstile = FakeTurnstilePort()
    outbox = DummyOutboxRepo()
    snap_repo = DummySnapshotRepo()
    obs = DummyAuditAndHealth()

    processor = GateTransactionProcessor(
        factory_id="BD",
        access_decision=decision,
        scan_repo=scan_repo,
        attendance_repo=att_repo,
        open_gate_port=turnstile,
        close_gate_port=turnstile,
        emergency_gate_port=turnstile,
        audit_api=obs,
        health_api=obs,
    )
    facade = GateCoreFacade(
        processor=processor,
        scan_repo=scan_repo,
        snapshot_repo=snap_repo,
        outbox_repo=outbox,
    )
    return processor, facade, decision, scan_repo, att_repo, turnstile, outbox, obs


# --- Unit & Controller Tests Covering Flow A and All 9 Edge Cases ---


def test_flow_a_scan_open_passage_creates_attendance_and_lane_clear_closes() -> None:
    """Flow A1–A9: Valid scan -> OPENED (no attendance yet) -> onPassage creates 1 AttendanceTransaction -> onLaneClear closes gate."""
    processor, facade, _, scan_repo, att_repo, turnstile, _, _ = _build_fixture()

    app = FastAPI()
    app.include_router(build_gate_core_router(scan_submission=facade, signal_handler=processor))
    client = TestClient(app)

    # Step A1–A7: Submit scan via GateCoreController
    resp = client.post("/api/gate/scans", json={"scanId": "scan-1", "qrToken": "QR-VALID-1", "laneId": "LANE-BD-01"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "OPENED"
    cmd_id = data["commandId"]

    # ADR-02 & I-07: Opening alone never creates attendance
    assert len(att_repo.list_transactions()) == 0
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01") is not None

    # Step A8: Controller reports PASSAGE -> creates 1 AttendanceTransaction
    pas_resp = client.post(
        "/api/controller/signals/passage",
        json={"commandId": cmd_id, "laneId": "LANE-BD-01", "passageEventId": "pas-1"},
    )
    assert pas_resp.status_code == 200
    assert len(att_repo.list_transactions()) == 1
    assert att_repo.get_by_scan_id("scan-1") is not None

    # Step A9: Controller reports LANE_CLEAR -> closeGate sent once and lane released
    clr_resp = client.post(
        "/api/controller/signals/lane-clear",
        json={"commandId": cmd_id, "laneId": "LANE-BD-01"},
    )
    assert clr_resp.status_code == 200
    assert len(turnstile.close_calls) == 1
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01") is None


def test_edge_case_1_and_2_and_3_idempotent_scan_lane_busy_and_qr_replay() -> None:
    """Edge Cases 1, 2, 3: Duplicate scanId retry, LANE_BUSY preserving token, and QR_TOKEN_REPLAY_DETECTED."""
    processor, _, _, scan_repo, _, turnstile, _, _ = _build_fixture()

    # 1. First scan opens LANE-BD-01
    res1 = processor.processScan(scanId="scan-a", qrToken="TOKEN-A", laneId="LANE-BD-01")
    assert res1.state == ScanState.OPENED
    assert len(turnstile.open_calls) == 1

    # Edge Case 1: Resending the exact same scanId returns stored OPENED without calling openGate again
    res1_retry = processor.processScan(scanId="scan-a", qrToken="TOKEN-A", laneId="LANE-BD-01")
    assert res1_retry.state == ScanState.OPENED
    assert len(turnstile.open_calls) == 1

    # Edge Case 2: Second employee scans on busy LANE-BD-01 -> LANE_BUSY and TOKEN-B is NOT claimed
    res2_busy = processor.processScan(scanId="scan-b1", qrToken="TOKEN-B", laneId="LANE-BD-01")
    assert res2_busy.state == ScanState.LANE_BUSY
    assert "tok-TOKEN-B" not in scan_repo.tokens

    # Edge Case 3: Replaying already-claimed TOKEN-A on free LANE-BD-02 -> REJECTED (QR_TOKEN_REPLAY_DETECTED)
    res_replay = processor.processScan(scanId="scan-replay", qrToken="TOKEN-A", laneId="LANE-BD-02")
    assert res_replay.state == ScanState.REJECTED
    assert res_replay.reason == "QR_TOKEN_REPLAY_DETECTED"

    # Once LANE-BD-01 clears and closes, second employee can use TOKEN-B on LANE-BD-01
    assert res1.commandId is not None
    processor.onLaneClear(commandId=res1.commandId, laneId="LANE-BD-01")
    res2_ok = processor.processScan(scanId="scan-b2", qrToken="TOKEN-B", laneId="LANE-BD-01")
    assert res2_ok.state == ScanState.OPENED


def test_edge_case_4_and_5_storage_failure_controller_error_and_undetermined() -> None:
    """Edge Cases 4 & 5: Storage failure before openGate, CONTROLLER_ERROR releasing lane, and UNDETERMINED keeping lane."""
    processor, _, _, scan_repo, _, turnstile, _, obs = _build_fixture()

    # Edge Case 4: Database failure before ACCEPTED -> CANNOT_PROCESS and openGate is never called
    scan_repo.simulate_storage_failure = True
    res_fail = processor.processScan(scanId="scan-db-err", qrToken="TOKEN-1", laneId="LANE-BD-01")
    assert res_fail.state == ScanState.CANNOT_PROCESS
    assert len(turnstile.open_calls) == 0
    scan_repo.simulate_storage_failure = False

    # Edge Case 5a: Controller explicitly confirms non-execution -> CONTROLLER_ERROR and lane is released
    turnstile.open_outcome = CommandOutcome.CONTROLLER_ERROR
    res_ctrl_err = processor.processScan(scanId="scan-ctrl-err", qrToken="TOKEN-2", laneId="LANE-BD-01")
    assert res_ctrl_err.state == ScanState.CONTROLLER_ERROR
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01") is None

    # Edge Case 5b: Controller timeout -> UNDETERMINED, lane stays reserved, no automatic resend (I-03)
    turnstile.open_outcome = CommandOutcome.UNDETERMINED
    res_undet = processor.processScan(scanId="scan-undet", qrToken="TOKEN-3", laneId="LANE-BD-01")
    assert res_undet.state == ScanState.UNDETERMINED
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01") is not None
    assert len(obs.alerts) >= 2


def test_edge_case_6_7_8_9_timeout_15s_assisted_open_and_emergency_hold() -> None:
    """Edge Cases 6, 7, 8, 9: 15s passage timeout, duplicate passageEventId, Assisted Open (ADR-07), and Emergency Hold."""
    processor, facade, _, scan_repo, att_repo, turnstile, outbox, _ = _build_fixture()

    # Edge Case 6: 15s passage timeout flags NO_PASSAGE & PENDING_RECONCILIATION without closing the gate
    res_open = processor.processScan(scanId="scan-no-pass", qrToken="TOKEN-NP", laneId="LANE-BD-01")
    assert res_open.state == ScanState.OPENED
    active_cycle = scan_repo.get_active_cycle_for_lane("LANE-BD-01")
    assert active_cycle is not None

    assert processor.handlePassageTimeout(active_cycle.cycleId) is True
    assert scan_repo.get_scan("scan-no-pass").state == ScanState.PENDING_RECONCILIATION
    assert len(turnstile.close_calls) == 0  # Timer never calls closeGate!

    # Reconcile scan through GateCoreFacade.applyScanResolution
    assert facade.applyScanResolution("dec-01", "scan-no-pass", "APPROVED_NO_ENTRY") is True
    assert scan_repo.get_scan("scan-no-pass").state == ScanState.RECONCILED
    assert len(outbox.list_pending_events()) == 1

    # Close LANE-BD-01 via LANE_CLEAR
    assert res_open.commandId is not None
    processor.onLaneClear(commandId=res_open.commandId, laneId="LANE-BD-01")

    # Edge Case 8: Assisted Open records PassageEvent but creates NO automatic AttendanceTransaction (ADR-07)
    facade.recordIntervention(
        SupportCase(
            supportCaseId="case-ast-1",
            factoryId="BD",
            laneId="LANE-BD-01",
            guardId="GUARD-BD-01",
            employeeId="EMP-001",
            reason="Forgot phone",
            occurredAt=datetime.now(UTC),
        )
    )
    ast_res = facade.requestAssistedOpen("case-ast-1", "LANE-BD-01")
    assert ast_res.state == ScanState.OPENED
    assert ast_res.commandId is not None
    processor.onPassage("pas-ast-1", ast_res.commandId, "LANE-BD-01")
    assert "pas-ast-1" in att_repo.passages
    assert len(att_repo.list_transactions()) == 0
    processor.onLaneClear(ast_res.commandId, "LANE-BD-01")

    # Edge Case 9: Emergency Release during an OPENED scan puts scan into PENDING_RECONCILIATION and holds lane
    res_before_emg = processor.processScan(scanId="scan-emg", qrToken="TOKEN-EMG", laneId="LANE-BD-01")
    assert res_before_emg.state == ScanState.OPENED

    emg_res = facade.requestEmergencyRelease("case-emg-1", "LANE-BD-01")
    assert emg_res.reason == ClosureStatus.EMERGENCY_HOLD.value
    assert scan_repo.get_scan("scan-emg").state == ScanState.PENDING_RECONCILIATION

    # While in EMERGENCY_HOLD, onLaneClear must NOT close the gate
    assert emg_res.commandId is not None
    close_count_before = len(turnstile.close_calls)
    processor.onLaneClear(emg_res.commandId, "LANE-BD-01")
    assert len(turnstile.close_calls) == close_count_before

    # After resetEmergency, onLaneClear closes and releases the lane
    rst_res = facade.resetEmergency("case-emg-1", "LANE-BD-01")
    assert rst_res.reason == "EMERGENCY_RESET_CONFIRMED"
    processor.onLaneClear(emg_res.commandId, "LANE-BD-01")
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01") is None

    # Deadline check (§5.4) & Second distinct passageEventId anomaly (§4.2.2 Flow A8, §4.2.5)
    expired_dl = datetime.now(UTC) - timedelta(seconds=0.1)
    dl_res = processor.processScan(
        scanId="scan-expired-dl",
        qrToken="TOKEN-DL",
        laneId="LANE-BD-01",
        deadline=expired_dl,
    )
    assert dl_res.state == ScanState.CANNOT_PROCESS
    assert dl_res.reason == "REQUEST_DEADLINE_EXPIRED_BEFORE_COMMAND"

    # Normal scan with repeated passageEventId (ignored) vs second distinct passageEventId (flagged anomaly, still 1 tx)
    res_anom = processor.processScan(scanId="scan-anom", qrToken="TOKEN-ANOM", laneId="LANE-BD-01")
    assert res_anom.commandId is not None
    processor.onPassage("pas-first", res_anom.commandId, "LANE-BD-01")
    assert len(att_repo.list_transactions()) == 1
    processor.onPassage("pas-first", res_anom.commandId, "LANE-BD-01")  # Duplicate passageEventId -> ignored
    assert len(att_repo.list_transactions()) == 1
    processor.onPassage("pas-second-tailgate", res_anom.commandId, "LANE-BD-01")  # Second distinct passage -> flagged!
    assert len(att_repo.list_transactions()) == 1
    assert scan_repo.get_scan("scan-anom").reason == "FLAGGED_SECOND_PASSAGE_ANOMALY"

