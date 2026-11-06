"""FastAPI application composition root (Dependency Injection wiring for M1 Gate Core, M3 Gate Intervention, and Demo helpers)."""

from datetime import UTC, datetime

from fastapi import FastAPI

from tmc_gate.adapters.cross_cutting_stubs import (
    InMemoryAuditStub,
    InMemoryAuthorizationStub,
    InMemoryHealthStub,
)
from tmc_gate.adapters.demo_guard_sessions import DemoGuardSessionStub
from tmc_gate.adapters.demo_qr_issuer import QrCredentialIssuance
from tmc_gate.adapters.sqlite_repos import (
    SqliteAttendanceRepository,
    SqliteOutboxRepository,
    SqliteScanRepository,
    SqliteSnapshotRepository,
    SqliteUnitOfWork,
    create_sqlite_connection,
    init_sqlite_schema,
)
from tmc_gate.adapters.turnstile_adapter import ControllerApiClient, SimulatedTurnstileAdapter
from tmc_gate.api.demo_controller import build_demo_router
from tmc_gate.api.gate_controller import build_gate_core_router
from tmc_gate.api.intervention_controller import build_intervention_router
from tmc_gate.application.access_decision import AccessDecision
from tmc_gate.application.facade import GateCoreFacade
from tmc_gate.application.intervention import GateInterventionFacade, InterventionService
from tmc_gate.application.processor import GateTransactionProcessor
from tmc_gate.domain.enums import Direction
from tmc_gate.domain.models import Lane
from tmc_gate.domain.snapshot import EligibilitySnapshot, EmployeeRight


def create_app(
    db_path: str = ":memory:",
    factory_id: str = "BD",
    demo_mode: bool = True,
) -> FastAPI:
    """Wire ports and adapters using Constructor Dependency Injection (DIP) and return the FastAPI app.

    Simulator / QR issuance endpoints in demo_controller are mounted ONLY when demo_mode=True.
    """
    conn = create_sqlite_connection(db_path)
    init_sqlite_schema(conn)
    uow = SqliteUnitOfWork(conn)

    # Demo QR issuer (M4 component) holding the central Ed25519 private key
    qr_issuer = QrCredentialIssuance()

    # Initial factory EligibilitySnapshot holding only the Ed25519 public key (ADR-06, ADR-11)
    initial_snapshot = EligibilitySnapshot(
        factoryId=factory_id,
        snapshotVersion=1,
        confirmedWatermark=datetime.now(UTC),
        clockErrorBoundSec=0.5,
        isCompleteAndContinuous=True,
        publicKeyPem=qr_issuer.public_key_pem,
        verificationKeys=(qr_issuer.public_key_pem,),
        employees={
            "EMP-001": EmployeeRight(
                employeeId="EMP-001",
                active=True,
                allowedFactories={"BD", "LA"},
                allowedLanes={"LANE-BD-01", "LANE-BD-02"},
            ),
            "EMP-002": EmployeeRight(
                employeeId="EMP-002",
                active=True,
                allowedFactories={"BD"},
                allowedLanes={"LANE-BD-01", "LANE-BD-02"},
            ),
            "EMP-SUSPENDED": EmployeeRight(
                employeeId="EMP-SUSPENDED",
                active=False,
                allowedFactories={"BD"},
                allowedLanes={"LANE-BD-01"},
            ),
        },
        lanes={
            "LANE-BD-01": Direction.ENTRY,
            "LANE-BD-02": Direction.EXIT,
        },
    )

    # Persistence & Cross-cutting Adapters
    scan_repo = SqliteScanRepository(conn)
    scan_repo.seed_lane(Lane(laneId="LANE-BD-01", stationId="ST-BD-01", factoryId=factory_id, direction=Direction.ENTRY))
    scan_repo.seed_lane(Lane(laneId="LANE-BD-02", stationId="ST-BD-01", factoryId=factory_id, direction=Direction.EXIT))

    attendance_repo = SqliteAttendanceRepository(conn)
    outbox_repo = SqliteOutboxRepository(conn)
    snapshot_repo = SqliteSnapshotRepository(initial_snapshot)

    audit_stub = InMemoryAuditStub()
    health_stub = InMemoryHealthStub()
    auth_stub = InMemoryAuthorizationStub()

    # M2 Turnstile Integration Adapter
    controller_client = ControllerApiClient()
    turnstile_adapter = SimulatedTurnstileAdapter(controller_client)

    # M1 Gate Core Business Components
    access_decision = AccessDecision(snapshot_repo=snapshot_repo)
    processor = GateTransactionProcessor(
        factory_id=factory_id,
        access_decision=access_decision,
        scan_repo=scan_repo,
        attendance_repo=attendance_repo,
        open_gate_port=turnstile_adapter,
        close_gate_port=turnstile_adapter,
        emergency_gate_port=turnstile_adapter,
        audit_api=audit_stub,
        health_api=health_stub,
        uow=uow,
        status_query_port=turnstile_adapter,
        snapshot_repo=snapshot_repo,
    )
    gate_core_facade = GateCoreFacade(
        processor=processor,
        scan_repo=scan_repo,
        snapshot_repo=snapshot_repo,
        outbox_repo=outbox_repo,
        uow=uow,
    )

    # M3 Gate Intervention Business Components (injected only with narrow IInterventionRequest)
    intervention_service = InterventionService(
        factory_id=factory_id,
        intervention_port=gate_core_facade,
        auth_api=auth_stub,
        audit_api=audit_stub,
    )
    intervention_facade = GateInterventionFacade(service=intervention_service)

    fastapi_app = FastAPI(
        title="TMC QR Attendance & Access Control – Factory Gate Application (M1 & M3)",
        description=(
            "Milestone 2 Task 2 Implementation adhering to SOLID principles and Ports & Adapters architecture. "
            "Implements M1 Gate Core and M3 Gate Intervention (with M2 Turnstile Simulator & M4 QR Issuer for demo)."
        ),
        version="1.0.0",
    )

    # Attach state references for test inspection
    fastapi_app.state.uow = uow
    fastapi_app.state.qr_issuer = qr_issuer
    fastapi_app.state.scan_repo = scan_repo
    fastapi_app.state.attendance_repo = attendance_repo
    fastapi_app.state.outbox_repo = outbox_repo
    fastapi_app.state.snapshot_repo = snapshot_repo
    fastapi_app.state.controller_client = controller_client
    fastapi_app.state.processor = processor
    fastapi_app.state.gate_core_facade = gate_core_facade
    fastapi_app.state.demo_mode = demo_mode

    # Register Entry Layer Routers
    fastapi_app.include_router(build_gate_core_router(scan_submission=gate_core_facade, signal_handler=processor))
    # Guard sign-in is simulated in M2 (fixed demo sessions); real identity provider is out of scope.
    fastapi_app.include_router(
        build_intervention_router(facade=intervention_facade, guard_identity=DemoGuardSessionStub())
    )
    if demo_mode:
        fastapi_app.include_router(
            build_demo_router(
                qr_issuer=qr_issuer,
                snapshot_repo=snapshot_repo,
                factory_updates=gate_core_facade,
                attendance_repo=attendance_repo,
                controller_client=controller_client,
            )
        )

    return fastapi_app


app = create_app()
