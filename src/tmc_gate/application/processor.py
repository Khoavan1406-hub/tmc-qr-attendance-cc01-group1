"""[PACKAGE 3] GateTransactionProcessor component of M1 Gate Core (Figure 4.2, §4.2.2 Flow A, §4.2.5, §4.3, ADR-02).

Coordinates scan states, atomic lane reservation + token claim (via IUnitOfWork), controller commands,
optional status inquiry (IControllerStatusQuery), passage-based attendance creation, and safe closure.
"""

import time
import uuid
from collections.abc import Callable
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
    IPassageWindowQuery,
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


def _is_after(moment: datetime, deadline: datetime) -> bool:
    """Compare timestamps safely when one of them is naive (naive values are treated as UTC)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return moment > deadline


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
        uow: IUnitOfWork,
        status_query_port: IControllerStatusQuery | None = None,
        snapshot_repo: ISnapshotRepository | None = None,
        monotonic_ms: Callable[[], int] | None = None,
        passage_window_query: IPassageWindowQuery | None = None,
    ) -> None:
        self._factory_id = factory_id
        self._uow: IUnitOfWork = uow
        self._monotonic_ms: Callable[[], int] = monotonic_ms or (lambda: time.monotonic_ns() // 1_000_000)
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
        self._passage_window_query = passage_window_query
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

    def _budget_left_ms(self, budget_end_ms: int) -> int:
        """Time left in this scan's single budget, measured on the monotonic clock at the moment of the call.

        Every storage wait and the openGate timeout take what is left now, never the budget computed at entry.
        """
        return budget_end_ms - self._monotonic_ms()

    def _begin_within_budget(self, budget_end_ms: int, scanId: str, laneId: str) -> bool | None:
        """Begin a UnitOfWork with the time left in the scan budget.

        None when the budget is already spent (no wait is started), False when storage stays busy or is unavailable
        for the time left, True when the transaction began.
        """
        left_ms = self._budget_left_ms(budget_end_ms)
        if left_ms <= 0:
            return None
        return self._try_begin(left_ms, scanId, laneId)

    def _try_begin(self, timeout_ms: int, scanId: str, laneId: str) -> bool:
        """Begin a UnitOfWork within the scan budget. False when storage is busy or unavailable.

        On False nothing was started, so the caller must not roll back (on a shared connection that could discard
        another request's transaction).
        """
        try:
            self._uow.begin(timeout_ms=timeout_ms)
        except Exception:
            self._health_api.report_event_async({"alert": "STORAGE_BUSY_OR_UNAVAILABLE", "scanId": scanId, "laneId": laneId})
            return False
        return True

    def _store_scan_within_budget(
        self, scan: ScanAttempt, audit_payload: dict[str, object], budget_end_ms: int
    ) -> bool | None:
        """Persist a terminal scan (REJECTED / CANNOT_PROCESS) and its audit record.

        None when the scan budget is spent before the wait, False if storage is busy or fails, True when stored.
        """
        began = self._begin_within_budget(budget_end_ms, scan.scanId, scan.laneId)
        if not began:
            return began
        try:
            self._scan_repo.save_scan(scan, audit_payload=audit_payload)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            self._health_api.report_event_async(
                {"alert": "STORAGE_BUSY_OR_UNAVAILABLE", "scanId": scan.scanId, "laneId": scan.laneId}
            )
            return False
        return True

    @staticmethod
    def _budget_spent_result(scanId: str, laneId: str, degraded: bool = False) -> ScanResult:
        """The scan budget ran out before the next step could start; nothing new is claimed or sent."""
        return ScanResult(
            scanId=scanId,
            state=ScanState.CANNOT_PROCESS,
            laneId=laneId,
            degradedFlag=degraded,
            reason="REQUEST_DEADLINE_EXPIRED_BEFORE_COMMAND",
        )

    def _storage_unreachable_result(
        self, began: bool | None, scanId: str, laneId: str, degraded: bool = False
    ) -> ScanResult:
        if began is None:
            return self._budget_spent_result(scanId, laneId, degraded)
        return self._storage_busy_result(scanId, laneId, degraded)

    @staticmethod
    def _storage_busy_result(scanId: str, laneId: str, degraded: bool = False) -> ScanResult:
        return ScanResult(
            scanId=scanId,
            state=ScanState.CANNOT_PROCESS,
            laneId=laneId,
            degradedFlag=degraded,
            reason="STORAGE_BUSY_OR_UNAVAILABLE",
        )

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
        start_ms = self._monotonic_ms()
        eff_deadline = deadline or (current_time + timedelta(milliseconds=self.SCAN_DEADLINE_BUDGET_MS))

        # One budget per scan: a single monotonic end point shared by A2, A4, the rejection/replay audit and the
        # openGate timeout. Each step takes only the time left when it starts (§4.2.5, §5.4).
        budget_end_ms = start_ms + self._remaining_ms(current_time, eff_deadline)

        # Step A2: Duplicate scanId check (idempotent retry from kiosk; never downgrades stored OPENED).
        # The read runs in a UnitOfWork so that waiting for another request's open transaction is bounded by
        # the scan budget instead of blocking without limit; it never sees uncommitted rows.
        began = self._begin_within_budget(budget_end_ms, scanId, laneId)
        if not began:
            return self._storage_unreachable_result(began, scanId, laneId)
        try:
            existing = self._scan_repo.get_scan(scanId)
        finally:
            self._uow.rollback()
        if existing is not None:
            return ScanResult(
                scanId=existing.scanId,
                state=existing.state,
                laneId=existing.laneId,
                degradedFlag=existing.degradedFlag,
                reason=existing.reason,
            )

        # Timeout rule 1 (§4.2.5, §5.4): If request deadline has expired before sending command, return CANNOT_PROCESS
        if self._budget_left_ms(budget_end_ms) <= 0:
            return self._budget_spent_result(scanId, laneId)

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
            stored = self._store_scan_within_budget(
                failed_attempt,
                {
                    "action": "SCAN_EVALUATED",
                    "scanId": scanId,
                    "laneId": laneId,
                    "outcome": terminal_state.value,
                    "reason": decision.reason,
                },
                budget_end_ms,
            )
            if not stored:
                return self._storage_unreachable_result(stored, scanId, laneId, degraded)
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

        # Storage busy (another writer holds the database past the remaining budget) or unavailable:
        # nothing is claimed and no command is sent. No rollback: this request never began a transaction.
        # Budget already spent: nothing is claimed and no command is sent.
        began = self._begin_within_budget(budget_end_ms, scanId, laneId)
        if not began:
            return self._storage_unreachable_result(began, scanId, laneId, degraded)
        try:
            committed, status_reason = self._scan_repo.atomic_reserve_lane_claim_token_and_accept(
                scan=accepted_attempt,
                reservation=reservation,
                cycle=cycle,
                command=open_command,
                timeout_ms=max(0, self._budget_left_ms(budget_end_ms)),
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
                stored = self._store_scan_within_budget(
                    replay_scan,
                    {
                        "action": "QR_REPLAY_REJECTED",
                        "scanId": scanId,
                        "tokenId": decision.claims.tokenId,
                        "laneId": laneId,
                    },
                    budget_end_ms,
                )
                if not stored:
                    return self._storage_unreachable_result(stored, scanId, laneId, degraded)
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

        # Deadline re-check right before the command (Flow A only). ACCEPTED is stored and the token is claimed.
        remaining_ms = self._budget_left_ms(budget_end_ms)
        if remaining_ms <= 0:
            # The command was certainly not sent: release the lane, but the claimed token stays claimed (§7.3.4).
            with self._uow:
                applies, current_scan = self._open_result_applies(scanId, cycle_id)
                if applies:
                    self._scan_repo.update_opening_outcome(
                        scanId=scanId,
                        cycleId=cycle_id,
                        commandId=command_id,
                        scanState=ScanState.CANNOT_PROCESS.value,
                        commandOutcome=CommandOutcome.NOT_SENT.value,
                        releaseLane=True,
                        auditPayload={
                            "action": "OPEN_DEADLINE_EXPIRED_BEFORE_COMMAND",
                            "scanId": scanId,
                            "commandId": command_id,
                        },
                    )
                else:
                    self._record_open_command_only(command_id, CommandOutcome.NOT_SENT)
                self._uow.commit()
            if not applies:
                return self._superseded_open_result(current_scan, scanId, laneId, command_id, degraded)
            return ScanResult(
                scanId=scanId,
                state=ScanState.CANNOT_PROCESS,
                laneId=laneId,
                degradedFlag=degraded,
                reason="OPEN_DEADLINE_EXPIRED_BEFORE_COMMAND",
            )

        # Step A5: Invoke openGate outside the DB transaction with min(700 ms, remaining deadline)
        outcome = self._open_gate_port.openGate(
            commandId=command_id,
            laneId=laneId,
            timeout_ms=min(self.OPEN_COMMAND_BUDGET_MS, remaining_ms),
        )
        if outcome == CommandOutcome.UNDETERMINED:
            # Store UNDETERMINED first; keep lane and token; never resend openGate (I-03)
            applies, current_scan = self._store_open_outcome(
                scanId, cycle_id, command_id, ScanState.UNDETERMINED, CommandOutcome.UNDETERMINED
            )
            if not applies:
                return self._superseded_open_result(current_scan, scanId, laneId, command_id, degraded)
            outcome = self._query_or_undetermined(command_id, laneId)

        # Step A6 & A7: Persist opening outcome and return kiosk result
        if outcome == CommandOutcome.OPEN_CONFIRMED:
            confirmed_at = datetime.now(UTC)
            with self._uow:
                # Re-read inside the transaction: an emergency release that committed while openGate was running
                # has priority, so a late OPEN_CONFIRMED only updates the command history (no OPENED scan,
                # no passage window, no attendance from a later PASSAGE).
                applies, current_scan = self._open_result_applies(scanId, cycle_id)
                if not applies:
                    self._record_open_command_only(command_id, CommandOutcome.OPEN_CONFIRMED)
                    self._uow.commit()
                    return self._superseded_open_result(current_scan, scanId, laneId, command_id, degraded)
                # The 15-second passage window starts at OPEN_CONFIRMED, not at scan intake (Table 4.23b)
                opened_cycle = self._scan_repo.get_cycle(cycle_id)
                if opened_cycle is not None:
                    opened_cycle.observationDeadline = confirmed_at + timedelta(
                        milliseconds=self.PASSAGE_OBSERVATION_WINDOW_MS
                    )
                    self._scan_repo.update_cycle(opened_cycle, releaseLane=False)
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
            # Explicit non-execution: release the lane, unless an emergency release now holds it
            with self._uow:
                applies, current_scan = self._open_result_applies(scanId, cycle_id)
                if not applies:
                    self._record_open_command_only(command_id, CommandOutcome.CONTROLLER_ERROR)
                    self._uow.commit()
                    return self._superseded_open_result(current_scan, scanId, laneId, command_id, degraded)
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

        # Still undetermined after the optional query -> PENDING_RECONCILIATION (Table 4.23a)
        applies, current_scan = self._store_open_outcome(
            scanId, cycle_id, command_id, ScanState.PENDING_RECONCILIATION, CommandOutcome.UNDETERMINED
        )
        if not applies:
            return self._superseded_open_result(current_scan, scanId, laneId, command_id, degraded)
        self._health_api.report_event_async({"alert": "OPEN_UNDETERMINED", "laneId": laneId})
        return ScanResult(
            scanId=scanId,
            state=ScanState.PENDING_RECONCILIATION,
            laneId=laneId,
            degradedFlag=degraded,
            reason="CONTROLLER_OUTCOME_UNDETERMINED",
            commandId=command_id,
        )

    def _query_or_undetermined(self, commandId: str, laneId: str) -> CommandOutcome:
        """Ask the controller once when supported; otherwise the outcome stays UNDETERMINED."""
        if self._status_query_port is None:
            return CommandOutcome.UNDETERMINED
        return self._status_query_port.queryStatus(commandId=commandId, laneId=laneId)

    _AWAITING_OPEN_RESULT = (ScanState.ACCEPTED, ScanState.UNDETERMINED)

    def _open_result_applies(self, scanId: str, cycleId: str) -> tuple[bool, ScanAttempt | None]:
        """Inside the caller's transaction: may this openGate result still change the scan and the cycle?

        No when an emergency release took the lane (EMERGENCY_HOLD) or already moved the scan on while the
        command was running; the emergency has priority and the result is kept as command history only.
        """
        cycle = self._scan_repo.get_cycle(cycleId)
        scan = self._scan_repo.get_scan(scanId)
        applies = (
            cycle is not None
            and cycle.closureStatus == ClosureStatus.OPENED
            and scan is not None
            and scan.state in self._AWAITING_OPEN_RESULT
        )
        return applies, scan

    def _record_open_command_only(self, commandId: str, outcome: CommandOutcome) -> None:
        """Keep a superseded openGate result in GateCommand history without touching scan, cycle or lane."""
        command = self._scan_repo.get_command(commandId)
        if command is None:
            return
        if command.outcome in (CommandOutcome.OPEN_CONFIRMED, CommandOutcome.CONTROLLER_ERROR):
            return  # a confirmed result is never overwritten
        command.outcome = outcome
        self._scan_repo.save_command(command)

    def _superseded_open_result(
        self, scan: ScanAttempt | None, scanId: str, laneId: str, commandId: str, degraded: bool
    ) -> ScanResult:
        self._health_api.report_event_async(
            {"alert": "OPEN_RESULT_AFTER_EMERGENCY", "scanId": scanId, "commandId": commandId, "laneId": laneId}
        )
        return ScanResult(
            scanId=scanId,
            state=scan.state if scan is not None else ScanState.PENDING_RECONCILIATION,
            laneId=laneId,
            degradedFlag=degraded,
            reason=scan.reason if scan is not None else "EMERGENCY_RELEASE_DURING_OPEN_CYCLE",
            commandId=commandId,
        )

    def _store_open_outcome(
        self, scanId: str, cycleId: str, commandId: str, state: ScanState, outcome: CommandOutcome
    ) -> tuple[bool, ScanAttempt | None]:
        with self._uow:
            applies, current_scan = self._open_result_applies(scanId, cycleId)
            if applies:
                self._scan_repo.update_opening_outcome(
                    scanId=scanId,
                    cycleId=cycleId,
                    commandId=commandId,
                    scanState=state.value,
                    commandOutcome=outcome.value,
                    releaseLane=False,
                    auditPayload={"action": f"OPEN_{state.value}", "scanId": scanId, "commandId": commandId},
                )
            else:
                self._record_open_command_only(commandId, outcome)
            self._uow.commit()
        return applies, current_scan

    def _cycle_of_scan(self, scan: ScanAttempt) -> GateCycle | None:
        """The scan's own cycle, also after LANE_CLEAR/CLOSED released the lane (falls back to the lane lookup)."""
        if self._passage_window_query is not None:
            return self._passage_window_query.get_cycle_by_scan(scan.scanId)
        cycle = self._scan_repo.get_active_cycle_for_lane(scan.laneId)
        return cycle if cycle is not None and cycle.scanId == scan.scanId else None

    def getScanStatus(self, scanId: str) -> ScanResult | None:
        """Return stored scan status for kiosk retry inquiries without downgrading OPENED (Table 4.19).

        If the 15-second window of an OPENED scan has already passed, the timeout is applied first, so a read
        never reports OPENED after the deadline even when the worker has not swept the lane yet.
        """
        scan = self._scan_repo.get_scan(scanId)
        if scan is None:
            return None
        if scan.state == ScanState.OPENED:
            cycle = self._cycle_of_scan(scan)
            if (
                cycle is not None
                and cycle.passageStatus == PassageStatus.WAITING
                and self.handlePassageTimeout(cycle.cycleId)
            ):
                scan = self._scan_repo.get_scan(scanId) or scan
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

        Automatic attendance only while the scan is still OPENED and the cycle is WAITING (Table 4.23b).
        A PASSAGE after NO_PASSAGE, or after the scan moved to PENDING_RECONCILIATION (15-second timeout or
        emergency release), is stored once with audit evidence and flagged LATE_PASSAGE for review; it creates
        no attendance, does not restart the 15-second timer and never sends closeGate.
        A PASSAGE after LANE_CLEAR/CLOSED inside the window is still counted and never re-locks the lane.
        """
        event_time = occurredAt or datetime.now(UTC)
        received_at = datetime.now(UTC)
        found = self._scan_repo.get_cycle_by_command(commandId)
        if found is None or found.laneId != laneId:
            return

        with self._uow:
            # Re-read and decide inside the same transaction as the write, so a concurrent emergency release
            # or timeout that committed first is always seen (no decision from a stale OPENED read).
            cycle = self._scan_repo.get_cycle(found.cycleId) or found
            scan = self._scan_repo.get_scan(cycle.scanId) if cycle.scanId is not None else None
            was_already_passed = cycle.passageStatus == PassageStatus.PASSED
            # Received after the 15-second window although the timer callback has not run yet
            window_expired = cycle.passageStatus == PassageStatus.WAITING and _is_after(
                received_at, cycle.observationDeadline
            )
            is_late = not was_already_passed and (
                window_expired
                or cycle.closureStatus == ClosureStatus.EMERGENCY_HOLD
                or cycle.passageStatus == PassageStatus.NO_PASSAGE
                or (scan is not None and scan.state != ScanState.OPENED)
            )
            if window_expired:
                # Apply what handlePassageTimeout would have done; the timer still never sends closeGate
                cycle.passageStatus = PassageStatus.NO_PASSAGE
            elif not is_late:
                cycle.passageStatus = PassageStatus.PASSED

            passage = PassageEvent(
                passageEventId=passageEventId,
                commandId=commandId,
                cycleId=cycle.cycleId,
                laneId=laneId,
                occurredAt=event_time,
                reviewReason="LATE_PASSAGE" if is_late else None,
            )

            attendance_tx: AttendanceTransaction | None = None
            if scan is not None and not was_already_passed and not is_late and scan.employeeId is not None:
                lane = self._scan_repo.get_lane(laneId)
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

            if is_late:
                event_type = "LATE_PASSAGE_FLAGGED_FOR_REVIEW"
            elif was_already_passed:
                event_type = "ANOMALY_SECOND_PASSAGE_FLAGGED"
            else:
                event_type = "PASSAGE_AND_ATTENDANCE_RECORDED"

            stored_new_passage = self._attendance_repo.store_passage_and_attendance_atomic(
                passage=passage,
                transaction=attendance_tx,
                cycle=cycle,
                outboxEvent={
                    "eventId": f"evt-{uuid.uuid4().hex[:12]}",
                    "eventType": event_type,
                    "passageEventId": passageEventId,
                    "scanId": cycle.scanId,
                    "laneId": laneId,
                },
                auditPayload={
                    "action": "PASSAGE_RECORDED" if event_type == "PASSAGE_AND_ATTENDANCE_RECORDED" else event_type,
                    "passageEventId": passageEventId,
                    "scanId": cycle.scanId,
                    "laneId": laneId,
                },
            )
            if stored_new_passage and was_already_passed and scan is not None:
                scan.reason = "FLAGGED_SECOND_PASSAGE_ANOMALY"
                self._scan_repo.save_scan(scan)
            if window_expired and scan is not None and scan.state == ScanState.OPENED:
                scan.state = ScanState.PENDING_RECONCILIATION
                scan.reason = "MISSING_PASSAGE_AFTER_15S"
                self._scan_repo.save_scan(scan)
            self._uow.commit()

        if stored_new_passage and was_already_passed and cycle.scanId is not None:
            self._health_api.report_event_async(
                {"alert": "SECOND_PASSAGE_ANOMALY", "scanId": cycle.scanId, "passageEventId": passageEventId}
            )
        if stored_new_passage and is_late:
            self._health_api.report_event_async(
                {"alert": "LATE_PASSAGE_FOR_REVIEW", "scanId": cycle.scanId, "passageEventId": passageEventId}
            )

    def onLaneClear(
        self,
        commandId: str,
        laneId: str,
        occurredAt: datetime | None = None,
    ) -> None:
        """Step A9: on LANE_CLEAR send closeGate exactly once; release the lane only on CLOSED (I-05, Table 4.23b)."""
        cycle = self._scan_repo.get_cycle_by_command(commandId)
        if cycle is None or cycle.laneId != laneId:
            return
        if cycle.closureStatus in (ClosureStatus.EMERGENCY_HOLD, ClosureStatus.CLOSED):
            return
        if cycle.closureStatus == ClosureStatus.CLOSE_FAILED:
            # Explicit failure: no automatic resend; the guard follows the safety procedure
            self._health_api.report_event_async({"alert": "CLOSE_FAILED_REPEATED_LANE_CLEAR", "laneId": laneId})
            return
        close_cmd_id = f"cmd-close-{uuid.uuid4().hex[:10]}"
        existing_close_id: str | None = None
        claimed = False
        with self._uow:
            # Re-read inside the transaction so two concurrent LANE_CLEAR signals cannot both claim the close
            current = self._scan_repo.get_cycle(cycle.cycleId) or cycle
            if current.closureStatus == ClosureStatus.OPENED and current.closeCommandId is None:
                current.laneClearReceived = True
                current.closureStatus = ClosureStatus.CLOSE_SENT
                current.closeCommandId = close_cmd_id
                close_command = GateCommand(
                    commandId=close_cmd_id,
                    cycleId=current.cycleId,
                    laneId=laneId,
                    scanId=current.scanId,
                    supportCaseId=current.supportCaseId,
                    commandType=CommandType.CLOSE,
                    outcome=CommandOutcome.SENT,
                    issuedAt=occurredAt or datetime.now(UTC),
                )
                self._scan_repo.update_cycle(current, releaseLane=False, command=close_command)
                claimed = True
            elif current.closureStatus in (ClosureStatus.CLOSE_SENT, ClosureStatus.CLOSE_UNCERTAIN):
                existing_close_id = current.closeCommandId
            self._uow.commit()

        if not claimed:
            # Repeated LANE_CLEAR after CLOSE_SENT / CLOSE_UNCERTAIN: query the stored command, never create a new one
            if existing_close_id is not None and self._status_query_port is not None:
                outcome = self._status_query_port.queryStatus(commandId=existing_close_id, laneId=laneId)
                self._store_close_outcome(cycle.cycleId, existing_close_id, outcome)
            return

        # Call the controller only after CLOSE_SENT and closeCommandId are committed
        outcome = self._close_gate_port.closeGate(commandId=close_cmd_id, laneId=laneId)
        if outcome == CommandOutcome.UNDETERMINED and self._status_query_port is not None:
            outcome = self._status_query_port.queryStatus(commandId=close_cmd_id, laneId=laneId)
        self._store_close_outcome(cycle.cycleId, close_cmd_id, outcome)

    def _store_close_outcome(self, cycleId: str, closeCommandId: str, outcome: CommandOutcome) -> None:
        """Persist a close result on its GateCommand and, only if it still applies, on the GateCycle.

        - A confirmed result (CLOSED / CONTROLLER_ERROR) is never downgraded by a later UNDETERMINED reply.
        - A result that no longer applies (lane in EMERGENCY_HOLD, cycle already finished, or a newer close
          command replaced this one) is kept as command history only; the cycle and the lane reservation,
          which may now belong to a new scan, are left untouched.
        """
        alert: str | None = None
        lane_id: str | None = None
        with self._uow:
            current = self._scan_repo.get_cycle(cycleId)
            command = self._scan_repo.get_command(closeCommandId)
            if command is not None:
                confirmed = command.outcome in (CommandOutcome.CLOSED, CommandOutcome.CONTROLLER_ERROR)
                if confirmed and outcome == CommandOutcome.UNDETERMINED:
                    outcome = command.outcome  # keep the confirmed result
                command.outcome = outcome
            if current is not None:
                lane_id = current.laneId
                still_applies = (
                    current.closeCommandId == closeCommandId
                    and current.closureStatus in (ClosureStatus.CLOSE_SENT, ClosureStatus.CLOSE_UNCERTAIN)
                )
            else:
                still_applies = False
            if still_applies:
                if outcome == CommandOutcome.CLOSED:
                    current.closureStatus = ClosureStatus.CLOSED
                elif outcome == CommandOutcome.CONTROLLER_ERROR:
                    current.closureStatus = ClosureStatus.CLOSE_FAILED
                else:
                    current.closureStatus = ClosureStatus.CLOSE_UNCERTAIN
                    alert = current.closureStatus.value
                if outcome == CommandOutcome.CONTROLLER_ERROR:
                    alert = current.closureStatus.value
                # The lane is still reserved by this cycle (CLOSE_SENT / CLOSE_UNCERTAIN), so releasing it is safe
                self._scan_repo.update_cycle(
                    current, releaseLane=outcome == CommandOutcome.CLOSED, command=command
                )
            elif command is not None:
                self._scan_repo.save_command(command)
            self._uow.commit()
        if command is None:
            self._health_api.report_event_async({"alert": "CLOSE_COMMAND_NOT_FOUND", "commandId": closeCommandId})
        if alert is not None:
            self._health_api.report_event_async({"alert": alert, "laneId": lane_id})

    def handlePassageTimeout(self, cycleId: str, now: datetime | None = None) -> bool:
        """Flag missing passage after 15 seconds (15_000 ms) as NO_PASSAGE (§4.2.5, Table 4.23b).

        - Scan cycle: the OPENED scan moves to PENDING_RECONCILIATION (MISSING_PASSAGE_AFTER_15S).
        - Assisted cycle (supportCaseId, no scan): recorded for review (ASSISTED_MISSING_PASSAGE); no attendance.
        Deadline and state are re-checked inside the transaction, so a PASSAGE or an emergency that committed first
        wins and an early call does nothing. The lane reservation is never taken again: a cycle that LANE_CLEAR has
        already closed stays released. Never sends closeGate from the timer (I-05).
        """
        current_time = now or datetime.now(UTC)
        scan: ScanAttempt | None = None
        with self._uow:
            cycle = self._scan_repo.get_cycle(cycleId)
            if (
                cycle is None
                or cycle.passageStatus != PassageStatus.WAITING
                or not _is_after(current_time, cycle.observationDeadline)
            ):
                self._uow.rollback()
                return False
            if cycle.scanId is not None:
                scan = self._scan_repo.get_scan(cycle.scanId)
                if scan is None or scan.state != ScanState.OPENED:
                    # No passage window is running yet (open result pending) or the scan already moved on
                    self._uow.rollback()
                    return False
            elif cycle.supportCaseId is None or cycle.closureStatus == ClosureStatus.EMERGENCY_HOLD:
                self._uow.rollback()
                return False
            cycle.passageStatus = PassageStatus.NO_PASSAGE
            holds_lane = self._scan_repo.get_active_cycle_for_lane(cycle.laneId)
            still_reserved = holds_lane is not None and holds_lane.cycleId == cycle.cycleId
            self._scan_repo.update_cycle(
                cycle, releaseLane=not still_reserved or cycle.closureStatus == ClosureStatus.CLOSED
            )
            if scan is not None:
                scan.state = ScanState.PENDING_RECONCILIATION
                scan.reason = "MISSING_PASSAGE_AFTER_15S"
                self._scan_repo.save_scan(
                    scan,
                    audit_payload={"action": "MISSING_PASSAGE_AFTER_15S", "scanId": scan.scanId, "cycleId": cycleId},
                )
            self._uow.commit()
        if scan is not None:
            self._health_api.report_event_async(
                {"alert": "MISSING_PASSAGE_AFTER_15S", "scanId": scan.scanId, "laneId": cycle.laneId}
            )
        else:
            event = {"action": "ASSISTED_MISSING_PASSAGE", "supportCaseId": cycle.supportCaseId, "cycleId": cycleId}
            self._audit_api.append_async(event)
            self._health_api.report_event_async(
                {"alert": "ASSISTED_MISSING_PASSAGE_FOR_REVIEW", "supportCaseId": cycle.supportCaseId, "laneId": cycle.laneId}
            )
        return True

    def sweepPassageTimeouts(self, laneIds: list[str], now: datetime | None = None) -> int:
        """One pass of the passage-timeout worker: time out every cycle whose 15-second window has passed.

        With an IPassageWindowQuery the sweep finds cycles by their own state, so cycles whose lane LANE_CLEAR has
        already released are included; otherwise it falls back to the lane reservations of `laneIds`.
        handlePassageTimeout decides inside its own transaction. Returns how many cycles were moved to NO_PASSAGE.
        """
        current_time = now or datetime.now(UTC)
        if self._passage_window_query is not None:
            candidates = self._passage_window_query.list_cycles_awaiting_passage(current_time)
        else:
            candidates = [
                cycle
                for cycle in (self._scan_repo.get_active_cycle_for_lane(lane_id) for lane_id in laneIds)
                if cycle is not None
                and cycle.passageStatus == PassageStatus.WAITING
                and _is_after(current_time, cycle.observationDeadline)
            ]
        return sum(1 for cycle in candidates if self.handlePassageTimeout(cycle.cycleId, now=current_time))

    # --- Flow B: Assisted & Emergency operations called via GateCoreFacade ---

    def recordIntervention(self, case: SupportCase) -> bool:
        """Store an authorized support case, audit evidence, and an outbox event in Gate Core (Flow B4, p. 65).

        A supportCaseId is written once. Resending the same case is accepted without a new write; a different
        case under an existing supportCaseId returns False and keeps the original evidence.
        """
        with self._uow:
            existing = self._scan_repo.get_support_case(case.supportCaseId)
            if existing is not None:
                # Read-only path: close the transaction before returning (no write for a resend or a conflict).
                self._uow.rollback()
            else:
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
        if existing is not None and _same_support_case(existing, case):
            return True
        if existing is not None:
            self._audit_api.append_async({
                "action": "SUPPORT_CASE_ID_CONFLICT",
                "supportCaseId": case.supportCaseId,
                "guardId": case.guardId,
                "laneId": case.laneId,
            })
            return False
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
            return self._existing_assisted_open_result(supportCaseId, existing_cmd)

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
        # Check for an earlier command, reserve the lane and store the OPEN command in ONE transaction, so two
        # concurrent requests for the same support case can never both send openGate (one per supportCaseId).
        with self._uow:
            earlier = self._scan_repo.get_command_by_support_case(supportCaseId, CommandType.OPEN.value)
            if earlier is not None:
                self._uow.rollback()
                return self._existing_assisted_open_result(supportCaseId, earlier)
            if self._scan_repo.get_active_cycle_for_lane(laneId) is not None:
                self._uow.rollback()
                return ScanResult(scanId=supportCaseId, state=ScanState.LANE_BUSY, laneId=laneId, reason="LANE_BUSY")
            self._scan_repo.update_cycle(cycle, releaseLane=False, command=command)
            self._uow.commit()

        outcome = self._open_gate_port.openGate(commandId=cmd_id, laneId=laneId, timeout_ms=self.ASSISTED_OPEN_TIMEOUT_MS)
        if outcome == CommandOutcome.UNDETERMINED:
            command.outcome = CommandOutcome.UNDETERMINED
            with self._uow:
                if self._assisted_cycle_still_open(cycle_id):
                    self._scan_repo.update_cycle(cycle, releaseLane=False, command=command)
                else:
                    self._record_open_command_only(cmd_id, CommandOutcome.UNDETERMINED)
                self._uow.commit()
            outcome = self._query_or_undetermined(cmd_id, laneId)

        command.outcome = outcome
        release_lane = outcome == CommandOutcome.CONTROLLER_ERROR
        if outcome == CommandOutcome.OPEN_CONFIRMED:
            cycle.observationDeadline = datetime.now(UTC) + timedelta(milliseconds=self.PASSAGE_OBSERVATION_WINDOW_MS)
        with self._uow:
            # An emergency release that committed while openGate ran keeps EMERGENCY_HOLD; the result is history only
            superseded = not self._assisted_cycle_still_open(cycle_id)
            if superseded:
                self._record_open_command_only(cmd_id, outcome)
            else:
                self._scan_repo.update_cycle(cycle, releaseLane=release_lane, command=command)
            self._uow.commit()
        if superseded:
            self._health_api.report_event_async(
                {"alert": "OPEN_RESULT_AFTER_EMERGENCY", "supportCaseId": supportCaseId, "commandId": cmd_id}
            )
            return ScanResult(
                scanId=supportCaseId,
                state=ScanState.PENDING_RECONCILIATION,
                laneId=laneId,
                commandId=cmd_id,
                reason=ClosureStatus.EMERGENCY_HOLD.value,
            )

        state = (
            ScanState.OPENED
            if outcome == CommandOutcome.OPEN_CONFIRMED
            else (
                ScanState.CONTROLLER_ERROR
                if outcome == CommandOutcome.CONTROLLER_ERROR
                else ScanState.PENDING_RECONCILIATION
            )
        )
        return ScanResult(scanId=supportCaseId, state=state, laneId=laneId, commandId=cmd_id, reason=outcome.value)

    def _existing_assisted_open_result(self, supportCaseId: str, existing_cmd: GateCommand) -> ScanResult:
        """Status of the one OPEN command already stored for this support case; never sends openGate again."""
        if existing_cmd.outcome in (CommandOutcome.UNDETERMINED, CommandOutcome.SENT):
            existing_cmd.outcome = self._query_or_undetermined(existing_cmd.commandId, existing_cmd.laneId)
        st = (
            ScanState.OPENED
            if existing_cmd.outcome == CommandOutcome.OPEN_CONFIRMED
            else (
                ScanState.CONTROLLER_ERROR
                if existing_cmd.outcome == CommandOutcome.CONTROLLER_ERROR
                else ScanState.PENDING_RECONCILIATION
            )
        )
        return ScanResult(
            scanId=supportCaseId,
            state=st,
            laneId=existing_cmd.laneId,
            commandId=existing_cmd.commandId,
            reason=existing_cmd.outcome.value,
        )

    def _assisted_cycle_still_open(self, cycleId: str) -> bool:
        """True while no emergency has superseded this assisted opening (emergency marks it NO_PASSAGE, even after reset)."""
        current = self._scan_repo.get_cycle(cycleId)
        return (
            current is not None
            and current.closureStatus == ClosureStatus.OPENED
            and current.passageStatus == PassageStatus.WAITING
        )

    def requestEmergencyRelease(self, supportCaseId: str, laneId: str, now: datetime | None = None) -> ScanResult:
        """Place the lane in EMERGENCY_HOLD and invoke emergencyRelease (Flow B, Table 4.22).

        One emergencyRelease command per supportCaseId: a repeated request returns the stored command and,
        if its outcome is still unknown, asks the controller once instead of sending the command again.
        """
        current_time = now or datetime.now(UTC)
        cmd_id = f"cmd-emg-{uuid.uuid4().hex[:10]}"
        with self._uow:
            existing = self._scan_repo.get_command_by_support_case(supportCaseId, CommandType.EMERGENCY_RELEASE.value)
            if existing is not None:
                self._uow.rollback()
            else:
                active = self._scan_repo.get_active_cycle_for_lane(laneId)
                if active is not None:
                    # Keep scanId and closeCommandId of the running cycle; switch it to EMERGENCY_HOLD
                    cycle = active
                    cycle.closureStatus = ClosureStatus.EMERGENCY_HOLD
                    cycle.supportCaseId = cycle.supportCaseId or supportCaseId
                    cycle.emergencySupportCaseId = supportCaseId  # the newest emergency owns the hold
                    if active.scanId and active.passageStatus == PassageStatus.WAITING:
                        scan = self._scan_repo.get_scan(active.scanId)
                        # OPENED, or still waiting for its openGate result: an emergency always wins, so no
                        # later OPEN_CONFIRMED / PASSAGE can turn this scan into attendance
                        if scan and scan.state in (ScanState.OPENED, *self._AWAITING_OPEN_RESULT):
                            scan.state = ScanState.PENDING_RECONCILIATION
                            scan.reason = "EMERGENCY_RELEASE_DURING_OPEN_CYCLE"
                            self._scan_repo.save_scan(scan)
                    if cycle.passageStatus == PassageStatus.WAITING:
                        # The emergency supersedes the running opening for good: passage can no longer be
                        # attributed, and this mark survives a later reset, so a late openGate result for the
                        # cycle stays history only and never releases the lane or restarts the window.
                        cycle.passageStatus = PassageStatus.NO_PASSAGE
                else:
                    cycle = GateCycle(
                        cycleId=f"cyc-emg-{uuid.uuid4().hex[:10]}",
                        laneId=laneId,
                        supportCaseId=supportCaseId,
                        passageStatus=PassageStatus.NO_PASSAGE,
                        closureStatus=ClosureStatus.EMERGENCY_HOLD,
                        observationDeadline=current_time,
                        emergencySupportCaseId=supportCaseId,
                    )
                command = GateCommand(
                    commandId=cmd_id,
                    cycleId=cycle.cycleId,
                    laneId=laneId,
                    supportCaseId=supportCaseId,
                    commandType=CommandType.EMERGENCY_RELEASE,
                    outcome=CommandOutcome.SENT,
                    issuedAt=current_time,
                )
                self._scan_repo.update_cycle(cycle, releaseLane=False, command=command)
                self._uow.commit()

        if existing is not None:
            outcome = existing.outcome
            if outcome not in (CommandOutcome.EMERGENCY_RELEASED, CommandOutcome.CONTROLLER_ERROR):
                outcome = self._store_emergency_outcome(
                    existing.commandId, self._query_or_undetermined(existing.commandId, existing.laneId)
                )
            return self._emergency_release_result(supportCaseId, existing.laneId, existing.commandId, outcome)

        outcome = self._emergency_gate_port.emergencyRelease(commandId=cmd_id, laneId=laneId)
        outcome = self._store_emergency_outcome(cmd_id, outcome)
        return self._emergency_release_result(supportCaseId, laneId, cmd_id, outcome)

    def _store_emergency_outcome(self, commandId: str, outcome: CommandOutcome) -> CommandOutcome:
        """Save the emergencyRelease outcome on its GateCommand; a confirmed result is never downgraded."""
        with self._uow:
            command = self._scan_repo.get_command(commandId)
            if command is not None:
                if command.outcome in (CommandOutcome.EMERGENCY_RELEASED, CommandOutcome.CONTROLLER_ERROR):
                    outcome = command.outcome
                else:
                    command.outcome = outcome
                    self._scan_repo.save_command(command)
            self._uow.commit()
        if outcome != CommandOutcome.EMERGENCY_RELEASED:
            self._health_api.report_event_async(
                {"alert": "EMERGENCY_RELEASE_NOT_CONFIRMED", "commandId": commandId, "outcome": outcome.value}
            )
        return outcome

    @staticmethod
    def _emergency_release_result(
        supportCaseId: str, laneId: str, commandId: str, outcome: CommandOutcome
    ) -> ScanResult:
        return ScanResult(
            scanId=supportCaseId,
            state=ScanState.OPENED if outcome == CommandOutcome.EMERGENCY_RELEASED else ScanState.UNDETERMINED,
            laneId=laneId,
            commandId=commandId,
            reason=ClosureStatus.EMERGENCY_HOLD.value,
        )

    _RESET_FINAL = (CommandOutcome.RESET_CONFIRMED, CommandOutcome.CONTROLLER_ERROR)

    @staticmethod
    def _holds_emergency(cycle: GateCycle | None, supportCaseId: str) -> bool:
        """True while this cycle is in EMERGENCY_HOLD for exactly this emergency (the latest release)."""
        if cycle is None or cycle.closureStatus != ClosureStatus.EMERGENCY_HOLD:
            return False
        # Older SQLite rows get this owner from their latest EMERGENCY_RELEASE command at startup (migration)
        return cycle.emergencySupportCaseId == supportCaseId

    def requestEmergencyReset(self, supportCaseId: str, laneId: str, now: datetime | None = None) -> ScanResult:
        """Request reset after emergency release; exits EMERGENCY_HOLD ONLY when RESET_CONFIRMED.

        - The reset must belong to the emergency that currently holds the lane (same supportCaseId). A reset
          for an older emergency never clears a newer one (EMERGENCY_SUPERSEDED).
        - The RESET GateCommand is stored before resetEmergency is sent. A repeated request reuses that
          command: a SENT/UNDETERMINED outcome is queried once, never sent again with a new commandId.
          Only a controller-confirmed CONTROLLER_ERROR (not executed) allows a new reset command.
        - The result is applied in a new transaction that re-reads the cycle.
        """
        current_time = now or datetime.now(UTC)
        cmd_id = f"cmd-rst-{uuid.uuid4().hex[:10]}"
        with self._uow:
            existing = self._scan_repo.get_command_by_support_case(supportCaseId, CommandType.RESET.value)
            if existing is not None and existing.outcome != CommandOutcome.CONTROLLER_ERROR:
                self._uow.rollback()
            else:
                existing = None
                active = self._scan_repo.get_active_cycle_for_lane(laneId)
                if active is None or active.closureStatus != ClosureStatus.EMERGENCY_HOLD:
                    self._uow.rollback()
                    return ScanResult(
                        scanId=supportCaseId, state=ScanState.REJECTED, laneId=laneId, reason="LANE_NOT_IN_EMERGENCY_HOLD"
                    )
                if not self._holds_emergency(active, supportCaseId):
                    self._uow.rollback()
                    return ScanResult(
                        scanId=supportCaseId, state=ScanState.REJECTED, laneId=laneId, reason="EMERGENCY_SUPERSEDED"
                    )
                self._scan_repo.save_command(
                    GateCommand(
                        commandId=cmd_id,
                        cycleId=active.cycleId,
                        laneId=laneId,
                        supportCaseId=supportCaseId,
                        commandType=CommandType.RESET,
                        outcome=CommandOutcome.SENT,
                        issuedAt=current_time,
                    )
                )
                self._uow.commit()

        if existing is not None:
            outcome = existing.outcome
            if outcome not in self._RESET_FINAL:
                outcome = self._query_or_undetermined(existing.commandId, existing.laneId)
            return self._apply_reset_outcome(supportCaseId, existing.laneId, existing.commandId, outcome)

        outcome = self._emergency_gate_port.resetEmergency(commandId=cmd_id, laneId=laneId)
        return self._apply_reset_outcome(supportCaseId, laneId, cmd_id, outcome)

    def _apply_reset_outcome(
        self, supportCaseId: str, laneId: str, commandId: str, outcome: CommandOutcome
    ) -> ScanResult:
        """Store the reset outcome and leave EMERGENCY_HOLD only if this emergency still holds the lane."""
        superseded = False
        with self._uow:
            command = self._scan_repo.get_command(commandId)
            if command is not None:
                if command.outcome in self._RESET_FINAL:
                    outcome = command.outcome  # a final result is never downgraded
                elif outcome != CommandOutcome.SENT:
                    command.outcome = outcome
                    self._scan_repo.save_command(command)
            if outcome == CommandOutcome.RESET_CONFIRMED:
                cycle = self._scan_repo.get_cycle(command.cycleId) if command is not None else None
                if self._holds_emergency(cycle, supportCaseId):
                    cycle.closureStatus = ClosureStatus.OPENED
                    # The pre-emergency close command stays in GateCommand history but no longer governs this
                    # cycle; the next LANE_CLEAR must send and confirm a new closeGate.
                    cycle.closeCommandId = None
                    cycle.laneClearReceived = False
                    self._scan_repo.update_cycle(cycle, releaseLane=False)
                elif cycle is not None and cycle.closureStatus == ClosureStatus.EMERGENCY_HOLD:
                    superseded = True  # a newer emergency took the lane after this reset was sent
            self._uow.commit()

        if outcome == CommandOutcome.RESET_CONFIRMED and not superseded:
            return ScanResult(
                scanId=supportCaseId, state=ScanState.OPENED, laneId=laneId, commandId=commandId,
                reason="EMERGENCY_RESET_CONFIRMED",
            )
        if superseded:
            self._health_api.report_event_async(
                {"alert": "EMERGENCY_RESET_SUPERSEDED", "commandId": commandId, "laneId": laneId}
            )
            return ScanResult(
                scanId=supportCaseId, state=ScanState.UNDETERMINED, laneId=laneId, commandId=commandId,
                reason="EMERGENCY_SUPERSEDED",
            )
        if outcome != CommandOutcome.CONTROLLER_ERROR:
            self._health_api.report_event_async(
                {"alert": "EMERGENCY_RESET_NOT_CONFIRMED", "commandId": commandId, "outcome": outcome.value}
            )
        return ScanResult(
            scanId=supportCaseId,
            state=ScanState.UNDETERMINED,
            laneId=laneId,
            commandId=commandId,
            reason=ClosureStatus.EMERGENCY_HOLD.value,
        )

def _same_support_case(stored: SupportCase, incoming: SupportCase) -> bool:
    """A resend carries the same evidence; occurredAt may differ because the client stamps it again."""
    keys = ("factoryId", "laneId", "guardId", "reason", "employeeId", "linkedScanId", "isEmergency")
    return all(getattr(stored, key) == getattr(incoming, key) for key in keys)
