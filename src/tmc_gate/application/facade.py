"""[PACKAGE 3] GateCoreFacade implementing GateCoreApi (Figure 4.2, Table 4.7, §7.1.4 ISP)."""

from datetime import datetime

from tmc_gate.application.processor import GateTransactionProcessor
from tmc_gate.domain.models import ScanResult, SupportCase
from tmc_gate.domain.snapshot import EligibilitySnapshot
from tmc_gate.ports.inbound import GateCoreApi
from tmc_gate.ports.repositories import IOutboxRepository, IScanRepository, ISnapshotRepository


class GateCoreFacade(GateCoreApi):
    """Public facade of M1 Gate Core realizing IScanSubmission, IInterventionRequest, and IFactoryUpdates."""

    def __init__(
        self,
        processor: GateTransactionProcessor,
        scan_repo: IScanRepository,
        snapshot_repo: ISnapshotRepository,
        outbox_repo: IOutboxRepository,
    ) -> None:
        self._processor = processor
        self._scan_repo = scan_repo
        self._snapshot_repo = snapshot_repo
        self._outbox_repo = outbox_repo

    def submitScan(
        self,
        scanId: str,
        qrToken: str,
        laneId: str,
        now: datetime | None = None,
    ) -> ScanResult:
        """TODO [Package 3]: Delegate to `self._processor.processScan(...)`."""
        raise NotImplementedError("TODO [Package 3]: Implement GateCoreFacade.submitScan")

    def getScanStatus(self, scanId: str) -> ScanResult | None:
        """TODO [Package 3]: Delegate to `self._processor.getScanStatus(...)`."""
        raise NotImplementedError("TODO [Package 3]: Implement GateCoreFacade.getScanStatus")

    def recordIntervention(self, case: SupportCase) -> bool:
        """TODO [Package 3]: Delegate to `self._processor.recordIntervention(...)`."""
        raise NotImplementedError("TODO [Package 3]: Implement GateCoreFacade.recordIntervention")

    def requestAssistedOpen(
        self,
        supportCaseId: str,
        laneId: str,
        now: datetime | None = None,
    ) -> ScanResult:
        """TODO [Package 3]: Delegate to `self._processor.requestAssistedOpen(...)`."""
        raise NotImplementedError("TODO [Package 3]: Implement GateCoreFacade.requestAssistedOpen")

    def requestEmergencyRelease(
        self,
        supportCaseId: str,
        laneId: str,
        now: datetime | None = None,
    ) -> ScanResult:
        """TODO [Package 3]: Delegate to `self._processor.requestEmergencyRelease(...)`."""
        raise NotImplementedError("TODO [Package 3]: Implement GateCoreFacade.requestEmergencyRelease")

    def resetEmergency(
        self,
        supportCaseId: str,
        laneId: str,
        now: datetime | None = None,
    ) -> ScanResult:
        """TODO [Package 3]: Delegate to `self._processor.resetEmergency(...)`."""
        raise NotImplementedError("TODO [Package 3]: Implement GateCoreFacade.resetEmergency")

    def applyEligibilityChanges(self, snapshot: EligibilitySnapshot) -> bool:
        """TODO [Package 3]: Save updated snapshot via `self._snapshot_repo.save_snapshot(snapshot)`."""
        raise NotImplementedError("TODO [Package 3]: Implement GateCoreFacade.applyEligibilityChanges")

    def applyScanResolution(
        self,
        decisionId: str,
        scanId: str,
        conclusion: str,
    ) -> bool:
        """TODO [Package 3]: Mark scan as RECONCILED and append RESOLUTION_APPLIED event to outbox."""
        raise NotImplementedError("TODO [Package 3]: Implement GateCoreFacade.applyScanResolution")

    def readCommittedEvents(self) -> list[dict[str, object]]:
        """TODO [Package 3]: Return pending events from `self._outbox_repo.list_pending_events()`."""
        raise NotImplementedError("TODO [Package 3]: Implement GateCoreFacade.readCommittedEvents")
