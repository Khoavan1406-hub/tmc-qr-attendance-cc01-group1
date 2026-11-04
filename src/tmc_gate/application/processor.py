"""[PACKAGE 3] GateTransactionProcessor component of M1 Gate Core (Figure 4.2, §4.2.2 Flow A, §4.2.5, §4.3, ADR-02).

Coordinates scan states, atomic lane reservation + token claim, controller commands,
passage-based attendance creation, and safe closure.
"""

from datetime import datetime

from tmc_gate.application.access_decision import AccessDecision
from tmc_gate.domain.models import ScanResult, SupportCase
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
    ) -> ScanResult:
        """Execute steps A1–A7 of Flow A (§4.2.2).

        TODO [Package 3]:
        - Step A2: Check `self._scan_repo.get_scan(scanId)`. If already stored, return the stored result (idempotent retry).
        - Step A3: Call `self._access_decision.evaluate(qrToken, laneId, now)`. If not allowed, persist `REJECTED` or `CANNOT_PROCESS` and return.
        - Step A4: Call `self._scan_repo.atomic_reserve_lane_claim_token_and_accept(...)`.
          * If `"LANE_BUSY"` -> return `ScanState.LANE_BUSY` (do NOT claim tokenId).
          * If `"TOKEN_ALREADY_CLAIMED"` -> save and return `ScanState.REJECTED` with reason `"QR_TOKEN_REPLAY_DETECTED"`.
          * If `"STORAGE_FAILURE"` -> return `ScanState.CANNOT_PROCESS`.
        - Step A5: Call `self._open_gate_port.openGate(commandId, laneId)`.
        - Step A6 & A7: Update opening outcome (`OPENED`, `CONTROLLER_ERROR` releasing lane, or `UNDETERMINED` keeping lane reserved without resending) and return `ScanResult`.
        """
        raise NotImplementedError("TODO [Package 3]: Implement GateTransactionProcessor.processScan")

    def getScanStatus(self, scanId: str) -> ScanResult | None:
        """Return stored scan status for kiosk retry inquiries (Table 4.19)."""
        raise NotImplementedError("TODO [Package 3]: Implement GateTransactionProcessor.getScanStatus")

    def onOpenConfirmed(self, commandId: str, laneId: str, occurredAt: datetime | None = None) -> None:
        """Handle asynchronous OPEN_CONFIRMED signal if not already stored."""
        raise NotImplementedError("TODO [Package 3]: Implement GateTransactionProcessor.onOpenConfirmed")

    def onPassage(
        self,
        passageEventId: str,
        commandId: str,
        laneId: str,
        occurredAt: datetime | None = None,
    ) -> None:
        """Step A8: Record PassageEvent and create at most one original AttendanceTransaction per scanId (I-07, ADR-02).

        Note: If the GateCycle originated from an assisted opening (`cycle.scanId is None`), record the
        PassageEvent evidence but do NOT create an automatic AttendanceTransaction (ADR-07).
        """
        raise NotImplementedError("TODO [Package 3]: Implement GateTransactionProcessor.onPassage")

    def onLaneClear(self, commandId: str, laneId: str, occurredAt: datetime | None = None) -> None:
        """Step A9: When controller reports LANE_CLEAR (and lane is not in EMERGENCY_HOLD),
        send closeGate once and release the lane on CLOSED (I-05).
        """
        raise NotImplementedError("TODO [Package 3]: Implement GateTransactionProcessor.onLaneClear")

    def onClosed(self, commandId: str, laneId: str, occurredAt: datetime | None = None) -> None:
        """Confirm CLOSED state and release the lane."""
        raise NotImplementedError("TODO [Package 3]: Implement GateTransactionProcessor.onClosed")

    def handlePassageTimeout(self, cycleId: str) -> bool:
        """Flag missing passage after 15 seconds as NO_PASSAGE and PENDING_RECONCILIATION (§4.2.5).
        Never sends closeGate from the timer (I-05).
        """
        raise NotImplementedError("TODO [Package 3]: Implement GateTransactionProcessor.handlePassageTimeout")

    def recordIntervention(self, case: SupportCase) -> bool:
        """Store an authorized support case in Gate Core (Flow B4)."""
        raise NotImplementedError("TODO [Package 3]: Implement GateTransactionProcessor.recordIntervention")

    def requestAssistedOpen(self, supportCaseId: str, laneId: str, now: datetime | None = None) -> ScanResult:
        """Open a free lane for an authorized support case without creating automatic attendance (ADR-07)."""
        raise NotImplementedError("TODO [Package 3]: Implement GateTransactionProcessor.requestAssistedOpen")

    def requestEmergencyRelease(self, supportCaseId: str, laneId: str, now: datetime | None = None) -> ScanResult:
        """Place the lane in EMERGENCY_HOLD and invoke emergencyRelease (Flow B, Table 4.22)."""
        raise NotImplementedError("TODO [Package 3]: Implement GateTransactionProcessor.requestEmergencyRelease")

    def resetEmergency(self, supportCaseId: str, laneId: str, now: datetime | None = None) -> ScanResult:
        """Confirm reset after emergency release so normal closing can proceed."""
        raise NotImplementedError("TODO [Package 3]: Implement GateTransactionProcessor.resetEmergency")
