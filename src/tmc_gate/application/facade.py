"""[PACKAGE 3] GateCoreFacade implementing GateCoreApi (Figure 4.2, Table 4.7, §7.1.4 ISP)."""

from datetime import datetime

from tmc_gate.application.processor import GateTransactionProcessor
from tmc_gate.domain.enums import ScanState
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

    # --- IScanSubmission (StationClient) ---

    def submitScan(
        self,
        scanId: str,
        qrToken: str,
        laneId: str,
        now: datetime | None = None,
    ) -> ScanResult:
        return self._processor.processScan(scanId=scanId, qrToken=qrToken, laneId=laneId, now=now)

    def getScanStatus(self, scanId: str) -> ScanResult | None:
        return self._processor.getScanStatus(scanId=scanId)

    # --- IInterventionRequest (M3 Gate Intervention) ---

    def recordIntervention(self, case: SupportCase) -> bool:
        return self._processor.recordIntervention(case=case)

    def requestAssistedOpen(
        self,
        supportCaseId: str,
        laneId: str,
        now: datetime | None = None,
    ) -> ScanResult:
        return self._processor.requestAssistedOpen(supportCaseId=supportCaseId, laneId=laneId, now=now)

    def requestEmergencyRelease(
        self,
        supportCaseId: str,
        laneId: str,
        now: datetime | None = None,
    ) -> ScanResult:
        return self._processor.requestEmergencyRelease(supportCaseId=supportCaseId, laneId=laneId, now=now)

    def resetEmergency(
        self,
        supportCaseId: str,
        laneId: str,
        now: datetime | None = None,
    ) -> ScanResult:
        return self._processor.resetEmergency(supportCaseId=supportCaseId, laneId=laneId, now=now)

    # --- IFactoryUpdates (M9 Site Sync) ---

    def applyEligibilityChanges(self, snapshot: EligibilitySnapshot) -> bool:
        self._snapshot_repo.save_snapshot(snapshot)
        return True

    def applyScanResolution(
        self,
        decisionId: str,
        scanId: str,
        conclusion: str,
    ) -> bool:
        scan = self._scan_repo.get_scan(scanId)
        if scan is None:
            return False
        scan.state = ScanState.RECONCILED
        scan.reason = f"RECONCILED:{decisionId}:{conclusion}"
        self._scan_repo.save_scan(scan)
        self._outbox_repo.append_event(
            {
                "eventId": f"evt-res-{decisionId}",
                "eventType": "RESOLUTION_APPLIED",
                "decisionId": decisionId,
                "scanId": scanId,
                "conclusion": conclusion,
            }
        )
        return True

    def readCommittedEvents(self) -> list[dict[str, object]]:
        return self._outbox_repo.list_pending_events()
