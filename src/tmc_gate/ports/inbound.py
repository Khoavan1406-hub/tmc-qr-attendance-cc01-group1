"""Inbound public interfaces of M1 Gate Core and M3 Gate Intervention (§4.2.1 Table 4.20, §7.1.4 ISP).

- IScanSubmission: used only by StationClient (kiosk). Cannot call intervention or emergency operations.
- IInterventionRequest: used only by M3 Gate Intervention (includes requestEmergencyReset).
- IFactoryUpdates: used only by M9 Site Sync.
- GateCoreApi: composite interface realized by GateCoreFacade.
- GateInterventionApi: public interface of M3 Gate Intervention (Table 4.20).
"""

from abc import ABC, abstractmethod
from datetime import datetime

from tmc_gate.domain.models import ScanResult, SupportCase
from tmc_gate.domain.snapshot import EligibilitySnapshot


class IScanSubmission(ABC):
    """Narrow contract for StationClient at the kiosk (§7.1.4 ISP)."""

    @abstractmethod
    def submitScan(
        self,
        scanId: str,
        qrToken: str,
        laneId: str,
        now: datetime | None = None,
        deadline: datetime | None = None,
    ) -> ScanResult:
        """Submit a QR scan attempt from a kiosk and return the gate opening result."""

    @abstractmethod
    def getScanStatus(self, scanId: str) -> ScanResult | None:
        """Query the stored result of a previously submitted scanId after a timeout (never downgrades OPENED)."""


class IInterventionRequest(ABC):
    """Narrow contract for M3 Gate Intervention (§7.1.4 ISP)."""

    @abstractmethod
    def recordIntervention(self, case: SupportCase) -> bool:
        """Store an authorized support/intervention case, audit evidence, and outbox event in Gate Core (Flow B4)."""

    @abstractmethod
    def requestAssistedOpen(
        self,
        supportCaseId: str,
        laneId: str,
        now: datetime | None = None,
    ) -> ScanResult:
        """Request an assisted gate opening for an eligible employee under a recorded support case."""

    @abstractmethod
    def requestEmergencyRelease(
        self,
        supportCaseId: str,
        laneId: str,
        now: datetime | None = None,
    ) -> ScanResult:
        """Place the lane in EMERGENCY_HOLD and trigger emergencyRelease on the controller."""

    @abstractmethod
    def requestEmergencyReset(
        self,
        supportCaseId: str,
        laneId: str,
        now: datetime | None = None,
    ) -> ScanResult:
        """Request reset of emergency release mode; exits EMERGENCY_HOLD only when controller confirms RESET_CONFIRMED."""

    def resetEmergency(
        self,
        supportCaseId: str,
        laneId: str,
        now: datetime | None = None,
    ) -> ScanResult:
        """Alias forwarding to requestEmergencyReset for Table 4.20 compatibility."""
        return self.requestEmergencyReset(supportCaseId=supportCaseId, laneId=laneId, now=now)


class IFactoryUpdates(ABC):
    """Narrow contract for M9 Site Sync (§7.1.4 ISP, ADR-05)."""

    @abstractmethod
    def applyEligibilityChanges(
        self,
        snapshot: EligibilitySnapshot,
        eventId: str | None = None,
    ) -> bool:
        """Apply versioned eligibility changes and advance the confirmed watermark."""

    @abstractmethod
    def applyScanResolution(
        self,
        decisionId: str,
        scanId: str,
        conclusion: str,
        eventId: str | None = None,
    ) -> bool:
        """Store a reconciliation decision linked to a scan and mark it RECONCILED."""

    @abstractmethod
    def readCommittedEvents(self) -> list[dict[str, object]]:
        """Read pending committed Gate Event Feed records from the factory outbox."""


class GateCoreApi(IScanSubmission, IInterventionRequest, IFactoryUpdates, ABC):
    """Composite public interface of M1 Gate Core realized by GateCoreFacade (Table 4.20)."""


class GateInterventionApi(ABC):
    """Public interface of M3 Gate Intervention (Figure 4.4, Table 4.20 p. 57)."""

    @abstractmethod
    def recordAssistedAccess(
        self,
        supportCaseId: str,
        guardId: str,
        employeeId: str,
        laneId: str,
        reason: str,
        linkedScanId: str | None = None,
    ) -> bool:
        """Flow B3–B4: Verify guard authority and record the assisted-access case in Gate Core."""

    @abstractmethod
    def authorizeAssistedOpen(
        self,
        supportCaseId: str,
        guardId: str,
        laneId: str,
    ) -> ScanResult:
        """Flow B Assisted gate opening: Authorize and request Gate Core to open a free lane."""

    @abstractmethod
    def emergencyRelease(
        self,
        supportCaseId: str,
        guardId: str,
        laneId: str,
        reason: str,
    ) -> ScanResult:
        """Flow B Emergency opening: Authorize and trigger emergencyRelease on the lane."""

    @abstractmethod
    def resetEmergency(
        self,
        supportCaseId: str,
        guardId: str,
        laneId: str,
    ) -> ScanResult:
        """Flow B Emergency reset: Confirm incident has ended and request reset of EMERGENCY_HOLD."""
