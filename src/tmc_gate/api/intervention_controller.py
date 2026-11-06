"""[PACKAGE 6] Entry layer controller for M3 Gate Intervention: GateInterventionController (Figure 4.4, Table 4.9)."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from tmc_gate.application.intervention import GateInterventionFacade


class AssistedAccessRequest(BaseModel):
    supportCaseId: str = Field(..., examples=["case-001"])
    guardId: str = Field("GUARD-BD-01", examples=["GUARD-BD-01"])
    employeeId: str = Field("EMP-001", examples=["EMP-001"])
    laneId: str = Field("LANE-BD-01", examples=["LANE-BD-01"])
    reason: str = Field("Employee smartphone battery depleted", description="Guard-verified reason")
    linkedScanId: str | None = Field(None, description="Optional scanId if originating from a failed scan")


class EmergencyActionRequest(BaseModel):
    supportCaseId: str = Field(..., examples=["emg-001"])
    guardId: str = Field("GUARD-BD-01", examples=["GUARD-BD-01"])
    laneId: str = Field("LANE-BD-01", examples=["LANE-BD-01"])
    reason: str = Field("Safety evacuation / lane obstruction")


def build_intervention_router(facade: GateInterventionFacade) -> APIRouter:
    """Create FastAPI router for M3 Gate Intervention (Flow B)."""
    router = APIRouter()

    @router.post("/api/interventions/assisted-open", tags=["M3 - Gate Intervention (Flow B)"])
    def assisted_open(req: AssistedAccessRequest) -> dict[str, object]:
        """Guard records assisted access case and opens the gate (does not create attendance without HR approval)."""
        result = facade.recordAssistedAccessAndOpen(
            supportCaseId=req.supportCaseId,
            guardId=req.guardId,
            employeeId=req.employeeId,
            laneId=req.laneId,
            reason=req.reason,
            linkedScanId=req.linkedScanId,
        )
        return {
            "supportCaseId": req.supportCaseId,
            "state": result.state.value if hasattr(result.state, "value") else str(result.state),
            "laneId": result.laneId,
            "commandId": result.commandId,
            "reason": result.reason,
        }

    @router.post("/api/interventions/emergency-release", tags=["M3 - Gate Intervention (Flow B)"])
    def emergency_release(req: EmergencyActionRequest) -> dict[str, object]:
        """Guard triggers emergencyRelease -> puts lane in EMERGENCY_HOLD."""
        result = facade.emergencyRelease(
            supportCaseId=req.supportCaseId,
            guardId=req.guardId,
            laneId=req.laneId,
            reason=req.reason,
        )
        return {
            "supportCaseId": req.supportCaseId,
            "state": result.state.value if hasattr(result.state, "value") else str(result.state),
            "laneId": result.laneId,
            "commandId": result.commandId,
            "reason": result.reason,
        }

    @router.post("/api/interventions/emergency-reset", tags=["M3 - Gate Intervention (Flow B)"])
    def emergency_reset(req: EmergencyActionRequest) -> dict[str, object]:
        """Guard confirms incident is over and resets EMERGENCY_HOLD on the lane."""
        result = facade.resetEmergency(
            supportCaseId=req.supportCaseId,
            guardId=req.guardId,
            laneId=req.laneId,
        )
        return {
            "supportCaseId": req.supportCaseId,
            "state": result.state.value if hasattr(result.state, "value") else str(result.state),
            "laneId": result.laneId,
            "commandId": result.commandId,
            "reason": result.reason,
        }

    return router
