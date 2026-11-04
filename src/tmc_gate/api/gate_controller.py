"""[PACKAGE 3] Entry layer controllers for M1 Gate Core: GateCoreController & ControllerSignalHandler (Figure 4.2, Table 4.7)."""

from fastapi import APIRouter
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
        """StationClient submits a QR scan through IScanSubmission (Flow A1–A7).

        TODO [Package 3]:
        Call `scan_submission.submitScan(scanId=req.scanId, qrToken=req.qrToken, laneId=req.laneId)`
        and return dict with keys: `scanId`, `state`, `laneId`, `degradedFlag`, `reason`, `commandId`.
        """
        raise NotImplementedError("TODO [Package 3]: Implement POST /api/gate/scans")

    @router.get("/api/gate/scans/{scan_id}", tags=["M1 - Gate Core (Kiosk IScanSubmission)"])
    def get_scan_status(scan_id: str) -> dict[str, object]:
        """StationClient queries stored scan status after a timeout using the same scanId.

        TODO [Package 3]:
        Call `scan_submission.getScanStatus(scanId=scan_id)`. Raise HTTP 404 if `None`,
        otherwise return dict with `scanId`, `state`, `laneId`, `degradedFlag`, `reason`.
        """
        raise NotImplementedError("TODO [Package 3]: Implement GET /api/gate/scans/{scan_id}")

    @router.post("/api/controller/signals/passage", tags=["M1 - ControllerSignalHandler (Hardware Callbacks)"])
    def signal_passage(req: ControllerSignalRequest) -> dict[str, object]:
        """Controller reports PASSAGE event -> Gate Core records AttendanceTransaction (Flow A8, I-07).

        TODO [Package 3]:
        Validate `req.passageEventId` is present (else HTTP 400), call `signal_handler.onPassage(...)`,
        and return confirmation dict.
        """
        raise NotImplementedError("TODO [Package 3]: Implement POST /api/controller/signals/passage")

    @router.post("/api/controller/signals/lane-clear", tags=["M1 - ControllerSignalHandler (Hardware Callbacks)"])
    def signal_lane_clear(req: ControllerSignalRequest) -> dict[str, object]:
        """Controller reports LANE_CLEAR -> Gate Core sends closeGate once and records CLOSED (Flow A9, I-05).

        TODO [Package 3]:
        Call `signal_handler.onLaneClear(commandId=req.commandId, laneId=req.laneId)` and return confirmation dict.
        """
        raise NotImplementedError("TODO [Package 3]: Implement POST /api/controller/signals/lane-clear")

    return router
