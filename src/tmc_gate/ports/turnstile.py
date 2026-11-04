"""Turnstile controller integration ports defined by M1 Gate Core (§4.1.6, §7.1.3 LSP, §7.1.4 ISP, §7.1.5 DIP).

Split into narrower design contracts per §7.1.4:
- IOpenGate: openGate and its result
- ICloseGate: closeGate and its result
- IEmergencyGate: emergencyRelease and resetEmergency
- IPassageEvents: callback contract for OPEN_CONFIRMED, PASSAGE, LANE_CLEAR, and CLOSED signals
- ITurnstileControl: composite outbound port implemented by TurnstileAdapter (M2)
"""

from abc import ABC, abstractmethod
from datetime import datetime

from tmc_gate.domain.enums import CommandOutcome


class IOpenGate(ABC):
    """Contract for issuing a normal gate opening command (§7.1.4 ISP)."""

    @abstractmethod
    def openGate(self, commandId: str, laneId: str) -> CommandOutcome:
        """Send openGate command to the controller for a reserved lane.

        LSP behavioral rule (§7.1.3):
        - Must distinguish command receipt from OPEN_CONFIRMED.
        - Must return CONTROLLER_ERROR only when the controller explicitly confirms non-execution.
        - Must return UNDETERMINED on timeout or lost response after the command may have been sent.
        """


class ICloseGate(ABC):
    """Contract for issuing a safe gate closing command after LANE_CLEAR (§7.1.4 ISP)."""

    @abstractmethod
    def closeGate(self, commandId: str, laneId: str) -> CommandOutcome:
        """Send closeGate once after LANE_CLEAR is recorded."""


class IEmergencyGate(ABC):
    """Contract for emergency release and authorized reset operations (§7.1.4 ISP)."""

    @abstractmethod
    def emergencyRelease(self, commandId: str, laneId: str) -> CommandOutcome:
        """Trigger emergency release on the specified lane."""

    @abstractmethod
    def resetEmergency(self, commandId: str, laneId: str) -> CommandOutcome:
        """Confirm reset of emergency release mode on the specified lane."""


class IPassageEvents(ABC):
    """Callback contract through which controller signals reach M1 Gate Core (Figure 4.2, §7.1.4)."""

    @abstractmethod
    def onOpenConfirmed(self, commandId: str, laneId: str, occurredAt: datetime | None = None) -> None:
        """Handle late or asynchronous OPEN_CONFIRMED signal from the controller."""

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
    def onLaneClear(self, commandId: str, laneId: str, occurredAt: datetime | None = None) -> None:
        """Handle LANE_CLEAR signal and trigger safe closing (I-05)."""

    @abstractmethod
    def onClosed(self, commandId: str, laneId: str, occurredAt: datetime | None = None) -> None:
        """Handle CLOSED confirmation signal and release the lane."""


class ITurnstileControl(IOpenGate, ICloseGate, IEmergencyGate, ABC):
    """Composite outbound controller port owned by M1 Gate Core and implemented by TurnstileAdapter (M2)."""
