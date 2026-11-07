"""[PACKAGE 5] Demo helper endpoints for Swagger UI (/docs): issue Ed25519 QR codes, simulate WAN outage, and inspect records.

Mounted by create_app() ONLY when demo_mode=True. These endpoints are demo helpers, not part of M1/M3:
- QR issuance stands in for the central M4 QrCredentialIssuance (the private key never reaches factory code).
- Watermark ageing simulates a WAN outage: no snapshot update arrives, so the confirmed watermark grows old.
  It writes the snapshot holder directly on purpose; IFactoryUpdates.applyEligibilityChanges() refuses to move a
  watermark backwards (§5.7.2), which is correct for Site Sync but would make the outage impossible to simulate.
"""

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from tmc_gate.adapters.demo_qr_issuer import QrCredentialIssuance
from tmc_gate.adapters.turnstile_adapter import ControllerApiClient
from tmc_gate.ports.repositories import IAttendanceRepository, ISnapshotRepository

DEMO_TAG = "Demo Helpers (M4 QR Issuer & Fault Simulation)"

OpenMode = Literal["OPEN_CONFIRMED", "RECEIPT_ONLY", "TIMEOUT", "ERROR", "NETWORK_ERROR"]
CloseMode = Literal["CLOSED", "RECEIPT_ONLY", "TIMEOUT", "ERROR", "NETWORK_ERROR"]
EmergencyMode = Literal["EMERGENCY_RELEASED", "RECEIPT_ONLY", "TIMEOUT", "ERROR", "NETWORK_ERROR"]
ResetMode = Literal["RESET_CONFIRMED", "RECEIPT_ONLY", "TIMEOUT", "ERROR", "NETWORK_ERROR"]
QueryMode = Literal["UNDETERMINED", "CONFIRMED", "ERROR", "NETWORK_ERROR"]

_MODE_HELP = (
    "Confirmed value -> command executed | ERROR -> controller confirms NOT executed (CONTROLLER_ERROR) | "
    "RECEIPT_ONLY, TIMEOUT, NETWORK_ERROR -> UNDETERMINED"
)


class IssueQrRequest(BaseModel):
    employeeId: str = Field("EMP-001", examples=["EMP-001", "EMP-002", "EMP-SUSPENDED"])
    factoryId: str = Field("BD", examples=["BD", "LA"], description="BD (Binh Duong) or LA (Long An)")
    lifetimeSec: int = Field(60, gt=0, description="QR token validity in seconds (default 60s per ADR-11)")


class WatermarkAgeSimulationRequest(BaseModel):
    watermarkAgeSeconds: float = Field(
        10.0,
        ge=0,
        examples=[10.0, 120.0, 2000.0],
        description="<= 60 s: NORMAL | > 60 s and < 1800 s: DEGRADED | >= 1800 s: CANNOT_PROCESS (I-10)",
    )
    clockErrorBoundSec: float = Field(
        0.5,
        ge=0,
        description="Clock error bound in seconds; must be < 2.0 s, otherwise CANNOT_PROCESS",
    )


class ControllerSimulationModeRequest(BaseModel):
    openMode: OpenMode = Field("OPEN_CONFIRMED", description=_MODE_HELP)
    closeMode: CloseMode = Field("CLOSED", description=_MODE_HELP)
    emergencyMode: EmergencyMode = Field("EMERGENCY_RELEASED", description=_MODE_HELP)
    resetMode: ResetMode = Field("RESET_CONFIRMED", description=_MODE_HELP)
    queryStatus: QueryMode = Field(
        "UNDETERMINED",
        description="Reply to a status query after UNDETERMINED: CONFIRMED -> the earlier command was executed",
    )
    latencyMs: int = Field(
        0,
        ge=0,
        description="Simulated controller reply time; above the command timeout (700 ms for Flow A) -> UNDETERMINED",
    )


def build_demo_router(
    qr_issuer: QrCredentialIssuance,
    snapshot_repo: ISnapshotRepository,
    attendance_repo: IAttendanceRepository,
    controller_client: ControllerApiClient,
) -> APIRouter:
    """Create Swagger UI demo helper endpoints."""
    router = APIRouter()

    @router.post("/api/demo/qr-tokens", tags=[DEMO_TAG])
    def issue_demo_qr(req: IssueQrRequest) -> dict[str, object]:
        """Issue a signed, 60-second, factory-bound QR token as the central issuer would (demo only)."""
        try:
            token, claims = qr_issuer.issue_qr_token(
                employee_id=req.employeeId,
                factory_id=req.factoryId,
                lifetime_sec=req.lifetimeSec,
            )
        except NotImplementedError as exc:
            raise HTTPException(status_code=501, detail=f"QR issuer not available: {exc}") from exc
        return {"qrToken": token, "claims": claims}

    @router.post("/api/demo/watermark-age", tags=[DEMO_TAG])
    def simulate_wan_watermark_age(req: WatermarkAgeSimulationRequest) -> dict[str, object]:
        """Simulate a WAN outage by ageing the confirmed watermark, then report the resulting operating mode."""
        now = datetime.now(UTC)
        current = snapshot_repo.get_snapshot()
        aged = replace(
            current,
            confirmedWatermark=now - timedelta(seconds=req.watermarkAgeSeconds),
            clockErrorBoundSec=req.clockErrorBoundSec,
        )
        snapshot_repo.save_snapshot(aged)
        return {
            "watermarkAgeSeconds": req.watermarkAgeSeconds,
            "clockErrorBoundSec": req.clockErrorBoundSec,
            "confirmedWatermark": aged.confirmedWatermark.isoformat(),
            "operatingMode": aged.getOperatingMode(now).value,
        }

    @router.post("/api/demo/controller-mode", tags=[DEMO_TAG])
    def configure_controller_simulator(req: ControllerSimulationModeRequest) -> dict[str, object]:
        """Choose how the simulated turnstile controller answers each command."""
        controller_client.simulated_open_mode = req.openMode
        controller_client.simulated_close_mode = req.closeMode
        controller_client.simulated_emergency_mode = req.emergencyMode
        controller_client.simulated_reset_mode = req.resetMode
        controller_client.simulated_query_status = req.queryStatus
        controller_client.simulated_latency_ms = req.latencyMs
        return req.model_dump()

    @router.get("/api/demo/attendance-transactions", tags=[DEMO_TAG])
    def list_factory_attendance() -> list[dict[str, object]]:
        """List factory attendance transactions (created only after a confirmed PASSAGE)."""
        try:
            transactions = attendance_repo.list_transactions()
        except NotImplementedError as exc:
            raise HTTPException(status_code=501, detail=f"Attendance repository not available: {exc}") from exc
        return [jsonable_encoder(asdict(tx)) for tx in transactions]

    return router
