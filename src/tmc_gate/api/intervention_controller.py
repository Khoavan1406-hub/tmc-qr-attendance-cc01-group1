"""[PACKAGE 6] Entry layer controller for M3 Gate Intervention: GateInterventionController (Figure 4.4, Table 4.9).

The acting guard comes from the X-Guard-Session header, resolved server-side through IGuardIdentityApi.
A guardId sent in the body is ignored. Missing or unknown sessions get HTTP 401 before any M3 call.
"""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from tmc_gate.application.intervention import GateInterventionFacade
from tmc_gate.domain.models import ScanResult
from tmc_gate.ports.repositories import IGuardIdentityApi

GUARD_SESSION_HEADER = "X-Guard-Session"


class AssistedAccessRequest(BaseModel):
    supportCaseId: str = Field(..., examples=["case-001"])
    employeeId: str = Field(..., examples=["EMP-001"])
    laneId: str = Field(..., examples=["LANE-BD-01"])
    reason: str = Field(..., description="Guard-verified reason", examples=["Employee smartphone battery depleted"])
    linkedScanId: str | None = Field(None, description="Optional scanId if originating from a failed scan")


class EmergencyReleaseRequest(BaseModel):
    supportCaseId: str = Field(..., examples=["emg-001"])
    laneId: str = Field(..., examples=["LANE-BD-01"])
    reason: str = Field(..., examples=["Safety evacuation / lane obstruction"])


class EmergencyResetRequest(BaseModel):
    supportCaseId: str = Field(..., examples=["emg-001"])
    laneId: str = Field(..., examples=["LANE-BD-01"])


def _to_body(supportCaseId: str, result: ScanResult) -> dict[str, object]:
    return {
        "supportCaseId": supportCaseId,
        "state": result.state.value if hasattr(result.state, "value") else str(result.state),
        "laneId": result.laneId,
        "commandId": result.commandId,
        "reason": result.reason,
    }


def build_intervention_router(facade: GateInterventionFacade, guard_identity: IGuardIdentityApi) -> APIRouter:
    """Create FastAPI router for M3 Gate Intervention (Flow B)."""
    router = APIRouter()

    def _signed_in_guard(session: str | None) -> str:
        guard_id = guard_identity.resolve_guard(session) if session else None
        if guard_id is None:
            raise HTTPException(status_code=401, detail="GUARD_NOT_AUTHENTICATED")
        return guard_id

    @router.post("/api/interventions/assisted-open", tags=["M3 - Gate Intervention (Flow B)"])
    def assisted_open(
        req: AssistedAccessRequest,
        x_guard_session: str | None = Header(None, alias=GUARD_SESSION_HEADER),
    ) -> dict[str, object]:
        """Guard records assisted access case and opens the gate (does not create attendance without HR approval)."""
        guard_id = _signed_in_guard(x_guard_session)
        result = facade.recordAssistedAccessAndOpen(
            supportCaseId=req.supportCaseId,
            guardId=guard_id,
            employeeId=req.employeeId,
            laneId=req.laneId,
            reason=req.reason,
            linkedScanId=req.linkedScanId,
        )
        return _to_body(req.supportCaseId, result)

    @router.post("/api/interventions/emergency-release", tags=["M3 - Gate Intervention (Flow B)"])
    def emergency_release(
        req: EmergencyReleaseRequest,
        x_guard_session: str | None = Header(None, alias=GUARD_SESSION_HEADER),
    ) -> dict[str, object]:
        """Guard triggers emergencyRelease -> puts lane in EMERGENCY_HOLD."""
        guard_id = _signed_in_guard(x_guard_session)
        result = facade.emergencyRelease(
            supportCaseId=req.supportCaseId,
            guardId=guard_id,
            laneId=req.laneId,
            reason=req.reason,
        )
        return _to_body(req.supportCaseId, result)

    @router.post("/api/interventions/emergency-reset", tags=["M3 - Gate Intervention (Flow B)"])
    def emergency_reset(
        req: EmergencyResetRequest,
        x_guard_session: str | None = Header(None, alias=GUARD_SESSION_HEADER),
    ) -> dict[str, object]:
        """Guard confirms incident is over and resets EMERGENCY_HOLD on the lane."""
        guard_id = _signed_in_guard(x_guard_session)
        result = facade.resetEmergency(
            supportCaseId=req.supportCaseId,
            guardId=guard_id,
            laneId=req.laneId,
        )
        return _to_body(req.supportCaseId, result)

    return router
