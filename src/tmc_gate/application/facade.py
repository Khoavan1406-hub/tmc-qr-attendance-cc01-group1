"""[PACKAGE 3] GateCoreFacade implementing GateCoreApi (Figure 4.2, Table 4.7, §7.1.4 ISP)."""

from datetime import datetime

from tmc_gate.application.processor import GateTransactionProcessor
from tmc_gate.domain.enums import DedupStatus, ScanState
from tmc_gate.domain.models import ScanResult, SupportCase
from tmc_gate.domain.snapshot import EligibilitySnapshot
from tmc_gate.ports.inbound import GateCoreApi
from tmc_gate.ports.repositories import (
    IOutboxRepository,
    IScanRepository,
    ISnapshotRepository,
    IUnitOfWork,
)


class GateCoreFacade(GateCoreApi):
    """Public facade of M1 Gate Core realizing IScanSubmission, IInterventionRequest, and IFactoryUpdates."""

    def __init__(
        self,
        processor: GateTransactionProcessor,
        scan_repo: IScanRepository,
        snapshot_repo: ISnapshotRepository,
        outbox_repo: IOutboxRepository,
        uow: IUnitOfWork,
    ) -> None:
        self._processor = processor
        self._scan_repo = scan_repo
        self._snapshot_repo = snapshot_repo
        self._outbox_repo = outbox_repo
        self._uow: IUnitOfWork = uow

    # --- IScanSubmission (StationClient) ---

    def submitScan(
        self,
        scanId: str,
        qrToken: str,
        laneId: str,
        now: datetime | None = None,
        deadline: datetime | None = None,
    ) -> ScanResult:
        return self._processor.processScan(
            scanId=scanId,
            qrToken=qrToken,
            laneId=laneId,
            now=now,
            deadline=deadline,
        )

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

    def requestEmergencyReset(
        self,
        supportCaseId: str,
        laneId: str,
        now: datetime | None = None,
    ) -> ScanResult:
        return self._processor.requestEmergencyReset(supportCaseId=supportCaseId, laneId=laneId, now=now)

    # --- IFactoryUpdates (M9 Site Sync) ---

    def applyEligibilityChanges(
        self,
        snapshot: EligibilitySnapshot,
        eventId: str | None = None,
    ) -> bool:
        evt_id = eventId or f"elig-v{snapshot.snapshotVersion}"
        payload = {
            "factoryId": snapshot.factoryId,
            "snapshotVersion": snapshot.snapshotVersion,
            "confirmedWatermark": snapshot.confirmedWatermark.isoformat(),
        }
        with self._uow:
            status = self._outbox_repo.check_and_mark_processed("GATE_CORE_ELIGIBILITY", evt_id, payload)
            if status == DedupStatus.CONFLICT_FLAGGED:
                self._uow.commit()
                return False
            if status == DedupStatus.APPLIED:
                current = self._snapshot_repo.get_snapshot()
                # §5.7.2 (p. 91): "Duplicates, older versions... cannot refresh the watermark."
                if snapshot.snapshotVersion < current.snapshotVersion:
                    self._uow.commit()
                    return False
                if snapshot.confirmedWatermark < current.confirmedWatermark:
                    snapshot.confirmedWatermark = current.confirmedWatermark
                self._snapshot_repo.save_snapshot(snapshot)
            self._uow.commit()
        return True

    def applyScanResolution(
        self,
        decisionId: str,
        scanId: str,
        conclusion: str,
        eventId: str | None = None,
    ) -> bool:
        evt_id = eventId or f"res-{decisionId}"
        payload = {"decisionId": decisionId, "scanId": scanId, "conclusion": conclusion}
        with self._uow:
            status = self._outbox_repo.check_and_mark_processed("GATE_CORE_RESOLUTION", evt_id, payload)
            if status == DedupStatus.CONFLICT_FLAGGED:
                self._uow.commit()
                return False
            if status == DedupStatus.DUPLICATE_IGNORED:
                self._uow.commit()
                return True
            scan = self._scan_repo.get_scan(scanId)
            if scan is None:
                self._uow.commit()
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
            self._uow.commit()
        return True

    def readCommittedEvents(self) -> list[dict[str, object]]:
        return self._outbox_repo.list_pending_events()
