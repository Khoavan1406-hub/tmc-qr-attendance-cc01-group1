"""[PACKAGE 3] GateTransactionProcessor component of M1 Gate Core (Figure 4.2, §4.2.2 Flow A, §4.2.5, §4.3, ADR-02).

Coordinates scan states, atomic lane reservation + token claim, controller commands,
passage-based attendance creation, and safe closure.
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
)
from tmc_gate.ports.turnstile import ICloseGate, IEmergencyGate, IOpenGate, IPassageEvents


class GateTransactionProcessor(IPassageEvents):
    """Core coordinator for the factory gate cycle (§4.1.6, §4.2.2 Flow A, §4.3)."""

    PASSAGE_OBSERVATION_WINDOW_SEC: int = 15
    SCAN_DEADLINE_BUDGET_SEC: float = 2.0

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
    ) -> None:
        self._factory_id = factory_id
        self._access_decision = access_decision
        self._scan_repo = scan_repo
        self._attendance_repo = attendance_repo
        self._open_gate_port = open_gate_port
        self._close_gate_port = close_gate_port
        self._emergency_gate_port = emergency_gate_port
        self._audit_api = audit_api
        self._health_api = health_api

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
        request_deadline = deadline or (current_time + timedelta(seconds=self.SCAN_DEADLINE_BUDGET_SEC))

        # Step A2: Duplicate scanId check (idempotent retry from kiosk)
        existing = self._scan_repo.get_scan(scanId)
        if existing is not None:
            return ScanResult(
                scanId=existing.scanId,
                state=existing.state,
                laneId=existing.laneId,
                degradedFlag=existing.degradedFlag,
                reason=existing.reason,
            )

        # Check if request deadline has already expired before starting work (§4.2.5 Timeouts, §5.4 Deadlines)
        if datetime.now(UTC) > request_deadline and deadline is not None:
            return ScanResult(
                scanId=scanId,
                state=ScanState.CANNOT_PROCESS,
                laneId=laneId,
                reason="REQUEST_DEADLINE_EXPIRED_BEFORE_COMMAND",
            )

        # Step A3: Evaluate QR token and eligibility against local snapshot
        decision = self._access_decision.evaluate(qrToken=qrToken, laneId=laneId, now=current_time)
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
            return ScanResult(
                scanId=scanId,
                state=terminal_state,
                laneId=laneId,
                degradedFlag=degraded,
                reason=decision.reason,
            )

        # Step A4: Reserve the lane, claim tokenId, and store ACCEPTED atomically (ADR-02, ADR-11)
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
            observationDeadline=current_time + timedelta(seconds=self.PASSAGE_OBSERVATION_WINDOW_SEC),
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

        committed, status_reason = self._scan_repo.atomic_reserve_lane_claim_token_and_accept(
            scan=accepted_attempt,
            reservation=reservation,
            cycle=cycle,
            command=open_command,
        )
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
                self._scan_repo.save_scan(
                    replay_scan,
                    audit_payload={
                        "action": "QR_REPLAY_REJECTED",
                        "scanId": scanId,
                        "tokenId": decision.claims.tokenId,
                        "laneId": laneId,
                    },
                )
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

        # Step A5: Invoke controller openGate port after ACCEPTED is durably stored
        outcome = self._open_gate_port.openGate(commandId=command_id, laneId=laneId)

        # Step A6 & A7: Persist opening outcome and return kiosk result
        if outcome == CommandOutcome.OPEN_CONFIRMED:
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
            self._health_api.report_event_async({"alert": "CONTROLLER_ERROR", "laneId": laneId})
            return ScanResult(
                scanId=scanId,
                state=ScanState.CONTROLLER_ERROR,
                laneId=laneId,
                degradedFlag=degraded,
                reason="CONTROLLER_CONFIRMED_NON_EXECUTION",
                commandId=command_id,
            )

        # Timeout or RECEIPT_ONLY without OPEN_CONFIRMED -> UNDETERMINED (I-03: never resend openGate)
        self._scan_repo.update_opening_outcome(
            scanId=scanId,
            cycleId=cycle_id,
            commandId=command_id,
            scanState=ScanState.UNDETERMINED.value,
            commandOutcome=CommandOutcome.UNDETERMINED.value,
            releaseLane=False,
            auditPayload={
                "action": "OPEN_OUTCOME_UNDETERMINED",
                "scanId": scanId,
                "commandId": command_id,
            },
        )
        self._health_api.report_event_async({"alert": "OPEN_UNDETERMINED", "laneId": laneId})
        return ScanResult(
            scanId=scanId,
            state=ScanState.UNDETERMINED,
            laneId=laneId,
            degradedFlag=degraded,
            reason="CONTROLLER_OUTCOME_UNDETERMINED",
            commandId=command_id,
        )

    def getScanStatus(self, scanId: str) -> ScanResult | None:
        """Return stored scan status for kiosk retry inquiries (Table 4.19)."""
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

    # --- IPassageEvents callbacks (Step A8 & A9 of Flow A) ---

    def onOpenConfirmed(self, commandId: str, laneId: str, occurredAt: datetime | None = None) -> None:
        """Handle asynchronous OPEN_CONFIRMED signal if not already stored."""
        cycle = self._scan_repo.get_cycle_by_command(commandId)
        if cycle is None or cycle.scanId is None:
            return
        self._scan_repo.update_opening_outcome(
            scanId=cycle.scanId,
            cycleId=cycle.cycleId,
            commandId=commandId,
            scanState=ScanState.OPENED.value,
            commandOutcome=CommandOutcome.OPEN_CONFIRMED.value,
            releaseLane=False,
        )

    def onPassage(
        self,
        passageEventId: str,
        commandId: str,
        laneId: str,
        occurredAt: datetime | None = None,
    ) -> None:
        """Step A8: Record PassageEvent and create at most one original AttendanceTransaction per scanId (I-07, ADR-02)."""
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
                )

        stored_new_passage = self._attendance_repo.store_passage_and_attendance_atomic(
            passage=passage,
            transaction=attendance_tx,
            cycle=cycle,
            outboxEvent={
                "eventId": f"evt-{uuid.uuid4().hex[:12]}",
                "eventType": "PASSAGE_AND_ATTENDANCE_RECORDED" if not was_already_passed else "ANOMALY_SECOND_PASSAGE_FLAGGED",
                "passageEventId": passageEventId,
                "scanId": cycle.scanId,
                "laneId": laneId,
            },
            auditPayload={
                "action": "PASSAGE_RECORDED" if not was_already_passed else "ANOMALY_SECOND_PASSAGE_FLAGGED",
                "passageEventId": passageEventId,
                "scanId": cycle.scanId,
                "laneId": laneId,
            },
        )
        # Flow A8 & §4.2.5: A repeated passageEventId is ignored; a second, different passage for the same scan is flagged for review
        if stored_new_passage and was_already_passed and cycle.scanId is not None:
            scan = self._scan_repo.get_scan(cycle.scanId)
            if scan is not None:
                scan.reason = "FLAGGED_SECOND_PASSAGE_ANOMALY"
                self._scan_repo.save_scan(scan)
            self._health_api.report_event_async(
                {"alert": "SECOND_PASSAGE_ANOMALY", "scanId": cycle.scanId, "passageEventId": passageEventId}
            )

    def onLaneClear(self, commandId: str, laneId: str, occurredAt: datetime | None = None) -> None:
        """Step A9: When controller reports LANE_CLEAR, send closeGate once (I-05)."""
        cycle = self._scan_repo.get_cycle_by_command(commandId)
        if cycle is None or cycle.laneId != laneId:
            return
        if cycle.closureStatus == ClosureStatus.EMERGENCY_HOLD:
            return

        cycle.laneClearReceived = True
        cycle.closureStatus = ClosureStatus.CLEAR_TO_CLOSE
        self._scan_repo.update_cycle(cycle, releaseLane=False)

        close_cmd_id = f"cmd-close-{uuid.uuid4().hex[:10]}"
        close_outcome = self._close_gate_port.closeGate(commandId=close_cmd_id, laneId=laneId)
        if close_outcome == CommandOutcome.CLOSED:
            cycle.closureStatus = ClosureStatus.CLOSED
            self._scan_repo.update_cycle(cycle, releaseLane=True)
        elif close_outcome == CommandOutcome.CONTROLLER_ERROR:
            cycle.closureStatus = ClosureStatus.CLOSE_FAILED
            self._scan_repo.update_cycle(cycle, releaseLane=False)
            self._health_api.report_event_async({"alert": "CLOSE_FAILED", "laneId": laneId})
        else:
            cycle.closureStatus = ClosureStatus.CLOSE_UNCERTAIN
            self._scan_repo.update_cycle(cycle, releaseLane=False)
            self._health_api.report_event_async({"alert": "CLOSE_UNCERTAIN", "laneId": laneId})

    def onClosed(self, commandId: str, laneId: str, occurredAt: datetime | None = None) -> None:
        """Confirm CLOSED state and release the lane."""
        cycle = self._scan_repo.get_cycle_by_command(commandId)
        if cycle is None or cycle.laneId != laneId:
            return
        cycle.closureStatus = ClosureStatus.CLOSED
        self._scan_repo.update_cycle(cycle, releaseLane=True)

    def handlePassageTimeout(self, cycleId: str) -> bool:
        """Flag missing passage after 15 seconds as NO_PASSAGE and PENDING_RECONCILIATION (§4.2.5).

        Never sends closeGate from the timer (I-05).
        """
        cycle = self._scan_repo.get_cycle(cycleId)
        if cycle is None or cycle.passageStatus != PassageStatus.WAITING:
            return False

        cycle.passageStatus = PassageStatus.NO_PASSAGE
        self._scan_repo.update_cycle(cycle, releaseLane=False)
        if cycle.scanId:
            scan = self._scan_repo.get_scan(cycle.scanId)
            if scan and scan.state == ScanState.OPENED:
                scan.state = ScanState.PENDING_RECONCILIATION
                scan.reason = "MISSING_PASSAGE_AFTER_15S"
                self._scan_repo.save_scan(scan)
        return True

    # --- Flow B: Assisted & Emergency operations called via GateCoreFacade ---

    def recordIntervention(self, case: SupportCase) -> bool:
        """Store an authorized support case in Gate Core (Flow B4)."""
        self._scan_repo.save_support_case(case)
        return True

    def requestAssistedOpen(self, supportCaseId: str, laneId: str, now: datetime | None = None) -> ScanResult:
        """Open a free lane for an authorized support case without creating automatic attendance (ADR-07)."""
        current_time = now or datetime.now(UTC)
        case = self._scan_repo.get_support_case(supportCaseId)
        if case is None:
            return ScanResult(scanId=supportCaseId, state=ScanState.REJECTED, laneId=laneId, reason="SUPPORT_CASE_NOT_FOUND")

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
            observationDeadline=current_time + timedelta(seconds=self.PASSAGE_OBSERVATION_WINDOW_SEC),
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
        self._scan_repo.update_cycle(cycle, releaseLane=False, command=command)
        outcome = self._open_gate_port.openGate(commandId=cmd_id, laneId=laneId)
        state = ScanState.OPENED if outcome == CommandOutcome.OPEN_CONFIRMED else ScanState.UNDETERMINED
        return ScanResult(scanId=supportCaseId, state=state, laneId=laneId, commandId=cmd_id, reason=outcome.value)

    def requestEmergencyRelease(self, supportCaseId: str, laneId: str, now: datetime | None = None) -> ScanResult:
        """Place the lane in EMERGENCY_HOLD and invoke emergencyRelease (Flow B, Table 4.22)."""
        current_time = now or datetime.now(UTC)
        active = self._scan_repo.get_active_cycle_for_lane(laneId)
        if active is not None and active.scanId and active.passageStatus == PassageStatus.WAITING:
            scan = self._scan_repo.get_scan(active.scanId)
            if scan and scan.state == ScanState.OPENED:
                scan.state = ScanState.PENDING_RECONCILIATION
                scan.reason = "EMERGENCY_RELEASE_DURING_OPEN_CYCLE"
                self._scan_repo.save_scan(scan)

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
        self._scan_repo.update_cycle(cycle, releaseLane=False, command=command)
        outcome = self._emergency_gate_port.emergencyRelease(commandId=cmd_id, laneId=laneId)
        return ScanResult(
            scanId=supportCaseId,
            state=ScanState.OPENED if outcome == CommandOutcome.EMERGENCY_RELEASED else ScanState.UNDETERMINED,
            laneId=laneId,
            commandId=cmd_id,
            reason=ClosureStatus.EMERGENCY_HOLD.value,
        )

    def resetEmergency(self, supportCaseId: str, laneId: str, now: datetime | None = None) -> ScanResult:
        """Confirm reset after emergency release so normal closing can proceed."""
        active = self._scan_repo.get_active_cycle_for_lane(laneId)
        if active is None or active.closureStatus != ClosureStatus.EMERGENCY_HOLD:
            return ScanResult(scanId=supportCaseId, state=ScanState.REJECTED, laneId=laneId, reason="LANE_NOT_IN_EMERGENCY_HOLD")

        cmd_id = f"cmd-rst-{uuid.uuid4().hex[:10]}"
        outcome = self._emergency_gate_port.resetEmergency(commandId=cmd_id, laneId=laneId)
        if outcome == CommandOutcome.RESET_CONFIRMED:
            active.closureStatus = ClosureStatus.OPENED
            self._scan_repo.update_cycle(active, releaseLane=False)
            return ScanResult(scanId=supportCaseId, state=ScanState.OPENED, laneId=laneId, commandId=cmd_id, reason="EMERGENCY_RESET_CONFIRMED")
        return ScanResult(scanId=supportCaseId, state=ScanState.UNDETERMINED, laneId=laneId, commandId=cmd_id, reason=outcome.value)
