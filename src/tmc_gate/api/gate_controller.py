"""[PACKAGE 3] Entry layer controllers for M1 Gate Core: GateCoreController & ControllerSignalHandler (Figure 4.2, Table 4.7)."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tmc_gate.ports.inbound import IScanSubmission
from tmc_gate.ports.turnstile import IPassageEvents


class SubmitScanRequest(BaseModel):
    scanId: str = Field(..., examples=["scan-001"], description="Client-generated scan identifier (Table 4.24)")
    qrToken: str = Field(..., description="Signed Ed25519 QR credential valid for 60 seconds")
    laneId: str = Field("LANE-BD-01", examples=["LANE-BD-01", "LANE-BD-02"], description="Registered factory lane ID")


class ControllerSignalRequest(BaseModel):
    commandId: str = Field(..., description="Command identifier returned when the gate was opened")
    laneId: str = Field("LANE-BD-01", description="Lane reporting the signal")
    passageEventId: str | None = Field(None, examples=["pas-001"], description="Required for PASSAGE signal (I-01)")


def build_gate_core_router(
    scan_submission: IScanSubmission,
    signal_handler: IPassageEvents,
) -> APIRouter:
    """Create FastAPI router for GateCoreController and ControllerSignalHandler (Figure 4.2)."""
    router = APIRouter()

    @router.post("/api/gate/scans", tags=["M1 - Gate Core (Kiosk IScanSubmission)"])
    def submit_scan(req: SubmitScanRequest) -> dict[str, object]:
        """StationClient submits a QR scan through IScanSubmission (Flow A1–A7)."""
        res = scan_submission.submitScan(scanId=req.scanId, qrToken=req.qrToken, laneId=req.laneId)
        return {
            "scanId": res.scanId,
            "state": res.state.value,
            "laneId": res.laneId,
            "degradedFlag": res.degradedFlag,
            "reason": res.reason,
            "commandId": res.commandId,
        }

    @router.get("/api/gate/scans/{scan_id}", tags=["M1 - Gate Core (Kiosk IScanSubmission)"])
    def get_scan_status(scan_id: str) -> dict[str, object]:
        """StationClient queries stored scan status after a timeout using the same scanId."""
        res = scan_submission.getScanStatus(scanId=scan_id)
        if res is None:
            raise HTTPException(status_code=404, detail="SCAN_NOT_FOUND")
        return {
            "scanId": res.scanId,
            "state": res.state.value,
            "laneId": res.laneId,
            "degradedFlag": res.degradedFlag,
            "reason": res.reason,
        }

    @router.post("/api/controller/signals/passage", tags=["M1 - ControllerSignalHandler (Hardware Callbacks)"])
    def signal_passage(req: ControllerSignalRequest) -> dict[str, object]:
        """Controller reports PASSAGE event -> Gate Core records AttendanceTransaction (Flow A8, I-07)."""
        if not req.passageEventId:
            raise HTTPException(status_code=400, detail="passageEventId is required for PASSAGE signal")
        signal_handler.onPassage(
            passageEventId=req.passageEventId,
            commandId=req.commandId,
            laneId=req.laneId,
        )
        return {"status": "PASSAGE_PROCESSED", "passageEventId": req.passageEventId, "commandId": req.commandId}

    @router.post("/api/controller/signals/lane-clear", tags=["M1 - ControllerSignalHandler (Hardware Callbacks)"])
    def signal_lane_clear(req: ControllerSignalRequest) -> dict[str, object]:
        """Controller reports LANE_CLEAR -> Gate Core sends closeGate once and records CLOSED (Flow A9, I-05)."""
        signal_handler.onLaneClear(commandId=req.commandId, laneId=req.laneId)
        return {"status": "LANE_CLEAR_PROCESSED", "commandId": req.commandId}

    return router
