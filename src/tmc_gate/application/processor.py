"""[PACKAGE 3] GateTransactionProcessor component of M1 Gate Core (Figure 4.2, §4.2.2 Flow A, §4.2.5, §4.3, ADR-02).

Coordinates scan states, atomic lane reservation + token claim (via IUnitOfWork), controller commands,
optional status inquiry (IControllerStatusQuery), passage-based attendance creation, and safe closure.
"""

import uuid
from datetime import UTC, datetime, timedelta

from tmc_gate.application.access_decision import AccessDecision
from tmc_gate.domain.enums import (
    ClosureStatus,
    CommandOutcome,
    CommandType,
    Direction,
    OperatingMode,
    PassageStatus,
    ScanState,
)
from tmc_gate.domain.models import (
    AttendanceTransaction,
    GateCommand,
    GateCycle,
    PassageEvent,
    ScanAttempt,
    ScanResult,
    SupportCase,
    TokenReservation,
)
from tmc_gate.ports.repositories import (
    IAttendanceRepository,
    IAuditApi,
    IHealthApi,
    IScanRepository,
    ISnapshotRepository,
    IUnitOfWork,
)
from tmc_gate.ports.turnstile import (
    ICloseGate,
    IControllerStatusQuery,
    IEmergencyGate,
    IOpenGate,
    IPassageEvents,
)


class _NoOpUnitOfWork(IUnitOfWork):
    """Fallback no-op UnitOfWork when none is injected in isolated unit tests."""

    def begin(self, timeout_ms: int | None = None) -> None:
        pass

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class GateTransactionProcessor(IPassageEvents):
    """Core coordinator for the factory gate cycle (§4.1.6, §4.2.2 Flow A, §4.3)."""

    PASSAGE_OBSERVATION_WINDOW_MS: int = 15_000
    PASSAGE_OBSERVATION_WINDOW_SEC: int = 15
    SCAN_DEADLINE_BUDGET_MS: int = 2_000
    SCAN_DEADLINE_BUDGET_SEC: float = 2.0
    OPEN_COMMAND_BUDGET_MS: int = 700  # Flow A only (Table 5.5)
    ASSISTED_OPEN_TIMEOUT_MS: int = 3_000  # Flow B assisted opening, configurable

    def __init__(
        self,
        factory_id: str,
        access_decision: AccessDecision,
        scan_repo: IScanRepository,
        attendance_repo: IAttendanceRepository,
        open_gate_port: IOpenGate,
        close_gate_port: ICloseGate,
        emergency_gate_port: IEmergencyGate,
        audit_api: IAuditApi,
        health_api: IHealthApi,
        uow: IUnitOfWork | None = None,
        status_query_port: IControllerStatusQuery | None = None,
        snapshot_repo: ISnapshotRepository | None = None,
    ) -> None:
        self._factory_id = factory_id
        self._uow: IUnitOfWork = uow or _NoOpUnitOfWork()
        self._access_decision = access_decision
        self._scan_repo = scan_repo
        self._attendance_repo = attendance_repo
        self._open_gate_port = open_gate_port
        self._close_gate_port = close_gate_port
        self._emergency_gate_port = emergency_gate_port
        self._audit_api = audit_api
        self._health_api = health_api
        self._status_query_port = status_query_port
        self._snapshot_repo = snapshot_repo
        self._in_degraded_mode: bool = False

    def _track_degraded_mode_transition(self, mode: OperatingMode, snapshot_version: int) -> None:
        """Section 5.7.2 (p. 92): Write the start and end of degraded mode to the audit log
        and raise an alert through the factory deployment of Monitoring.
        """
        if mode == OperatingMode.DEGRADED and not self._in_degraded_mode:
            self._in_degraded_mode = True
            self._audit_api.append_async(
                {"action": "DEGRADED_MODE_STARTED", "factoryId": self._factory_id, "snapshotVersion": snapshot_version}
            )
            self._health_api.report_event_async(
                {"alert": "DEGRADED_MODE_STARTED", "factoryId": self._factory_id, "snapshotVersion": snapshot_version}
            )
        elif mode == OperatingMode.NORMAL and self._in_degraded_mode:
            self._in_degraded_mode = False
            self._audit_api.append_async(
                {"action": "DEGRADED_MODE_ENDED", "factoryId": self._factory_id, "snapshotVersion": snapshot_version}
            )
            self._health_api.report_event_async(
                {"alert": "DEGRADED_MODE_ENDED", "factoryId": self._factory_id, "snapshotVersion": snapshot_version}
            )

    @staticmethod
    def _remaining_ms(now: datetime, deadline: datetime) -> int:
        delta = deadline.astimezone(UTC) - now.astimezone(UTC)
        delta_us = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
        return delta_us // 1000

    def processScan(
        self,
        scanId: str,
        qrToken: str,
        laneId: str,
        now: datetime | None = None,
        deadline: datetime | None = None,
    ) -> ScanResult:
        """Execute steps A1–A7 of Flow A (§4.2.2):
        A2 Duplicate check -> A3 Access checks -> A4 Atomic reserve lane + claim token + store ACCEPTED
        -> A5 Check remaining 2s deadline & send openGate command -> A6 Store opening result -> A7 Return kiosk result.
        """
        current_time = now or datetime.now(UTC)
        eff_deadline = deadline or (current_time + timedelta(milliseconds=self.SCAN_DEADLINE_BUDGET_MS))

        # Step A2: Duplicate scanId check (idempotent retry from kiosk; never downgrades stored OPENED)
        existing = self._scan_repo.get_scan(scanId)
        if existing is not None:
            return ScanResult(
                scanId=existing.scanId,
                state=existing.state,
                laneId=existing.laneId,
                degradedFlag=existing.degradedFlag,
                reason=existing.reason,
            )

        # Timeout rule 1 (§4.2.5, §5.4): If request deadline has expired before sending command, return CANNOT_PROCESS
        remaining_ms = self._remaining_ms(current_time, eff_deadline)
        if remaining_ms <= 0:
            return ScanResult(
                scanId=scanId,
                state=ScanState.CANNOT_PROCESS,
                laneId=laneId,
                reason="REQUEST_DEADLINE_EXPIRED_BEFORE_COMMAND",
            )

        # Step A3: Evaluate QR token and eligibility against local snapshot
        decision = self._access_decision.evaluate(qrToken=qrToken, laneId=laneId, now=current_time)
        self._track_degraded_mode_transition(decision.operatingMode, decision.snapshotVersion)
        degraded = decision.operatingMode == OperatingMode.DEGRADED

        if not decision.allowed or decision.claims is None:
            terminal_state = (
                ScanState.CANNOT_PROCESS
                if decision.operatingMode == OperatingMode.CANNOT_PROCESS
                else ScanState.REJECTED
            )
            failed_attempt = ScanAttempt(
                scanId=scanId,
                factoryId=self._factory_id,
                laneId=laneId,
                state=terminal_state,
                createdAt=current_time,
                tokenId=decision.claims.tokenId if decision.claims else None,
                employeeId=decision.claims.employeeId if decision.claims else None,
                degradedFlag=degraded,
                snapshotVersion=decision.snapshotVersion,
                reason=decision.reason,
            )
            with self._uow:
                self._scan_repo.save_scan(
                    failed_attempt,
                    audit_payload={
                        "action": "SCAN_EVALUATED",
                        "scanId": scanId,
                        "laneId": laneId,
                        "outcome": terminal_state.value,
                        "reason": decision.reason,
                    },
                )
                self._uow.commit()
            return ScanResult(
                scanId=scanId,
                state=terminal_state,
                laneId=laneId,
                degradedFlag=degraded,
                reason=decision.reason,
            )

        # Step A4: Reserve the lane, claim tokenId, and store ACCEPTED atomically via IUnitOfWork (ADR-02, ADR-11)
        cycle_id = f"cyc-{uuid.uuid4().hex[:12]}"
        command_id = f"cmd-{uuid.uuid4().hex[:12]}"

        accepted_attempt = ScanAttempt(
            scanId=scanId,
            factoryId=self._factory_id,
            laneId=laneId,
            state=ScanState.ACCEPTED,
            createdAt=current_time,
            tokenId=decision.claims.tokenId,
            employeeId=decision.claims.employeeId,
            degradedFlag=degraded,
            snapshotVersion=decision.snapshotVersion,
            reason="ACCEPTED",
        )
        reservation = TokenReservation(
            tokenId=decision.claims.tokenId,
            scanId=scanId,
            factoryId=self._factory_id,
            claimedAt=current_time,
            expiresAt=decision.claims.expiresAt,
        )
        cycle = GateCycle(
            cycleId=cycle_id,
            laneId=laneId,
            scanId=scanId,
            passageStatus=PassageStatus.WAITING,
            closureStatus=ClosureStatus.OPENED,
            observationDeadline=current_time + timedelta(milliseconds=self.PASSAGE_OBSERVATION_WINDOW_MS),
        )
        open_command = GateCommand(
            commandId=command_id,
            cycleId=cycle_id,
            laneId=laneId,
            scanId=scanId,
            commandType=CommandType.OPEN,
            outcome=CommandOutcome.SENT,
            issuedAt=current_time,
        )

        self._uow.begin(timeout_ms=remaining_ms)
        try:
            committed, status_reason = self._scan_repo.atomic_reserve_lane_claim_token_and_accept(
                scan=accepted_attempt,
                reservation=reservation,
                cycle=cycle,
                command=open_command,
                timeout_ms=remaining_ms,
            )
            if committed:
                self._uow.commit()
            else:
                self._uow.rollback()
        except Exception:
            self._uow.rollback()
            committed, status_reason = False, "STORAGE_FAILURE"

        if not committed:
            if status_reason == "LANE_BUSY":
                return ScanResult(
                    scanId=scanId,
                    state=ScanState.LANE_BUSY,
                    laneId=laneId,
                    degradedFlag=degraded,
                    reason="LANE_HAS_ACTIVE_OR_UNRESOLVED_CYCLE",
                )
            if status_reason == "TOKEN_ALREADY_CLAIMED":
                replay_scan = ScanAttempt(
                    scanId=scanId,
                    factoryId=self._factory_id,
                    laneId=laneId,
                    state=ScanState.REJECTED,
                    createdAt=current_time,
                    tokenId=decision.claims.tokenId,
                    employeeId=decision.claims.employeeId,
                    degradedFlag=degraded,
                    snapshotVersion=decision.snapshotVersion,
                    reason="QR_TOKEN_REPLAY_DETECTED",
                )
                with self._uow:
                    self._scan_repo.save_scan(
                        replay_scan,
                        audit_payload={
                            "action": "QR_REPLAY_REJECTED",
                            "scanId": scanId,
                            "tokenId": decision.claims.tokenId,
                            "laneId": laneId,
                        },
                    )
                    self._uow.commit()
                return ScanResult(
                    scanId=scanId,
                    state=ScanState.REJECTED,
                    laneId=laneId,
                    degradedFlag=degraded,
                    reason="QR_TOKEN_REPLAY_DETECTED",
                )
            return ScanResult(
                scanId=scanId,
                state=ScanState.CANNOT_PROCESS,
                laneId=laneId,
                degradedFlag=degraded,
                reason="CANNOT_STORE_ACCEPTED_STATE",
            )

        # Step A5: Invoke controller openGate port outside DB transaction after ACCEPTED is durably stored
        outcome = self._open_gate_port.openGate(commandId=command_id, laneId=laneId, timeout_ms=min(700, remaining_ms))
        if outcome == CommandOutcome.UNDETERMINED and self._status_query_port is not None:
            outcome = self._status_query_port.queryStatus(commandId=command_id, laneId=laneId)

        # Step A6 & A7: Persist opening outcome and return kiosk result
        if outcome == CommandOutcome.OPEN_CONFIRMED:
            with self._uow:
                self._scan_repo.update_opening_outcome(
                    scanId=scanId,
                    cycleId=cycle_id,
                    commandId=command_id,
                    scanState=ScanState.OPENED.value,
                    commandOutcome=CommandOutcome.OPEN_CONFIRMED.value,
                    releaseLane=False,
                    outboxEvent={
                        "eventId": f"evt-{uuid.uuid4().hex[:12]}",
                        "eventType": "GATE_OPEN_CONFIRMED",
                        "scanId": scanId,
                        "commandId": command_id,
                        "laneId": laneId,
                        "degraded": degraded,
                    },
                    auditPayload={
                        "action": "GATE_OPEN_CONFIRMED",
                        "scanId": scanId,
                        "commandId": command_id,
                        "laneId": laneId,
                    },
                )
                self._uow.commit()
            self._audit_api.append_async({"action": "GATE_OPEN_CONFIRMED", "scanId": scanId})
            return ScanResult(
                scanId=scanId,
                state=ScanState.OPENED,
                laneId=laneId,
                degradedFlag=degraded,
                reason="OPEN_CONFIRMED",
                commandId=command_id,
            )

        if outcome == CommandOutcome.CONTROLLER_ERROR:
            # Explicit non-execution: release the lane
            with self._uow:
                self._scan_repo.update_opening_outcome(
                    scanId=scanId,
                    cycleId=cycle_id,
                    commandId=command_id,
                    scanState=ScanState.CONTROLLER_ERROR.value,
                    commandOutcome=CommandOutcome.CONTROLLER_ERROR.value,
                    releaseLane=True,
                    auditPayload={
                        "action": "CONTROLLER_NON_EXECUTION",
                        "scanId": scanId,
                        "commandId": command_id,
                    },
                )
                self._uow.commit()
            self._health_api.report_event_async({"alert": "CONTROLLER_ERROR", "laneId": laneId})
            return ScanResult(
                scanId=scanId,
                state=ScanState.CONTROLLER_ERROR,
                laneId=laneId,
                degradedFlag=degraded,
                reason="CONTROLLER_CONFIRMED_NON_EXECUTION",
                commandId=command_id,
            )

        # Timeout or RECEIPT_ONLY without OPEN_CONFIRMED -> UNDETERMINED (I-03: keep lane reserved, never resend openGate)
        # Table 4.23a (p. 70): "Inquire if possible and move to the matching state; otherwise PENDING RECONCILIATION."
        final_state = (
            ScanState.UNDETERMINED
            if self._status_query_port is not None
            else ScanState.PENDING_RECONCILIATION
        )
        with self._uow:
            self._scan_repo.update_opening_outcome(
                scanId=scanId,
                cycleId=cycle_id,
                commandId=command_id,
                scanState=final_state.value,
                commandOutcome=CommandOutcome.UNDETERMINED.value,
                releaseLane=False,
                auditPayload={
                    "action": "OPEN_OUTCOME_UNDETERMINED",
                    "scanId": scanId,
                    "commandId": command_id,
                },
            )
            self._uow.commit()
        self._health_api.report_event_async({"alert": "OPEN_UNDETERMINED", "laneId": laneId})
        return ScanResult(
            scanId=scanId,
            state=final_state,
            laneId=laneId,
            degradedFlag=degraded,
            reason="CONTROLLER_OUTCOME_UNDETERMINED",
            commandId=command_id,
        )

    def getScanStatus(self, scanId: str) -> ScanResult | None:
        """Return stored scan status for kiosk retry inquiries without downgrading OPENED (Table 4.19)."""
        scan = self._scan_repo.get_scan(scanId)
        if scan is None:
            return None
        return ScanResult(
            scanId=scan.scanId,
            state=scan.state,
            laneId=scan.laneId,
            degradedFlag=scan.degradedFlag,
            reason=scan.reason,
        )

    # --- IPassageEvents callbacks (ONLY onPassage and onLaneClear per §7.1.4) ---

    def onPassage(
        self,
        passageEventId: str,
        commandId: str,
        laneId: str,
        occurredAt: datetime | None = None,
    ) -> None:
        """Step A8: Record PassageEvent and create at most one original AttendanceTransaction per scanId (I-07, ADR-02).

        Handles late PASSAGE arriving after LANE_CLEAR/CLOSED without re-locking the closed lane.
        """
        event_time = occurredAt or datetime.now(UTC)
        cycle = self._scan_repo.get_cycle_by_command(commandId)
        if cycle is None or cycle.laneId != laneId:
            return

        passage = PassageEvent(
            passageEventId=passageEventId,
            commandId=commandId,
            cycleId=cycle.cycleId,
            laneId=laneId,
            occurredAt=event_time,
        )
        was_already_passed = cycle.passageStatus == PassageStatus.PASSED
        cycle.passageStatus = PassageStatus.PASSED

        attendance_tx: AttendanceTransaction | None = None
        if cycle.scanId is not None and not was_already_passed:
            scan = self._scan_repo.get_scan(cycle.scanId)
            lane = self._scan_repo.get_lane(laneId)
            if scan is not None and scan.employeeId is not None:
                attendance_tx = AttendanceTransaction(
                    transactionId=f"att-{uuid.uuid4().hex[:12]}",
                    scanId=scan.scanId,
                    passageEventId=passageEventId,
                    employeeId=scan.employeeId,
                    factoryId=scan.factoryId,
                    laneId=laneId,
                    direction=lane.direction if lane else Direction.ENTRY,
                    occurredAt=event_time,
                    source="QR_GATE",
                    degradedFlag=scan.degradedFlag,
                    snapshotVersion=scan.snapshotVersion,
                )

        with self._uow:
            stored_new_passage = self._attendance_repo.store_passage_and_attendance_atomic(
                passage=passage,
                transaction=attendance_tx,
                cycle=cycle,
                outboxEvent={
                    "eventId": f"evt-{uuid.uuid4().hex[:12]}",
                    "eventType": "PASSAGE_AND_ATTENDANCE_RECORDED"
                    if not was_already_passed
                    else "ANOMALY_SECOND_PASSAGE_FLAGGED",
                    "passageEventId": passageEventId,
                    "scanId": cycle.scanId,
                    "laneId": laneId,
                },
                auditPayload={
                    "action": "PASSAGE_RECORDED"
                    if not was_already_passed
                    else "ANOMALY_SECOND_PASSAGE_FLAGGED",
                    "passageEventId": passageEventId,
                    "scanId": cycle.scanId,
                    "laneId": laneId,
                },
            )
            if stored_new_passage and was_already_passed and cycle.scanId is not None:
                scan = self._scan_repo.get_scan(cycle.scanId)
                if scan is not None:
                    scan.reason = "FLAGGED_SECOND_PASSAGE_ANOMALY"
                    self._scan_repo.save_scan(scan)
            self._uow.commit()

        if stored_new_passage and was_already_passed and cycle.scanId is not None:
            self._health_api.report_event_async(
                {"alert": "SECOND_PASSAGE_ANOMALY", "scanId": cycle.scanId, "passageEventId": passageEventId}
            )

    def onLaneClear(
        self,
        commandId: str,
        laneId: str,
        occurredAt: datetime | None = None,
    ) -> None:
        """Step A9: When controller reports LANE_CLEAR, send closeGate once and release lane only on CLOSED (I-05)."""
        cycle = self._scan_repo.get_cycle_by_command(commandId)
        if cycle is None or cycle.laneId != laneId:
            return
        if cycle.closureStatus == ClosureStatus.EMERGENCY_HOLD:
            return

        cycle.laneClearReceived = True
        cycle.closureStatus = ClosureStatus.CLEAR_TO_CLOSE
        with self._uow:
            self._scan_repo.update_cycle(cycle, releaseLane=False)
            self._uow.commit()

        close_cmd_id = f"cmd-close-{uuid.uuid4().hex[:10]}"
        close_outcome = self._close_gate_port.closeGate(commandId=close_cmd_id, laneId=laneId)
        if close_outcome == CommandOutcome.UNDETERMINED and self._status_query_port is not None:
            close_outcome = self._status_query_port.queryStatus(commandId=close_cmd_id, laneId=laneId)

        with self._uow:
            if close_outcome == CommandOutcome.CLOSED:
                cycle.closureStatus = ClosureStatus.CLOSED
                self._scan_repo.update_cycle(cycle, releaseLane=True)
            elif close_outcome == CommandOutcome.CONTROLLER_ERROR:
                cycle.closureStatus = ClosureStatus.CLOSE_FAILED
                self._scan_repo.update_cycle(cycle, releaseLane=False)
            else:
                cycle.closureStatus = ClosureStatus.CLOSE_UNCERTAIN
                self._scan_repo.update_cycle(cycle, releaseLane=False)
            self._uow.commit()

        if close_outcome != CommandOutcome.CLOSED:
            self._health_api.report_event_async({"alert": cycle.closureStatus.value, "laneId": laneId})

    def handlePassageTimeout(self, cycleId: str) -> bool:
        """Flag missing passage after 15 seconds (15_000 ms) as NO_PASSAGE and PENDING_RECONCILIATION (§4.2.5).

        Never sends closeGate from the timer (I-05).
        """
        cycle = self._scan_repo.get_cycle(cycleId)
        if cycle is None or cycle.passageStatus != PassageStatus.WAITING:
            return False

        cycle.passageStatus = PassageStatus.NO_PASSAGE
        already_closed = cycle.closureStatus == ClosureStatus.CLOSED
        with self._uow:
            self._scan_repo.update_cycle(cycle, releaseLane=already_closed)
            if cycle.scanId:
                scan = self._scan_repo.get_scan(cycle.scanId)
                if scan and scan.state == ScanState.OPENED:
                    scan.state = ScanState.PENDING_RECONCILIATION
                    scan.reason = "MISSING_PASSAGE_AFTER_15S"
                    self._scan_repo.save_scan(scan)
            self._uow.commit()
        return True

    # --- Flow B: Assisted & Emergency operations called via GateCoreFacade ---

    def recordIntervention(self, case: SupportCase) -> bool:
        """Store an authorized support case, audit evidence, and an outbox event in Gate Core (Flow B4, p. 65)."""
        with self._uow:
            self._scan_repo.save_support_case(
                case,
                outboxEvent={
                    "eventId": f"evt-case-{case.supportCaseId}",
                    "eventType": "SUPPORT_CASE_RECORDED",
                    "supportCaseId": case.supportCaseId,
                    "factoryId": case.factoryId,
                    "laneId": case.laneId,
                    "employeeId": case.employeeId,
                    "isEmergency": case.isEmergency,
                },
                auditPayload={
                    "action": "SUPPORT_CASE_RECORDED",
                    "supportCaseId": case.supportCaseId,
                    "guardId": case.guardId,
                    "laneId": case.laneId,
                    "isEmergency": case.isEmergency,
                },
            )
            self._uow.commit()
        return True

    def requestAssistedOpen(self, supportCaseId: str, laneId: str, now: datetime | None = None) -> ScanResult:
        """Open a free lane for an authorized support case without creating automatic attendance (Flow B, ADR-07)."""
        current_time = now or datetime.now(UTC)
        case = self._scan_repo.get_support_case(supportCaseId)
        if case is None:
            return ScanResult(scanId=supportCaseId, state=ScanState.REJECTED, laneId=laneId, reason="SUPPORT_CASE_NOT_FOUND")

        # Repeated request returns existing command status without re-sending openGate (§4.2.3 p. 66)
        existing_cmd = self._scan_repo.get_command_by_support_case(supportCaseId, CommandType.OPEN.value)
        if existing_cmd is not None:
            if existing_cmd.outcome == CommandOutcome.UNDETERMINED and self._status_query_port is not None:
                existing_cmd.outcome = self._status_query_port.queryStatus(existing_cmd.commandId, existing_cmd.laneId)
            st = (
                ScanState.OPENED
                if existing_cmd.outcome == CommandOutcome.OPEN_CONFIRMED
                else (
                    ScanState.CONTROLLER_ERROR
                    if existing_cmd.outcome == CommandOutcome.CONTROLLER_ERROR
                    else (ScanState.UNDETERMINED if self._status_query_port is not None else ScanState.PENDING_RECONCILIATION)
                )
            )
            return ScanResult(
                scanId=supportCaseId,
                state=st,
                laneId=existing_cmd.laneId,
                commandId=existing_cmd.commandId,
                reason=existing_cmd.outcome.value,
            )

        if self._snapshot_repo is not None and case.employeeId is not None:
            snap = self._snapshot_repo.get_snapshot()
            if snap.getOperatingMode(current_time) == OperatingMode.CANNOT_PROCESS:
                return ScanResult(
                    scanId=supportCaseId,
                    state=ScanState.CANNOT_PROCESS,
                    laneId=laneId,
                    reason="ELIGIBILITY_CANNOT_BE_VERIFIED",
                )
            eligible, elig_reason = snap.isEmployeeEligible(case.employeeId, laneId)
            if not eligible:
                return ScanResult(
                    scanId=supportCaseId,
                    state=ScanState.REJECTED,
                    laneId=laneId,
                    reason=elig_reason,
                )

        active = self._scan_repo.get_active_cycle_for_lane(laneId)
        if active is not None:
            return ScanResult(scanId=supportCaseId, state=ScanState.LANE_BUSY, laneId=laneId, reason="LANE_BUSY")

        cycle_id = f"cyc-ast-{uuid.uuid4().hex[:10]}"
        cmd_id = f"cmd-ast-{uuid.uuid4().hex[:10]}"
        cycle = GateCycle(
            cycleId=cycle_id,
            laneId=laneId,
            supportCaseId=supportCaseId,
            passageStatus=PassageStatus.WAITING,
            closureStatus=ClosureStatus.OPENED,
            observationDeadline=current_time + timedelta(milliseconds=self.PASSAGE_OBSERVATION_WINDOW_MS),
        )
        command = GateCommand(
            commandId=cmd_id,
            cycleId=cycle_id,
            laneId=laneId,
            supportCaseId=supportCaseId,
            commandType=CommandType.OPEN,
            outcome=CommandOutcome.SENT,
            issuedAt=current_time,
        )
        with self._uow:
            self._scan_repo.update_cycle(cycle, releaseLane=False, command=command)
            self._uow.commit()

        outcome = self._open_gate_port.openGate(commandId=cmd_id, laneId=laneId, timeout_ms=self.ASSISTED_OPEN_TIMEOUT_MS)
        if outcome == CommandOutcome.UNDETERMINED and self._status_query_port is not None:
            outcome = self._status_query_port.queryStatus(commandId=cmd_id, laneId=laneId)

        command.outcome = outcome
        release_lane = outcome == CommandOutcome.CONTROLLER_ERROR
        with self._uow:
            self._scan_repo.update_cycle(cycle, releaseLane=release_lane, command=command)
            self._uow.commit()

        state = (
            ScanState.OPENED
            if outcome == CommandOutcome.OPEN_CONFIRMED
            else (
                ScanState.CONTROLLER_ERROR
                if outcome == CommandOutcome.CONTROLLER_ERROR
                else (ScanState.UNDETERMINED if self._status_query_port is not None else ScanState.PENDING_RECONCILIATION)
            )
        )
        return ScanResult(scanId=supportCaseId, state=state, laneId=laneId, commandId=cmd_id, reason=outcome.value)

    def requestEmergencyRelease(self, supportCaseId: str, laneId: str, now: datetime | None = None) -> ScanResult:
        """Place the lane in EMERGENCY_HOLD and invoke emergencyRelease (Flow B, Table 4.22)."""
        current_time = now or datetime.now(UTC)
        active = self._scan_repo.get_active_cycle_for_lane(laneId)

        cycle_id = active.cycleId if active else f"cyc-emg-{uuid.uuid4().hex[:10]}"
        cmd_id = f"cmd-emg-{uuid.uuid4().hex[:10]}"
        cycle = GateCycle(
            cycleId=cycle_id,
            laneId=laneId,
            supportCaseId=supportCaseId,
            passageStatus=PassageStatus.NO_PASSAGE,
            closureStatus=ClosureStatus.EMERGENCY_HOLD,
            observationDeadline=current_time,
        )
        command = GateCommand(
            commandId=cmd_id,
            cycleId=cycle_id,
            laneId=laneId,
            supportCaseId=supportCaseId,
            commandType=CommandType.EMERGENCY_RELEASE,
            outcome=CommandOutcome.SENT,
            issuedAt=current_time,
        )
        with self._uow:
            if active is not None and active.scanId and active.passageStatus == PassageStatus.WAITING:
                scan = self._scan_repo.get_scan(active.scanId)
                if scan and scan.state == ScanState.OPENED:
                    scan.state = ScanState.PENDING_RECONCILIATION
                    scan.reason = "EMERGENCY_RELEASE_DURING_OPEN_CYCLE"
                    self._scan_repo.save_scan(scan)
            self._scan_repo.update_cycle(cycle, releaseLane=False, command=command)
            self._uow.commit()

        outcome = self._emergency_gate_port.emergencyRelease(commandId=cmd_id, laneId=laneId)
        return ScanResult(
            scanId=supportCaseId,
            state=ScanState.OPENED if outcome == CommandOutcome.EMERGENCY_RELEASED else ScanState.UNDETERMINED,
            laneId=laneId,
            commandId=cmd_id,
            reason=ClosureStatus.EMERGENCY_HOLD.value,
        )

    def requestEmergencyReset(self, supportCaseId: str, laneId: str, now: datetime | None = None) -> ScanResult:
        """Request reset after emergency release; exits EMERGENCY_HOLD ONLY when RESET_CONFIRMED."""
        active = self._scan_repo.get_active_cycle_for_lane(laneId)
        if active is None or active.closureStatus != ClosureStatus.EMERGENCY_HOLD:
            return ScanResult(scanId=supportCaseId, state=ScanState.REJECTED, laneId=laneId, reason="LANE_NOT_IN_EMERGENCY_HOLD")

        cmd_id = f"cmd-rst-{uuid.uuid4().hex[:10]}"
        outcome = self._emergency_gate_port.resetEmergency(commandId=cmd_id, laneId=laneId)
        if outcome == CommandOutcome.RESET_CONFIRMED:
            active.closureStatus = ClosureStatus.OPENED
            with self._uow:
                self._scan_repo.update_cycle(active, releaseLane=False)
                self._uow.commit()
            return ScanResult(scanId=supportCaseId, state=ScanState.OPENED, laneId=laneId, commandId=cmd_id, reason="EMERGENCY_RESET_CONFIRMED")

        return ScanResult(
            scanId=supportCaseId,
            state=ScanState.UNDETERMINED,
            laneId=laneId,
            commandId=cmd_id,
            reason=ClosureStatus.EMERGENCY_HOLD.value,
        )
