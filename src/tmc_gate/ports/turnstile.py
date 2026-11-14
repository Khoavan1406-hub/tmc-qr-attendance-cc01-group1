"""Turnstile controller integration ports defined by M1 Gate Core (§4.1.6, §7.1.3 LSP, §7.1.4 ISP, §7.1.5 DIP).

Split into narrower design contracts per §7.1.4:
- IOpenGate: openGate and its result (OPEN_CONFIRMED | CONTROLLER_ERROR | UNDETERMINED)
- ICloseGate: closeGate and its result (CLOSED | CONTROLLER_ERROR | UNDETERMINED)
- IEmergencyGate: emergencyRelease and resetEmergency (EMERGENCY_RELEASED | RESET_CONFIRMED | CONTROLLER_ERROR | UNDETERMINED)
- IPassageEvents: callback contract implemented by Gate Core (ONLY onPassage and onLaneClear)
- IControllerStatusQuery: optional port for querying uncertain command status where the controller API supports it
- ITurnstileControl: composite outbound port (IOpenGate + ICloseGate + IEmergencyGate) implemented by TurnstileAdapter (M2)
"""

from abc import ABC, abstractmethod
from datetime import datetime

from tmc_gate.domain.enums import CommandOutcome

# Final results each port may return (shared by contract tests). Never SENT / NOT_SENT.
OPEN_RESULTS = frozenset({CommandOutcome.OPEN_CONFIRMED, CommandOutcome.CONTROLLER_ERROR, CommandOutcome.UNDETERMINED})
CLOSE_RESULTS = frozenset({CommandOutcome.CLOSED, CommandOutcome.CONTROLLER_ERROR, CommandOutcome.UNDETERMINED})
RELEASE_RESULTS = frozenset({CommandOutcome.EMERGENCY_RELEASED, CommandOutcome.CONTROLLER_ERROR, CommandOutcome.UNDETERMINED})
RESET_RESULTS = frozenset({CommandOutcome.RESET_CONFIRMED, CommandOutcome.CONTROLLER_ERROR, CommandOutcome.UNDETERMINED})


class IOpenGate(ABC):
    """Contract for issuing a normal gate opening command (§7.1.4 ISP)."""

    @abstractmethod
    def openGate(self, commandId: str, laneId: str, timeout_ms: int) -> CommandOutcome:
        """Send openGate command to the controller for a reserved lane.

        Returns only OPEN_CONFIRMED | CONTROLLER_ERROR | UNDETERMINED (OPEN_RESULTS).
        timeout_ms: Flow A passes min(700 ms, remaining scan deadline); assisted opening passes its own timeout.
        If no confirmation arrives within timeout_ms, return UNDETERMINED. A receipt alone is internal to the adapter.

        LSP behavioral rules (§7.1.3):
        - Must distinguish command receipt from OPEN_CONFIRMED (receipt-only returns UNDETERMINED).
        - Must return CONTROLLER_ERROR only when the controller explicitly confirms non-execution.
        - Must return UNDETERMINED on timeout or lost response (timeout is never CONTROLLER_ERROR).
        - Adapter must never synthesize PASSAGE events on its own.
        """


class ICloseGate(ABC):
    """Contract for issuing a safe gate closing command after LANE_CLEAR (§7.1.4 ISP)."""

    @abstractmethod
    def closeGate(self, commandId: str, laneId: str) -> CommandOutcome:
        """Send closeGate once after LANE_CLEAR is recorded; returns only CLOSE_RESULTS (CLOSED | CONTROLLER_ERROR | UNDETERMINED)."""


class IEmergencyGate(ABC):
    """Contract for emergency release and authorized reset operations (§7.1.4 ISP)."""

    @abstractmethod
    def emergencyRelease(self, commandId: str, laneId: str) -> CommandOutcome:
        """Trigger emergency release; returns only RELEASE_RESULTS (EMERGENCY_RELEASED | CONTROLLER_ERROR | UNDETERMINED)."""

    @abstractmethod
    def resetEmergency(self, commandId: str, laneId: str) -> CommandOutcome:
        """Request reset of release mode; returns only RESET_RESULTS. A reset request is not a confirmed reset."""


class IPassageEvents(ABC):
    """Callback contract through which controller signals reach M1 Gate Core (Figure 4.2, §7.1.4).

    Duplicate & late signal rules:
    - onPassage: each passageEventId is accepted at most once (repeated passageEventId is ignored).
      At most one original AttendanceTransaction is created per scanId. A second distinct passageEventId
      for the same scan is stored as PassageEvent evidence and flags the scan for review.
      Automatic attendance is created only for a reliable PASSAGE received within the 15-second
      observation window while the scan is still OPENED. A late PASSAGE (passageStatus NO_PASSAGE),
      or one arriving after the scan moved to PENDING_RECONCILIATION (e.g. after emergency release),
      is stored once with an audit record and flagged LATE_PASSAGE for review; no attendance is created,
      the 15-second timer is not restarted and no closeGate is sent (Table 4.23b; handled in PR2).
      If LANE_CLEAR already closed the lane, onPassage never re-locks it; a closed lane stays released.
    - onLaneClear: if the lane is in EMERGENCY_HOLD, normal closing is blocked until resetEmergency is confirmed.
      Otherwise records LANE_CLEAR and sends closeGate once.
    """

    @abstractmethod
    def onPassage(
        self,
        passageEventId: str,
        commandId: str,
        laneId: str,
        occurredAt: datetime | None = None,
    ) -> None:
        """Handle PASSAGE signal and trigger attendance recording (I-01, I-07)."""

    @abstractmethod
    def onLaneClear(
        self,
        commandId: str,
        laneId: str,
        occurredAt: datetime | None = None,
    ) -> None:
        """Handle LANE_CLEAR signal and trigger safe closing (I-05)."""


class IControllerStatusQuery(ABC):
    """Optional port for querying command execution status when an outcome is UNDETERMINED (§7.1.3, I-03).

    If the adapter does not support status queries, or the query still returns UNDETERMINED,
    GateTransactionProcessor stores UNDETERMINED first and then moves the scan to PENDING_RECONCILIATION.
    The command is never resent.
    """

    @abstractmethod
    def queryStatus(self, commandId: str, laneId: str) -> CommandOutcome:
        """Query the controller for the definitive status of a previously issued commandId."""


class ITurnstileControl(IOpenGate, ICloseGate, IEmergencyGate, ABC):
    """Composite outbound controller port owned by M1 Gate Core and implemented by TurnstileAdapter (M2)."""
