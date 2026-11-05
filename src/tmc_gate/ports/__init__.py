"""Ports and Public Interfaces owned by M1 Gate Core (§4.2.1 Table 4.20, §7.1.4 ISP, §7.1.5 DIP).

Must ONLY import from tmc_gate.domain.
"""

from tmc_gate.ports.inbound import (
    GateCoreApi,
    GateInterventionApi,
    IFactoryUpdates,
    IInterventionRequest,
    IScanSubmission,
)
from tmc_gate.ports.repositories import (
    IAttendanceRepository,
    IAuditApi,
    IAuthorizationApi,
    IHealthApi,
    IOutboxRepository,
    IScanRepository,
    ISnapshotRepository,
    IUnitOfWork,
)
from tmc_gate.ports.turnstile import (
    ICloseGate,
    IControllerStatusQuery,
    IEmergencyGate,
    IOpenGate,
    IPassageEvents,
    ITurnstileControl,
)

__all__ = [
    "IScanSubmission",
    "IInterventionRequest",
    "IFactoryUpdates",
    "GateCoreApi",
    "GateInterventionApi",
    "IOpenGate",
    "ICloseGate",
    "IEmergencyGate",
    "IPassageEvents",
    "IControllerStatusQuery",
    "ITurnstileControl",
    "IUnitOfWork",
    "IScanRepository",
    "IAttendanceRepository",
    "IOutboxRepository",
    "ISnapshotRepository",
    "IAuditApi",
    "IHealthApi",
    "IAuthorizationApi",
]
