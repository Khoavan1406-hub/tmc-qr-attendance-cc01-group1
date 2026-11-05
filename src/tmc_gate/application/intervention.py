"""[PACKAGE 6] M3 Gate Intervention business components (Figure 4.4, Table 4.9, §4.2.3 Flow B, ADR-07).

Owns no business data of its own: checks guard authority server-side via IAuthorizationApi and calls
M1 Gate Core through the narrow IInterventionRequest interface (§7.1.4 ISP).
Exits EMERGENCY_HOLD only when requestEmergencyReset confirms RESET_CONFIRMED.
"""

from datetime import datetime

from tmc_gate.domain.models import ScanResult
from tmc_gate.ports.inbound import GateInterventionApi, IInterventionRequest
from tmc_gate.ports.repositories import IAuditApi, IAuthorizationApi


class InterventionService:
    """Checks guard authority and coordinates assisted/emergency requests through IInterventionRequest."""

    def __init__(
        self,
        factory_id: str,
        intervention_port: IInterventionRequest,
        auth_api: IAuthorizationApi,
        audit_api: IAuditApi,
    ) -> None:
        self._factory_id = factory_id
        self._intervention_port = intervention_port
        self._auth_api = auth_api
        self._audit_api = audit_api

    def record_and_open_assisted(
        self,
        supportCaseId: str,
        guardId: str,
        employeeId: str,
        laneId: str,
        reason: str,
        linkedScanId: str | None = None,
        now: datetime | None = None,
    ) -> ScanResult:
        """TODO [Package 6]:
        1. Verify guard authority via `self._auth_api.is_authorized(...)`.
        2. Record `SupportCase` via `self._intervention_port.recordIntervention(case)` (does NOT create attendance).
        3. Request gate open via `self._intervention_port.requestAssistedOpen(...)`.
        """
        raise NotImplementedError("TODO [Package 6]: Implement InterventionService.record_and_open_assisted")

    def trigger_emergency_release(
        self,
        supportCaseId: str,
        guardId: str,
        laneId: str,
        reason: str,
        now: datetime | None = None,
    ) -> ScanResult:
        """TODO [Package 6]: Verify guard authority, record emergency case, and call `requestEmergencyRelease(...)`."""
        raise NotImplementedError("TODO [Package 6]: Implement InterventionService.trigger_emergency_release")

    def reset_emergency(self, supportCaseId: str, guardId: str, laneId: str, now: datetime | None = None) -> ScanResult:
        """TODO [Package 6]: Verify guard authority and call `self._intervention_port.requestEmergencyReset(...)`.
        Only exits `EMERGENCY_HOLD` when `RESET_CONFIRMED` is returned.
        """
        raise NotImplementedError("TODO [Package 6]: Implement InterventionService.reset_emergency")


class GateInterventionFacade(GateInterventionApi):
    """Public facade for M3 Gate Intervention realizing GateInterventionApi (Figure 4.4, Table 4.20)."""

    def __init__(self, service: InterventionService) -> None:
        self._service = service

    def recordAssistedAccess(
        self,
        supportCaseId: str,
        guardId: str,
        employeeId: str,
        laneId: str,
        reason: str,
        linkedScanId: str | None = None,
    ) -> bool:
        """TODO [Package 6]: Implement GateInterventionApi.recordAssistedAccess (Flow B3–B4)."""
        raise NotImplementedError("TODO [Package 6]: Implement GateInterventionFacade.recordAssistedAccess")

    def authorizeAssistedOpen(
        self,
        supportCaseId: str,
        guardId: str,
        laneId: str,
    ) -> ScanResult:
        """TODO [Package 6]: Implement GateInterventionApi.authorizeAssistedOpen (Flow B Assisted gate opening)."""
        raise NotImplementedError("TODO [Package 6]: Implement GateInterventionFacade.authorizeAssistedOpen")

    def recordAssistedAccessAndOpen(
        self,
        supportCaseId: str,
        guardId: str,
        employeeId: str,
        laneId: str,
        reason: str,
        linkedScanId: str | None = None,
    ) -> ScanResult:
        return self._service.record_and_open_assisted(
            supportCaseId=supportCaseId,
            guardId=guardId,
            employeeId=employeeId,
            laneId=laneId,
            reason=reason,
            linkedScanId=linkedScanId,
        )

    def emergencyRelease(self, supportCaseId: str, guardId: str, laneId: str, reason: str) -> ScanResult:
        return self._service.trigger_emergency_release(
            supportCaseId=supportCaseId,
            guardId=guardId,
            laneId=laneId,
            reason=reason,
        )

    def resetEmergency(self, supportCaseId: str, guardId: str, laneId: str) -> ScanResult:
        return self._service.reset_emergency(supportCaseId=supportCaseId, guardId=guardId, laneId=laneId)

    def requestEmergencyReset(self, supportCaseId: str, guardId: str, laneId: str) -> ScanResult:
        return self._service.reset_emergency(supportCaseId=supportCaseId, guardId=guardId, laneId=laneId)
