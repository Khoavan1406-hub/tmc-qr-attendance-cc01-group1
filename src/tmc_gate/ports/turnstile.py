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


class IOpenGate(ABC):
    """Contract for issuing a normal gate opening command (§7.1.4 ISP)."""

    @abstractmethod
    def openGate(self, commandId: str, laneId: str) -> CommandOutcome:
        """Send openGate command to the controller for a reserved lane.

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
        """Send closeGate once after LANE_CLEAR is recorded; returns CLOSED, CONTROLLER_ERROR, or UNDETERMINED."""


class IEmergencyGate(ABC):
    """Contract for emergency release and authorized reset operations (§7.1.4 ISP)."""

    @abstractmethod
    def emergencyRelease(self, commandId: str, laneId: str) -> CommandOutcome:
        """Trigger emergency release on the specified lane."""

    @abstractmethod
    def resetEmergency(self, commandId: str, laneId: str) -> CommandOutcome:
        """Confirm reset of emergency release mode on the specified lane."""


class IPassageEvents(ABC):
    """Callback contract through which controller signals reach M1 Gate Core (Figure 4.2, §7.1.4).

    Duplicate & late signal rules:
    - onPassage: each passageEventId is accepted at most once (repeated passageEventId is ignored).
      At most one original AttendanceTransaction is created per scanId. A second distinct passageEventId
      for the same scan is stored as PassageEvent evidence and flags the scan for review.
      If LANE_CLEAR already closed the lane before PASSAGE arrived, onPassage records the passage and
      attendance without re-locking the closed lane.
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

    If the adapter does not support status queries (or the query still returns UNDETERMINED),
    GateTransactionProcessor routes the scan to PENDING_RECONCILIATION without resending the command.
    """

    @abstractmethod
    def queryStatus(self, commandId: str, laneId: str) -> CommandOutcome:
        """Query the controller for the definitive status of a previously issued commandId."""


class ITurnstileControl(IOpenGate, ICloseGate, IEmergencyGate, ABC):
    """Composite outbound controller port owned by M1 Gate Core and implemented by TurnstileAdapter (M2)."""
