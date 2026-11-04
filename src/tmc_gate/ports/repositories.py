"""Repository and cross-cutting port interfaces owned/used by M1 Gate Core (Figure 4.2, Table 4.7)."""

from abc import ABC, abstractmethod

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


class IScanRepository(ABC):
    """Persistence contract for scan states, active lane cycles, token claims, and controller commands."""

    @abstractmethod
    def get_scan(self, scanId: str) -> ScanAttempt | None:
        """Fetch an existing ScanAttempt by scanId."""

    @abstractmethod
    def save_scan(self, scan: ScanAttempt, audit_payload: dict[str, object] | None = None) -> None:
        """Persist a non-opening scan state (e.g., REJECTED, CANNOT_PROCESS) with local audit evidence."""

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
    ) -> tuple[bool, str]:
        """Atomically reserve the lane, claim tokenId, and store ACCEPTED in one transaction (ADR-02, Flow A4).

        Returns:
            (True, "ACCEPTED") if committed.
            (False, "LANE_BUSY") if the lane already has an active or unresolved GateCycle (token is NOT claimed).
            (False, "TOKEN_ALREADY_CLAIMED") if tokenId was already claimed at this factory.
            (False, "STORAGE_FAILURE") if durable persistence fails.
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
        """Persist OPENED, CONTROLLER_ERROR, or UNDETERMINED outcome together with audit and outbox data."""

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
        """Persist updated passageStatus or closureStatus on a GateCycle (and optional GateCommand)."""

    @abstractmethod
    def save_support_case(self, case: SupportCase) -> None:
        """Persist an assisted access or emergency SupportCase."""

    @abstractmethod
    def get_support_case(self, supportCaseId: str) -> SupportCase | None:
        """Retrieve a stored SupportCase by supportCaseId."""


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
        """Atomically store PassageEvent, at most one original AttendanceTransaction per scanId,
        audit evidence, and outbox event in one write (Flow A8, ADR-02).
        """

    @abstractmethod
    def get_by_scan_id(self, scanId: str) -> AttendanceTransaction | None:
        """Fetch the original AttendanceTransaction for a scanId, if created."""

    @abstractmethod
    def list_transactions(self) -> list[AttendanceTransaction]:
        """Return all stored factory attendance transactions."""


class IOutboxRepository(ABC):
    """Persistence contract for transactional outbox events destined for Gate Event Feed (Table 4.7, ADR-05)."""

    @abstractmethod
    def append_event(self, event: dict[str, object]) -> None:
        """Store an outgoing event in the factory outbox."""

    @abstractmethod
    def list_pending_events(self) -> list[dict[str, object]]:
        """Return all committed outbox events waiting to be relayed by Site Sync."""


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
