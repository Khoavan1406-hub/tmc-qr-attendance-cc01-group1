"""Domain package for M1 Gate Core (§4.2.5, §4.3).

Pure domain layer: must NOT import from ports, application, adapters, or api.
"""

from tmc_gate.domain.enums import (
    ClosureStatus,
    CommandOutcome,
    CommandType,
    Direction,
    OperatingMode,
    PassageStatus,
    ScanState,
)
from tmc_gate.domain.models import (
    AttendanceTransaction,
    DecisionResult,
    GateCommand,
    GateCycle,
    Lane,
    PassageEvent,
    ScanAttempt,
    ScanResult,
    SupportCase,
    TokenClaims,
    TokenReservation,
)
from tmc_gate.domain.snapshot import EligibilitySnapshot

__all__ = [
    "ScanState",
    "PassageStatus",
    "ClosureStatus",
    "OperatingMode",
    "Direction",
    "CommandType",
    "CommandOutcome",
    "Lane",
    "ScanAttempt",
    "TokenReservation",
    "GateCycle",
    "GateCommand",
    "PassageEvent",
    "AttendanceTransaction",
    "SupportCase",
    "TokenClaims",
    "DecisionResult",
    "ScanResult",
    "EligibilitySnapshot",
]
