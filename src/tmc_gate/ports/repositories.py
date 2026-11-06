"""Unit of Work, Repository, and Cross-cutting port interfaces owned/used by M1 Gate Core (Figure 4.2, Table 4.7).

Transaction rule:
- IUnitOfWork owns begin(), commit(), and rollback() (and context-manager protocol `__enter__`/`__exit__`).
- Repositories receive the shared transaction context of IUnitOfWork and NEVER open or commit transactions on their own.
"""

from abc import ABC, abstractmethod
from types import TracebackType

from tmc_gate.domain.enums import DedupStatus
from tmc_gate.domain.models import (
    AttendanceTransaction,
    GateCommand,
    GateCycle,
    Lane,
    PassageEvent,
    ScanAttempt,
    SupportCase,
    TokenReservation,
)
from tmc_gate.domain.snapshot import EligibilitySnapshot


class IUnitOfWork(ABC):
    """Transaction boundary coordinator shared across repositories (ADR-02, ADR-05)."""

    @abstractmethod
    def begin(self, timeout_ms: int | None = None) -> None:
        """Start an atomic transaction (e.g., BEGIN IMMEDIATE with busy_timeout set from remaining deadline)."""

    @abstractmethod
    def commit(self) -> None:
        """Commit the current transaction."""

    @abstractmethod
    def rollback(self) -> None:
        """Roll back the current transaction."""

    def __enter__(self) -> "IUnitOfWork":
        self.begin()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.rollback()


class IScanRepository(ABC):
    """Persistence contract for scan states, active lane cycles, token claims, and controller commands."""

    @abstractmethod
    def get_scan(self, scanId: str) -> ScanAttempt | None:
        """Fetch an existing ScanAttempt by scanId."""

    @abstractmethod
    def save_scan(self, scan: ScanAttempt, audit_payload: dict[str, object] | None = None) -> None:
        """Stage a ScanAttempt (and optional local audit evidence) within the current UnitOfWork transaction."""

    @abstractmethod
    def get_lane(self, laneId: str) -> Lane | None:
        """Fetch lane configuration and active cycle reference."""

    @abstractmethod
    def get_active_cycle_for_lane(self, laneId: str) -> GateCycle | None:
        """Return the active or unresolved GateCycle on the lane, if any."""

    @abstractmethod
    def atomic_reserve_lane_claim_token_and_accept(
        self,
        scan: ScanAttempt,
        reservation: TokenReservation,
        cycle: GateCycle,
        command: GateCommand,
        timeout_ms: int | None = None,
    ) -> tuple[bool, str]:
        """Reserve the lane, claim tokenId, and stage ACCEPTED scan & GateCommand inside one short transaction (ADR-02, Flow A4).

        Returns:
            (True, "ACCEPTED") if staged/committed.
            (False, "LANE_BUSY") if the lane already has an active or unresolved GateCycle (token is NOT claimed).
            (False, "TOKEN_ALREADY_CLAIMED") if tokenId was already claimed at this factory.
            (False, "STORAGE_FAILURE") if busy_timeout expires or durable persistence fails.
        """

    @abstractmethod
    def update_opening_outcome(
        self,
        scanId: str,
        cycleId: str,
        commandId: str,
        scanState: str,
        commandOutcome: str,
        releaseLane: bool,
        outboxEvent: dict[str, object] | None = None,
        auditPayload: dict[str, object] | None = None,
    ) -> None:
        """Stage OPENED, CONTROLLER_ERROR, or UNDETERMINED outcome together with audit and outbox data."""

    @abstractmethod
    def get_cycle_by_command(self, commandId: str) -> GateCycle | None:
        """Find the GateCycle associated with a controller commandId."""

    @abstractmethod
    def get_cycle(self, cycleId: str) -> GateCycle | None:
        """Find a GateCycle by cycleId."""

    @abstractmethod
    def update_cycle(
        self,
        cycle: GateCycle,
        releaseLane: bool = False,
        command: GateCommand | None = None,
    ) -> None:
        """Stage updated passageStatus or closureStatus on a GateCycle (and optional GateCommand)."""

    @abstractmethod
    def save_support_case(
        self,
        case: SupportCase,
        outboxEvent: dict[str, object] | None = None,
        auditPayload: dict[str, object] | None = None,
    ) -> None:
        """Stage an assisted access or emergency SupportCase with audit evidence and outbox event (Flow B4)."""

    @abstractmethod
    def get_support_case(self, supportCaseId: str) -> SupportCase | None:
        """Retrieve a stored SupportCase by supportCaseId."""

    @abstractmethod
    def get_command(self, commandId: str) -> GateCommand | None:
        """Find a stored GateCommand by commandId (open, close, emergency), with its original issuedAt."""

    @abstractmethod
    def save_command(self, command: GateCommand) -> None:
        """Stage a GateCommand outcome as history only; never changes a GateCycle or the lane reservation."""

    @abstractmethod
    def get_command_by_support_case(self, supportCaseId: str, commandType: str) -> GateCommand | None:
        """Find the GateCommand issued for a supportCaseId and command type (OPEN, EMERGENCY_RELEASE).

        Gate Core uses it so a repeated assisted-open or emergency request never sends a second command (§4.2.3).
        """


class IAttendanceRepository(ABC):
    """Persistence contract for PassageEvent and original AttendanceTransaction records (Table 4.7)."""

    @abstractmethod
    def store_passage_and_attendance_atomic(
        self,
        passage: PassageEvent,
        transaction: AttendanceTransaction | None,
        cycle: GateCycle,
        outboxEvent: dict[str, object],
        auditPayload: dict[str, object],
    ) -> bool:
        """Stage PassageEvent (1 GateCycle -> 0..* PassageEvent), at most one original AttendanceTransaction per scanId,
        audit evidence, and OutboxEvent within the current UnitOfWork transaction (Flow A8, ADR-02).
        """

    @abstractmethod
    def get_by_scan_id(self, scanId: str) -> AttendanceTransaction | None:
        """Fetch the original AttendanceTransaction for a scanId, if created."""

    @abstractmethod
    def list_transactions(self) -> list[AttendanceTransaction]:
        """Return all stored factory attendance transactions."""

    def list_passages_for_cycle(self, cycleId: str) -> list[PassageEvent]:
        """Return all PassageEvents recorded for a GateCycle (1 -> 0..* multiplicity)."""
        return []


class IOutboxRepository(ABC):
    """Persistence contract for transactional outbox events and (consumer, eventId) deduplication (Table 4.7, ADR-05)."""

    @abstractmethod
    def append_event(self, event: dict[str, object]) -> None:
        """Stage an outgoing event in the factory outbox within the current UnitOfWork transaction."""

    @abstractmethod
    def list_pending_events(self) -> list[dict[str, object]]:
        """Return all committed outbox events waiting to be relayed by Site Sync."""

    def check_and_mark_processed(
        self,
        consumer: str,
        eventId: str,
        payload: dict[str, object],
    ) -> DedupStatus:
        """Record (consumer, eventId) in the current transaction (ADR-05):
        - First time -> DedupStatus.APPLIED
        - Same (consumer, eventId) with identical payload -> DedupStatus.DUPLICATE_IGNORED
        - Same (consumer, eventId) with different payload -> DedupStatus.CONFLICT_FLAGGED
        """
        return DedupStatus.APPLIED


class ISnapshotRepository(ABC):
    """Persistence contract for factory-local EligibilitySnapshot (Figure 4.2, Table 4.7)."""

    @abstractmethod
    def get_snapshot(self) -> EligibilitySnapshot:
        """Load the current factory EligibilitySnapshot."""

    @abstractmethod
    def save_snapshot(self, snapshot: EligibilitySnapshot) -> None:
        """Save an updated EligibilitySnapshot and watermark."""


class IAuditApi(ABC):
    """Cross-cutting asynchronous Audit interface (Figure 4.2, ADR-09)."""

    @abstractmethod
    def append_async(self, record: dict[str, object]) -> None:
        """Forward durable audit evidence asynchronously outside the 2-second scan path."""


class IHealthApi(ABC):
    """Cross-cutting asynchronous Monitoring/Health interface (Figure 4.2, Table 4.18)."""

    @abstractmethod
    def report_event_async(self, metric_or_alert: dict[str, object]) -> None:
        """Report operational latency, degraded mode transitions, or controller faults asynchronously."""


class IAuthorizationApi(ABC):
    """Cross-cutting Identity & RBAC check used by M3 Gate Intervention (Figure 4.4, ADR-09)."""

    @abstractmethod
    def is_authorized(self, actorId: str, role: str, action: str, factoryId: str) -> bool:
        """Verify whether the actor has server-side permission and scope for the requested operation."""


class IGuardIdentityApi(ABC):
    """Cross-cutting identity check: maps a server-verified guard session to a guardId (ADR-09).

    M3 takes the acting guard from this port, never from the request body.
    """

    @abstractmethod
    def resolve_guard(self, sessionToken: str) -> str | None:
        """Return the guardId of a valid session, or None when the session is unknown or expired."""
