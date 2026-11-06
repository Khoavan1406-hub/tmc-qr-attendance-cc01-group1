"""Domain entities and value objects for the Gate Flow (§4.3, Figure 4.18, Table 4.24)."""

from dataclasses import dataclass, field
from datetime import datetime

from tmc_gate.domain.enums import (
    ClosureStatus,
    CommandOutcome,
    CommandType,
    Direction,
    OperatingMode,
    PassageStatus,
    ScanState,
)


@dataclass
class Lane:
    """Physical turnstile lane at a factory station (§4.3).

    Multiplicity rule: a Lane has at most one active or unresolved GateCycle at any time.
    """

    laneId: str
    stationId: str
    factoryId: str
    direction: Direction
    activeCycleId: str | None = None


@dataclass
class ScanAttempt:
    """Record of a QR scan attempt at a kiosk (§4.2.5 Table 4.23a, §4.3)."""

    scanId: str
    factoryId: str
    laneId: str
    state: ScanState
    createdAt: datetime
    tokenId: str | None = None
    employeeId: str | None = None
    degradedFlag: bool = False
    snapshotVersion: int = 0
    reason: str | None = None


@dataclass
class TokenReservation:
    """Single-use atomic reservation of a signed QR token within a factory (§4.3, §7.3.2, ADR-11).

    Stored in the same atomic transaction as ScanAttempt(ACCEPTED) before openGate is called.
    """

    tokenId: str
    scanId: str
    factoryId: str
    claimedAt: datetime
    expiresAt: datetime


@dataclass
class GateCycle:
    """Tracks passage and closure states for a single gate opening cycle (§4.2.5 Table 4.23b, §4.3).

    Stores passageStatus and closureStatus separately so closing never waits for the 15-second
    passage timer, and the 15-second timer never authorizes closeGate.
    """

    cycleId: str
    laneId: str
    passageStatus: PassageStatus
    closureStatus: ClosureStatus
    observationDeadline: datetime
    scanId: str | None = None
    supportCaseId: str | None = None
    laneClearReceived: bool = False
    closeCommandId: str | None = None

    def is_active_or_unresolved(self) -> bool:
        """Return True while the lane must remain reserved/busy (§4.2.2 Lane rules)."""
        return self.closureStatus != ClosureStatus.CLOSED


@dataclass
class GateCommand:
    """Controller command linked to a scan or support case and lane (§4.3, Table 4.24)."""

    commandId: str
    cycleId: str
    laneId: str
    commandType: CommandType
    outcome: CommandOutcome = CommandOutcome.SENT
    scanId: str | None = None
    supportCaseId: str | None = None
    issuedAt: datetime | None = None


@dataclass
class PassageEvent:
    """Confirmed physical passage signal reported by the turnstile controller (§4.3, I-01, I-07).

    Must be durably stored before an AttendanceTransaction is created.
    """

    passageEventId: str
    commandId: str
    cycleId: str
    laneId: str
    occurredAt: datetime


@dataclass
class AttendanceTransaction:
    """Original factory attendance record created only after a confirmed PassageEvent (§4.3, ADR-02).

    Data model enforces at most one original transaction per scanId and accepts each passageEventId once.
    """

    transactionId: str
    scanId: str
    passageEventId: str
    employeeId: str
    factoryId: str
    laneId: str
    direction: Direction
    occurredAt: datetime
    source: str = "QR_GATE"
    degradedFlag: bool = False
    snapshotVersion: int = 0


@dataclass
class SupportCase:
    """Assisted access or emergency intervention record submitted by a guard (§4.2.3 Flow B, ADR-07)."""

    supportCaseId: str
    factoryId: str
    laneId: str
    guardId: str
    reason: str
    occurredAt: datetime
    employeeId: str | None = None
    linkedScanId: str | None = None
    isEmergency: bool = False


@dataclass(frozen=True)
class TokenClaims:
    """Verified claims extracted from a signed, short-lived, factory-bound QR credential (§7.3.1, ADR-11)."""

    tokenId: str
    employeeId: str
    factoryId: str
    issuedAt: datetime
    expiresAt: datetime


@dataclass(frozen=True)
class DecisionResult:
    """Outcome returned by AccessDecision.evaluate() (§4.1.6, §4.2.2 step A3)."""

    allowed: bool
    operatingMode: OperatingMode
    snapshotVersion: int
    reason: str
    claims: TokenClaims | None = None
    direction: Direction | None = None


@dataclass
class ScanResult:
    """Response DTO returned to StationClient or callers of GateCoreApi."""

    scanId: str
    state: ScanState
    laneId: str
    degradedFlag: bool = False
    reason: str | None = None
    commandId: str | None = None
    extra: dict[str, object] = field(default_factory=dict)
