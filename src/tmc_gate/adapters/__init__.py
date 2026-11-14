"""Adapters layer implementing M1 repositories, M2 Turnstile Integration, and M4 Demo QR Issuance."""

from tmc_gate.adapters.cross_cutting_stubs import (
    InMemoryAuditStub,
    InMemoryAuthorizationStub,
    InMemoryHealthStub,
)
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
from tmc_gate.adapters.turnstile_adapter import (
    ControllerApiClient,
    FaultyTurnstileAdapter,
    SimulatedTurnstileAdapter,
    TurnstileAdapter,
)

__all__ = [
    "create_sqlite_connection",
    "init_sqlite_schema",
    "SqliteUnitOfWork",
    "SqliteScanRepository",
    "SqliteAttendanceRepository",
    "SqliteOutboxRepository",
    "SqliteSnapshotRepository",
    "ControllerApiClient",
    "SimulatedTurnstileAdapter",
    "FaultyTurnstileAdapter",
    "TurnstileAdapter",
    "QrCredentialIssuance",
    "InMemoryAuditStub",
    "InMemoryHealthStub",
    "InMemoryAuthorizationStub",
]
