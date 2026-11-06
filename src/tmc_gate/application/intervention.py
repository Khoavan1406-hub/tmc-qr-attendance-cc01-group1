"""[PACKAGE 6] M3 Gate Intervention business components (Figure 4.4, Table 4.9, §4.2.3 Flow B, ADR-07).

Owns no business data of its own: checks guard authority server-side via IAuthorizationApi and calls
M1 Gate Core through the narrow IInterventionRequest interface (§7.1.4 ISP).
Exits EMERGENCY_HOLD only when requestEmergencyReset confirms RESET_CONFIRMED.
"""

from datetime import UTC, datetime

from tmc_gate.domain.enums import ScanState
from tmc_gate.domain.models import ScanResult, SupportCase
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

    def _case_not_recorded(self, supportCaseId: str, guardId: str, laneId: str) -> ScanResult:
        """Gate Core did not store the case (storage failure or supportCaseId reused with different evidence).

        No gate command is requested, because the guard action would have no matching evidence.
        """
        self._audit_api.append_async({
            "action": "SUPPORT_CASE_NOT_RECORDED",
            "supportCaseId": supportCaseId,
            "guardId": guardId,
            "laneId": laneId,
            "factoryId": self._factory_id,
        })
        return ScanResult(
            scanId=supportCaseId,
            state=ScanState.REJECTED,
            laneId=laneId,
            reason="SUPPORT_CASE_NOT_RECORDED",
        )

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
        """Flow B (Assisted Access): Verify guard authority, record SupportCase, and request assisted gate opening.

        In accordance with I-04 and ADR-07, assisted gate opening does NOT create an automatic attendance
        transaction; attendance must await subsequent HR review and reconciliation.
        """
        current_time = now or datetime.now(UTC)

        # 1. Verify guard authority server-side via IAuthorizationApi
        if not self._auth_api.is_authorized(
            actorId=guardId,
            role="SECURITY_GUARD",
            action="ASSISTED_OPEN",
            factoryId=self._factory_id,
        ):
            self._audit_api.append_async({
                "action": "GUARD_UNAUTHORIZED",
                "supportCaseId": supportCaseId,
                "guardId": guardId,
                "laneId": laneId,
                "factoryId": self._factory_id,
            })
            return ScanResult(
                scanId=supportCaseId,
                state=ScanState.REJECTED,
                laneId=laneId,
                reason="GUARD_UNAUTHORIZED",
            )

        # 2. Record SupportCase in Gate Core via narrow IInterventionRequest (does NOT create attendance)
        case = SupportCase(
            supportCaseId=supportCaseId,
            factoryId=self._factory_id,
            laneId=laneId,
            guardId=guardId,
            reason=reason,
            occurredAt=current_time,
            employeeId=employeeId,
            linkedScanId=linkedScanId,
            isEmergency=False,
        )
        if not self._intervention_port.recordIntervention(case):
            return self._case_not_recorded(supportCaseId, guardId, laneId)

        # 3. Request gate open through IInterventionRequest
        result = self._intervention_port.requestAssistedOpen(
            supportCaseId=supportCaseId,
            laneId=laneId,
            now=current_time,
        )

        # 4. Audit evidence forwarded asynchronously
        self._audit_api.append_async({
            "action": "ASSISTED_OPEN_REQUESTED",
            "supportCaseId": supportCaseId,
            "guardId": guardId,
            "employeeId": employeeId,
            "laneId": laneId,
            "state": result.state.value if hasattr(result.state, "value") else str(result.state),
            "commandId": result.commandId,
            "reason": result.reason,
        })
        return result

    def record_assisted_case(
        self,
        supportCaseId: str,
        guardId: str,
        employeeId: str,
        laneId: str,
        reason: str,
        linkedScanId: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Flow B3–B4: Verify guard authority and record SupportCase without opening gate."""
        current_time = now or datetime.now(UTC)

        if not self._auth_api.is_authorized(
            actorId=guardId,
            role="SECURITY_GUARD",
            action="RECORD_ASSISTED",
            factoryId=self._factory_id,
        ):
            self._audit_api.append_async({
                "action": "GUARD_UNAUTHORIZED",
                "supportCaseId": supportCaseId,
                "guardId": guardId,
                "laneId": laneId,
                "factoryId": self._factory_id,
            })
            return False

        case = SupportCase(
            supportCaseId=supportCaseId,
            factoryId=self._factory_id,
            laneId=laneId,
            guardId=guardId,
            reason=reason,
            occurredAt=current_time,
            employeeId=employeeId,
            linkedScanId=linkedScanId,
            isEmergency=False,
        )
        if not self._intervention_port.recordIntervention(case):
            self._case_not_recorded(supportCaseId, guardId, laneId)
            return False
        self._audit_api.append_async({
            "action": "SUPPORT_CASE_RECORDED",
            "supportCaseId": supportCaseId,
            "guardId": guardId,
            "laneId": laneId,
        })
        return True

    def authorize_assisted_open(
        self,
        supportCaseId: str,
        guardId: str,
        laneId: str,
        now: datetime | None = None,
    ) -> ScanResult:
        """Flow B: Authorize and request Gate Core to open a free lane for an existing support case."""
        current_time = now or datetime.now(UTC)

        if not self._auth_api.is_authorized(
            actorId=guardId,
            role="SECURITY_GUARD",
            action="ASSISTED_OPEN",
            factoryId=self._factory_id,
        ):
            self._audit_api.append_async({
                "action": "GUARD_UNAUTHORIZED",
                "supportCaseId": supportCaseId,
                "guardId": guardId,
                "laneId": laneId,
                "factoryId": self._factory_id,
            })
            return ScanResult(
                scanId=supportCaseId,
                state=ScanState.REJECTED,
                laneId=laneId,
                reason="GUARD_UNAUTHORIZED",
            )

        result = self._intervention_port.requestAssistedOpen(
            supportCaseId=supportCaseId,
            laneId=laneId,
            now=current_time,
        )
        self._audit_api.append_async({
            "action": "ASSISTED_OPEN_AUTHORIZED",
            "supportCaseId": supportCaseId,
            "guardId": guardId,
            "laneId": laneId,
            "state": result.state.value if hasattr(result.state, "value") else str(result.state),
            "commandId": result.commandId,
            "reason": result.reason,
        })
        return result

    def trigger_emergency_release(
        self,
        supportCaseId: str,
        guardId: str,
        laneId: str,
        reason: str,
        now: datetime | None = None,
    ) -> ScanResult:
        """Flow B (Emergency Release): Verify guard authority, record emergency case, and put lane in EMERGENCY_HOLD."""
        current_time = now or datetime.now(UTC)

        # 1. Verify guard authority server-side via IAuthorizationApi
        if not self._auth_api.is_authorized(
            actorId=guardId,
            role="SECURITY_GUARD",
            action="EMERGENCY_RELEASE",
            factoryId=self._factory_id,
        ):
            self._audit_api.append_async({
                "action": "GUARD_UNAUTHORIZED",
                "supportCaseId": supportCaseId,
                "guardId": guardId,
                "laneId": laneId,
                "factoryId": self._factory_id,
            })
            return ScanResult(
                scanId=supportCaseId,
                state=ScanState.REJECTED,
                laneId=laneId,
                reason="GUARD_UNAUTHORIZED",
            )

        # 2. Record emergency SupportCase
        case = SupportCase(
            supportCaseId=supportCaseId,
            factoryId=self._factory_id,
            laneId=laneId,
            guardId=guardId,
            reason=reason,
            occurredAt=current_time,
            employeeId=None,
            linkedScanId=None,
            isEmergency=True,
        )
        if not self._intervention_port.recordIntervention(case):
            return self._case_not_recorded(supportCaseId, guardId, laneId)

        # 3. Call requestEmergencyRelease -> puts lane in EMERGENCY_HOLD and triggers emergencyRelease on controller
        result = self._intervention_port.requestEmergencyRelease(
            supportCaseId=supportCaseId,
            laneId=laneId,
            now=current_time,
        )

        # 4. Audit evidence
        self._audit_api.append_async({
            "action": "EMERGENCY_RELEASE_TRIGGERED",
            "supportCaseId": supportCaseId,
            "guardId": guardId,
            "laneId": laneId,
            "reason": reason,
            "state": result.state.value if hasattr(result.state, "value") else str(result.state),
            "commandId": result.commandId,
        })
        return result

    def reset_emergency(
        self,
        supportCaseId: str,
        guardId: str,
        laneId: str,
        now: datetime | None = None,
    ) -> ScanResult:
        """Flow B (Emergency Reset): Verify guard authority and request reset of EMERGENCY_HOLD.

        The lane exits EMERGENCY_HOLD only when the controller returns RESET_CONFIRMED.
        """
        current_time = now or datetime.now(UTC)

        # 1. Verify guard authority server-side via IAuthorizationApi
        if not self._auth_api.is_authorized(
            actorId=guardId,
            role="SECURITY_GUARD",
            action="EMERGENCY_RESET",
            factoryId=self._factory_id,
        ):
            self._audit_api.append_async({
                "action": "GUARD_UNAUTHORIZED",
                "supportCaseId": supportCaseId,
                "guardId": guardId,
                "laneId": laneId,
                "factoryId": self._factory_id,
            })
            return ScanResult(
                scanId=supportCaseId,
                state=ScanState.REJECTED,
                laneId=laneId,
                reason="GUARD_UNAUTHORIZED",
            )

        # 2. Request emergency reset through IInterventionRequest
        result = self._intervention_port.requestEmergencyReset(
            supportCaseId=supportCaseId,
            laneId=laneId,
            now=current_time,
        )

        # 3. Audit evidence
        self._audit_api.append_async({
            "action": "EMERGENCY_RESET_REQUESTED",
            "supportCaseId": supportCaseId,
            "guardId": guardId,
            "laneId": laneId,
            "state": result.state.value if hasattr(result.state, "value") else str(result.state),
            "reason": result.reason,
            "commandId": result.commandId,
        })
        return result


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
        return self._service.record_assisted_case(
            supportCaseId=supportCaseId,
            guardId=guardId,
            employeeId=employeeId,
            laneId=laneId,
            reason=reason,
            linkedScanId=linkedScanId,
        )

    def authorizeAssistedOpen(
        self,
        supportCaseId: str,
        guardId: str,
        laneId: str,
    ) -> ScanResult:
        return self._service.authorize_assisted_open(
            supportCaseId=supportCaseId,
            guardId=guardId,
            laneId=laneId,
        )

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
