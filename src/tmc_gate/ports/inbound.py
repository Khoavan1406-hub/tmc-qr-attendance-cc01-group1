"""Inbound public interfaces of M1 Gate Core segregated by consumer (§4.2.1 Table 4.20, §7.1.4 ISP).

- IScanSubmission: used only by StationClient (kiosk). Cannot call intervention or emergency operations.
- IInterventionRequest: used only by M3 Gate Intervention.
- IFactoryUpdates: used only by M9 Site Sync.
- GateCoreApi: composite interface realized by GateCoreFacade.
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
    ) -> ScanResult:
        """Submit a QR scan attempt from a kiosk and return the gate opening result."""

    @abstractmethod
    def getScanStatus(self, scanId: str) -> ScanResult | None:
        """Query the stored result of a previously submitted scanId after a timeout."""


class IInterventionRequest(ABC):
    """Narrow contract for M3 Gate Intervention (§7.1.4 ISP)."""

    @abstractmethod
    def recordIntervention(self, case: SupportCase) -> bool:
        """Store an authorized support/intervention case in Gate Core's repository."""

    @abstractmethod
    def requestAssistedOpen(
        self,
        supportCaseId: str,
        laneId: str,
        now: datetime | None = None,
    ) -> ScanResult:
        """Request an assisted gate opening for an eligible employee under an recorded support case."""

    @abstractmethod
    def requestEmergencyRelease(
        self,
        supportCaseId: str,
        laneId: str,
        now: datetime | None = None,
    ) -> ScanResult:
        """Place the lane in EMERGENCY_HOLD and trigger emergencyRelease on the controller."""

    @abstractmethod
    def resetEmergency(
        self,
        supportCaseId: str,
        laneId: str,
        now: datetime | None = None,
    ) -> ScanResult:
        """Confirm that emergency release mode has ended so normal closing rules can resume."""


class IFactoryUpdates(ABC):
    """Narrow contract for M9 Site Sync (§7.1.4 ISP)."""

    @abstractmethod
    def applyEligibilityChanges(self, snapshot: EligibilitySnapshot) -> bool:
        """Apply versioned eligibility changes and advance the confirmed watermark."""

    @abstractmethod
    def applyScanResolution(
        self,
        decisionId: str,
        scanId: str,
        conclusion: str,
    ) -> bool:
        """Store a reconciliation decision linked to a scan and mark it RECONCILED."""

    @abstractmethod
    def readCommittedEvents(self) -> list[dict[str, object]]:
        """Read pending committed Gate Event Feed records from the factory outbox."""


class GateCoreApi(IScanSubmission, IInterventionRequest, IFactoryUpdates, ABC):
    """Composite public interface of M1 Gate Core realized by GateCoreFacade (Table 4.20)."""
