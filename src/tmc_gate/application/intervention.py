"""[PACKAGE 6] M3 Gate Intervention business components (Figure 4.4, Table 4.9, §4.2.3 Flow B, ADR-07).

Owns no business data of its own: checks guard authority via AuthorizationApi and calls
M1 Gate Core through the narrow IInterventionRequest interface (§7.1.4 ISP).
"""

from datetime import datetime

from tmc_gate.domain.models import ScanResult
from tmc_gate.ports.inbound import IInterventionRequest
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
        """Flow B3–B4 + Assisted gate opening: verify guard permission, store case, and open gate.

        TODO [Package 6]:
        1. Check `self._auth_api.is_authorized(guardId, "SECURITY_GUARD", "ASSISTED_ACCESS", self._factory_id)`.
           If False, return `ScanResult(..., state=ScanState.REJECTED, reason="GUARD_UNAUTHORIZED")`.
        2. Create `SupportCase(isEmergency=False, ...)` and call `self._intervention_port.recordIntervention(case)`.
        3. Log audit via `self._audit_api.append_async(...)` and return `self._intervention_port.requestAssistedOpen(...)`.
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
        """Authorize and trigger emergencyRelease on a lane (Flow B Emergency opening).

        TODO [Package 6]:
        1. Verify guard authority for `"EMERGENCY_RELEASE"`.
        2. Create `SupportCase(isEmergency=True, ...)`, record it via `self._intervention_port.recordIntervention(case)`,
           and call `self._intervention_port.requestEmergencyRelease(...)`.
        """
        raise NotImplementedError("TODO [Package 6]: Implement InterventionService.trigger_emergency_release")

    def reset_emergency(self, supportCaseId: str, guardId: str, laneId: str, now: datetime | None = None) -> ScanResult:
        """Authorize and confirm reset of emergency release mode.

        TODO [Package 6]:
        Verify guard authority for `"EMERGENCY_RESET"` and call `self._intervention_port.resetEmergency(...)`.
        """
        raise NotImplementedError("TODO [Package 6]: Implement InterventionService.reset_emergency")


class GateInterventionFacade:
    """Public facade for M3 Gate Intervention (Figure 4.4)."""

    def __init__(self, service: InterventionService) -> None:
        self._service = service

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
