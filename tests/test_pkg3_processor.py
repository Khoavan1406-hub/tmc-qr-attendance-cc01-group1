"""[PACKAGE 3] Comprehensive unit tests for GateTransactionProcessor, GateCoreFacade, and GateCoreController.

Demonstrates Dependency Inversion Principle (DIP): tests M1 Gate Core coordination logic, IUnitOfWork transaction
boundaries, IControllerStatusQuery, and all Flow A / Flow B edge cases using lightweight in-memory port doubles.
"""

import hashlib
import json
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
    CommandType,
    DedupStatus,
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
from tmc_gate.domain.snapshot import EligibilitySnapshot, EmployeeRight
from tmc_gate.ports.repositories import (
    IAttendanceRepository,
    IAuditApi,
    IHealthApi,
    IOutboxRepository,
    IScanRepository,
    ISnapshotRepository,
    IUnitOfWork,
)
from tmc_gate.ports.turnstile import (
    ICloseGate,
    IControllerStatusQuery,
    IEmergencyGate,
    IOpenGate,
)


class FakeUnitOfWork(IUnitOfWork):
    """Tracks begin / commit / rollback calls for Package 3 unit tests."""

    def __init__(self) -> None:
        self.begun: int = 0
        self.committed: int = 0
        self.rolled_back: int = 0

    def begin(self, timeout_ms: int | None = None) -> None:
        self.begun += 1

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1


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
            snapshotVersion=7 if mode == OperatingMode.DEGRADED else 1,
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
        timeout_ms: int | None = None,
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

    def save_support_case(
        self,
        case: SupportCase,
        outboxEvent: dict[str, object] | None = None,
        auditPayload: dict[str, object] | None = None,
    ) -> None:
        self.cases[case.supportCaseId] = case
        if outboxEvent is not None:
            self.outbox.append(outboxEvent)

    def get_support_case(self, supportCaseId: str) -> SupportCase | None:
        return self.cases.get(supportCaseId)

    def get_command_by_support_case(self, supportCaseId: str, commandType: str) -> GateCommand | None:
        for cmd in reversed(list(self.commands.values())):
            if cmd.supportCaseId == supportCaseId and cmd.commandType.value == commandType:
                return cmd
        return None


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

    def list_passages_for_cycle(self, cycleId: str) -> list[PassageEvent]:
        return [p for p in self.passages.values() if p.cycleId == cycleId]


class FakeTurnstilePort(IOpenGate, ICloseGate, IEmergencyGate, IControllerStatusQuery):
    """Configurable test double for ITurnstileControl + IControllerStatusQuery."""

    def __init__(self) -> None:
        self.open_outcome: CommandOutcome = CommandOutcome.OPEN_CONFIRMED
        self.close_outcome: CommandOutcome = CommandOutcome.CLOSED
        self.reset_outcome: CommandOutcome = CommandOutcome.RESET_CONFIRMED
        self.query_outcome: CommandOutcome = CommandOutcome.UNDETERMINED
        self.open_calls: list[tuple[str, str]] = []
        self.close_calls: list[tuple[str, str]] = []
        self.open_timeouts: list[int] = []

    def openGate(self, commandId: str, laneId: str, timeout_ms: int) -> CommandOutcome:
        self.open_calls.append((commandId, laneId))
        self.open_timeouts.append(timeout_ms)
        return self.open_outcome

    def closeGate(self, commandId: str, laneId: str) -> CommandOutcome:
        self.close_calls.append((commandId, laneId))
        return self.close_outcome

    def emergencyRelease(self, commandId: str, laneId: str) -> CommandOutcome:
        return CommandOutcome.EMERGENCY_RELEASED

    def resetEmergency(self, commandId: str, laneId: str) -> CommandOutcome:
        return self.reset_outcome

    def queryStatus(self, commandId: str, laneId: str) -> CommandOutcome:
        return self.query_outcome


class FakeOutboxRepo(IOutboxRepository):
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.processed: dict[tuple[str, str], str] = {}

    def append_event(self, event: dict[str, object]) -> None:
        self.events.append(event)

    def list_pending_events(self) -> list[dict[str, object]]:
        return list(self.events)

    def check_and_mark_processed(
        self,
        consumer: str,
        eventId: str,
        payload: dict[str, object],
    ) -> DedupStatus:
        h = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        key = (consumer, eventId)
        if key not in self.processed:
            self.processed[key] = h
            return DedupStatus.APPLIED
        if self.processed[key] == h:
            return DedupStatus.DUPLICATE_IGNORED
        return DedupStatus.CONFLICT_FLAGGED


class FakeSnapshotRepo(ISnapshotRepository):
    def __init__(self) -> None:
        self.snapshot = EligibilitySnapshot(
            factoryId="BD",
            snapshotVersion=1,
            confirmedWatermark=datetime.now(UTC),
            clockErrorBoundSec=0.5,
            employees={
                "EMP-001": EmployeeRight("EMP-001", active=True, allowedFactories={"BD"}, allowedLanes={"LANE-BD-01", "LANE-BD-02"}),
                "EMP-SUSPENDED": EmployeeRight("EMP-SUSPENDED", active=False, allowedFactories={"BD"}, allowedLanes={"LANE-BD-01"}),
            },
            lanes={"LANE-BD-01": Direction.ENTRY, "LANE-BD-02": Direction.EXIT},
        )

    def get_snapshot(self) -> EligibilitySnapshot:
        return self.snapshot

    def save_snapshot(self, snapshot: EligibilitySnapshot) -> None:
        self.snapshot = snapshot


class FakeAuditAndHealth(IAuditApi, IHealthApi):
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
    FakeUnitOfWork,
    FakeScanRepo,
    FakeAttendanceRepo,
    FakeTurnstilePort,
    FakeOutboxRepo,
    FakeAuditAndHealth,
]:
    uow = FakeUnitOfWork()
    decision = FakeAccessDecision()
    scan_repo = FakeScanRepo()
    att_repo = FakeAttendanceRepo(scan_repo)
    turnstile = FakeTurnstilePort()
    outbox = FakeOutboxRepo()
    snap_repo = FakeSnapshotRepo()
    obs = FakeAuditAndHealth()

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
        uow=uow,
        status_query_port=turnstile,
        snapshot_repo=snap_repo,
    )
    facade = GateCoreFacade(
        processor=processor,
        scan_repo=scan_repo,
        snapshot_repo=snap_repo,
        outbox_repo=outbox,
        uow=uow,
    )
    return processor, facade, uow, scan_repo, att_repo, turnstile, outbox, obs


# --- Package 3 Tests ---


def test_flow_a_scan_open_passage_creates_attendance_and_lane_clear_closes() -> None:
    """Flow A1–A9: Valid scan -> OPENED (no attendance yet) -> onPassage creates 1 AttendanceTransaction -> onLaneClear closes gate."""
    processor, facade, uow, scan_repo, att_repo, turnstile, _, _ = _build_fixture()

    app = FastAPI()
    app.include_router(build_gate_core_router(scan_submission=facade, signal_handler=processor))
    client = TestClient(app)

    resp = client.post("/api/gate/scans", json={"scanId": "scan-1", "qrToken": "QR-VALID-1", "laneId": "LANE-BD-01"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "OPENED"
    cmd_id = data["commandId"]

    assert len(att_repo.list_transactions()) == 0
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01") is not None

    pas_resp = client.post(
        "/api/controller/signals/passage",
        json={"commandId": cmd_id, "laneId": "LANE-BD-01", "passageEventId": "pas-1"},
    )
    assert pas_resp.status_code == 200
    assert len(att_repo.list_transactions()) == 1
    assert att_repo.get_by_scan_id("scan-1") is not None

    clr_resp = client.post(
        "/api/controller/signals/lane-clear",
        json={"commandId": cmd_id, "laneId": "LANE-BD-01"},
    )
    assert clr_resp.status_code == 200
    assert len(turnstile.close_calls) == 1
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01") is None
    assert uow.committed >= 4


def test_lane_clear_before_passage_and_missing_closed_keeps_lane_reserved() -> None:
    """Required Tracker & Report scenarios:
    1. LANE_CLEAR arrives BEFORE PASSAGE: gate closes and releases lane; subsequent PASSAGE still creates
       AttendanceTransaction without re-locking the closed lane.
    2. Missing CLOSED confirmation (CLOSE_UNCERTAIN / CLOSE_FAILED) keeps the lane reserved (LANE_BUSY).
    """
    processor, _, _, scan_repo, att_repo, turnstile, _, _ = _build_fixture()

    # 1. LANE_CLEAR arrives before PASSAGE
    res1 = processor.processScan(scanId="scan-lc-first", qrToken="TOK-LC-1", laneId="LANE-BD-01")
    assert res1.state == ScanState.OPENED
    assert res1.commandId is not None

    processor.onLaneClear(commandId=res1.commandId, laneId="LANE-BD-01")
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01") is None

    processor.onPassage(passageEventId="pas-after-lc", commandId=res1.commandId, laneId="LANE-BD-01")
    assert att_repo.get_by_scan_id("scan-lc-first") is not None
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01") is None

    # 2. Missing CLOSED confirmation keeps lane reserved
    turnstile.close_outcome = CommandOutcome.UNDETERMINED
    res2 = processor.processScan(scanId="scan-unclosed", qrToken="TOK-LC-2", laneId="LANE-BD-01")
    assert res2.state == ScanState.OPENED
    assert res2.commandId is not None

    processor.onLaneClear(commandId=res2.commandId, laneId="LANE-BD-01")
    cycle = scan_repo.get_active_cycle_for_lane("LANE-BD-01")
    assert cycle is not None and cycle.closureStatus == ClosureStatus.CLOSE_UNCERTAIN

    res3 = processor.processScan(scanId="scan-blocked-by-unclosed", qrToken="TOK-LC-3", laneId="LANE-BD-01")
    assert res3.state == ScanState.LANE_BUSY


def test_edge_cases_idempotency_lane_busy_replay_storage_failure_and_status_query() -> None:
    """Covers duplicate scanId, LANE_BUSY preserving token, QR replay rejection, storage failure,
    and UNDETERMINED with/without IControllerStatusQuery.
    """
    processor, _, _, scan_repo, _, turnstile, _, _ = _build_fixture()

    res1 = processor.processScan(scanId="scan-a", qrToken="TOKEN-A", laneId="LANE-BD-01")
    assert res1.state == ScanState.OPENED
    assert len(turnstile.open_calls) == 1

    # Duplicate scanId retry returns stored result without calling openGate again
    res1_retry = processor.processScan(scanId="scan-a", qrToken="TOKEN-A", laneId="LANE-BD-01")
    assert res1_retry.state == ScanState.OPENED
    assert len(turnstile.open_calls) == 1

    # Busy lane preserves TOKEN-B
    res2_busy = processor.processScan(scanId="scan-b1", qrToken="TOKEN-B", laneId="LANE-BD-01")
    assert res2_busy.state == ScanState.LANE_BUSY
    assert "tok-TOKEN-B" not in scan_repo.tokens

    # Replay of TOKEN-A on free LANE-BD-02 is rejected
    res_replay = processor.processScan(scanId="scan-replay", qrToken="TOKEN-A", laneId="LANE-BD-02")
    assert res_replay.state == ScanState.REJECTED
    assert res_replay.reason == "QR_TOKEN_REPLAY_DETECTED"

    assert res1.commandId is not None
    processor.onLaneClear(commandId=res1.commandId, laneId="LANE-BD-01")

    # Storage failure before openGate -> CANNOT_PROCESS
    scan_repo.simulate_storage_failure = True
    assert processor.processScan(scanId="scan-db-err", qrToken="TOKEN-B", laneId="LANE-BD-01").state == ScanState.CANNOT_PROCESS
    scan_repo.simulate_storage_failure = False

    # When status_query_port is absent and openGate is UNDETERMINED -> moves to PENDING_RECONCILIATION (Table 4.23a)
    processor._status_query_port = None
    turnstile.open_outcome = CommandOutcome.UNDETERMINED
    res_undet = processor.processScan(scanId="scan-no-query", qrToken="TOKEN-B", laneId="LANE-BD-01")
    assert res_undet.state == ScanState.PENDING_RECONCILIATION


def test_15s_timeout_degraded_alerts_assisted_and_emergency_reset() -> None:
    """Covers 15s passage timeout (never closes gate), degraded mode start/end alerts,
    assisted open repeat idempotency, and unconfirmed emergency reset keeping EMERGENCY_HOLD.
    """
    processor, facade, _, scan_repo, att_repo, turnstile, _, obs = _build_fixture()

    # 1. Degraded scan logs DEGRADED_MODE_STARTED and tags AttendanceTransaction.snapshotVersion
    res_deg = processor.processScan(scanId="scan-deg", qrToken="DEGRADED:TOK-1", laneId="LANE-BD-01")
    assert res_deg.state == ScanState.OPENED
    assert res_deg.degradedFlag is True
    assert any(a.get("alert") == "DEGRADED_MODE_STARTED" for a in obs.alerts)
    assert res_deg.commandId is not None
    processor.onPassage("pas-deg", res_deg.commandId, "LANE-BD-01")
    tx = att_repo.get_by_scan_id("scan-deg")
    assert tx is not None and tx.degradedFlag is True and tx.snapshotVersion == 7
    processor.onLaneClear(res_deg.commandId, "LANE-BD-01")

    # 2. 15s timeout marks NO_PASSAGE and PENDING_RECONCILIATION without calling closeGate
    res_open = processor.processScan(scanId="scan-timeout-15s", qrToken="TOK-2", laneId="LANE-BD-01")
    assert any(a.get("alert") == "DEGRADED_MODE_ENDED" for a in obs.alerts)
    cycle = scan_repo.get_active_cycle_for_lane("LANE-BD-01")
    assert cycle is not None
    close_count = len(turnstile.close_calls)
    assert processor.handlePassageTimeout(cycle.cycleId) is True
    assert len(turnstile.close_calls) == close_count
    assert scan_repo.get_scan("scan-timeout-15s").state == ScanState.PENDING_RECONCILIATION
    assert res_open.commandId is not None
    processor.onLaneClear(res_open.commandId, "LANE-BD-01")

    # 3. Emergency release + unconfirmed reset stays in EMERGENCY_HOLD until RESET_CONFIRMED
    emg_res = facade.requestEmergencyRelease("case-emg-1", "LANE-BD-01")
    assert emg_res.reason == ClosureStatus.EMERGENCY_HOLD.value

    turnstile.reset_outcome = CommandOutcome.UNDETERMINED
    rst_fail = facade.requestEmergencyReset("case-emg-1", "LANE-BD-01")
    assert rst_fail.reason == ClosureStatus.EMERGENCY_HOLD.value

    turnstile.reset_outcome = CommandOutcome.RESET_CONFIRMED
    rst_ok = facade.requestEmergencyReset("case-emg-1", "LANE-BD-01")
    assert rst_ok.reason == "EMERGENCY_RESET_CONFIRMED"
