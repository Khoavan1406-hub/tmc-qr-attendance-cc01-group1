"""[PACKAGE 5] Demo helper endpoints for Swagger UI (/docs): issue Ed25519 QR codes, simulate WAN outage, and inspect records."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from tmc_gate.adapters.demo_qr_issuer import QrCredentialIssuance
from tmc_gate.adapters.turnstile_adapter import ControllerApiClient
from tmc_gate.ports.inbound import IFactoryUpdates
from tmc_gate.ports.repositories import IAttendanceRepository, ISnapshotRepository


class IssueQrRequest(BaseModel):
    employeeId: str = Field("EMP-001", examples=["EMP-001", "EMP-002", "EMP-SUSPENDED"])
    factoryId: str = Field("BD", examples=["BD", "LA"], description="BD (Binh Duong) or LA (Long An)")
    lifetimeSec: int = Field(60, description="QR token validity in seconds (default 60s per ADR-11)")


class WatermarkAgeSimulationRequest(BaseModel):
    watermarkAgeSeconds: float = Field(
        10.0,
        examples=[10.0, 120.0, 2000.0],
        description="<=60s: NORMAL | 61s..1800s: DEGRADED | >1800s: CANNOT_PROCESS (I-10)",
    )
    clockErrorBoundSec: float = Field(0.5, description="Clock error bound in seconds (must be <= 2.0s)")


class ControllerSimulationModeRequest(BaseModel):
    openMode: str = Field("OPEN_CONFIRMED", examples=["OPEN_CONFIRMED", "TIMEOUT", "ERROR"])
    closeMode: str = Field("CLOSED", examples=["CLOSED", "TIMEOUT", "ERROR"])


def build_demo_router(
    qr_issuer: QrCredentialIssuance,
    snapshot_repo: ISnapshotRepository,
    factory_updates: IFactoryUpdates,
    attendance_repo: IAttendanceRepository,
    controller_client: ControllerApiClient,
) -> APIRouter:
    """Create Swagger UI demo helper endpoints."""
    router = APIRouter()

    @router.post("/api/demo/qr-tokens", tags=["Demo Helpers (M4 QR Issuer & Fault Simulation)"])
    def issue_demo_qr(req: IssueQrRequest) -> dict[str, object]:
        """TODO [Package 5]: Call `qr_issuer.issue_qr_token(...)` and return `{"qrToken": token, "claims": claims}`."""
        raise NotImplementedError("TODO [Package 5]: Implement POST /api/demo/qr-tokens")

    @router.post("/api/demo/watermark-age", tags=["Demo Helpers (M4 QR Issuer & Fault Simulation)"])
    def simulate_wan_watermark_age(req: WatermarkAgeSimulationRequest) -> dict[str, object]:
        """TODO [Package 5]: Update `confirmedWatermark = now - timedelta(seconds=req.watermarkAgeSeconds)`
        on the snapshot, apply via `factory_updates.applyEligibilityChanges(snap)`, and return the new `operatingMode`.
        """
        raise NotImplementedError("TODO [Package 5]: Implement POST /api/demo/watermark-age")

    @router.post("/api/demo/controller-mode", tags=["Demo Helpers (M4 QR Issuer & Fault Simulation)"])
    def configure_controller_simulator(req: ControllerSimulationModeRequest) -> dict[str, str]:
        """TODO [Package 5]: Update `controller_client.simulated_open_mode` and `simulated_close_mode`."""
        raise NotImplementedError("TODO [Package 5]: Implement POST /api/demo/controller-mode")

    @router.get("/api/demo/attendance-transactions", tags=["Demo Helpers (M4 QR Issuer & Fault Simulation)"])
    def list_factory_attendance() -> list[dict[str, object]]:
        """TODO [Package 5]: Return serialized list of transactions from `attendance_repo.list_transactions()`."""
        raise NotImplementedError("TODO [Package 5]: Implement GET /api/demo/attendance-transactions")

    return router
