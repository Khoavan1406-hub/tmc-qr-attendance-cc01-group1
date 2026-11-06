"""[PACKAGE 4] SQLite Unit of Work and Persistence adapters for M1 Gate Core (Figure 4.2, Table 4.7, §5.6.1, ADR-02, ADR-05, ADR-11).

Transaction rules:
- SqliteUnitOfWork implements IUnitOfWork (begin / commit / rollback).
- Every repository receives the transaction context from IUnitOfWork and NEVER opens or commits transactions on its own.
- Supports 1 GateCycle -> 0..* PassageEvent (each passageEventId accepted once; at most 1 original AttendanceTransaction per scanId).
- Supports simulated consumer deduplication keyed by (consumer, eventId) with payload conflict detection.
"""

from __future__ import annotations

import sqlite3
import threading

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
from tmc_gate.ports.repositories import (
    IAttendanceRepository,
    IOutboxRepository,
    IScanRepository,
    ISnapshotRepository,
    IUnitOfWork,
)


def create_sqlite_connection(db_path: str = ":memory:", busy_timeout_ms: int = 2_000) -> sqlite3.Connection:
    """Create a thread-safe SQLite connection configured for explicit UnitOfWork transactions."""
    conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None, timeout=busy_timeout_ms / 1000.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)};")
    if db_path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_sqlite_schema(conn: sqlite3.Connection) -> None:
    """Create all factory database tables and unique constraints required by §4.2.5 and §5.6.1."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS lanes (
            lane_id TEXT PRIMARY KEY,
            station_id TEXT NOT NULL,
            factory_id TEXT NOT NULL,
            direction TEXT NOT NULL,
            active_cycle_id TEXT UNIQUE
        );

        CREATE TABLE IF NOT EXISTS scan_attempts (
            scan_id TEXT PRIMARY KEY,
            token_id TEXT,
            employee_id TEXT,
            factory_id TEXT NOT NULL,
            lane_id TEXT NOT NULL,
            state TEXT NOT NULL,
            degraded_flag INTEGER NOT NULL DEFAULT 0,
            snapshot_version INTEGER NOT NULL DEFAULT 0,
            reason TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS token_reservations (
            token_id TEXT PRIMARY KEY,
            scan_id TEXT NOT NULL UNIQUE,
            factory_id TEXT NOT NULL,
            claimed_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS gate_cycles (
            cycle_id TEXT PRIMARY KEY,
            lane_id TEXT NOT NULL,
            scan_id TEXT,
            support_case_id TEXT,
            passage_status TEXT NOT NULL,
            closure_status TEXT NOT NULL,
            observation_deadline TEXT NOT NULL,
            lane_clear_received INTEGER NOT NULL DEFAULT 0
            -- TODO(pkg4): add close_command_id TEXT and save/load GateCycle.closeCommandId,
            -- so a repeated LANE_CLEAR after restart queries the stored close command instead of sending a new one.
        );

        CREATE TABLE IF NOT EXISTS gate_commands (
            command_id TEXT PRIMARY KEY,
            cycle_id TEXT NOT NULL,
            lane_id TEXT NOT NULL,
            scan_id TEXT,
            support_case_id TEXT,
            command_type TEXT NOT NULL,
            outcome TEXT NOT NULL,
            issued_at TEXT
        );

        CREATE TABLE IF NOT EXISTS passage_events (
            passage_event_id TEXT PRIMARY KEY,
            command_id TEXT NOT NULL,
            cycle_id TEXT NOT NULL,
            lane_id TEXT NOT NULL,
            occurred_at TEXT NOT NULL
            -- TODO(pkg4): persist the LATE_PASSAGE review flag (e.g. late_flag / review_reason column)
            -- durably, not only in memory; shape is fixed in PR2.
        );

        CREATE TABLE IF NOT EXISTS attendance_transactions (
            transaction_id TEXT PRIMARY KEY,
            scan_id TEXT NOT NULL UNIQUE,
            passage_event_id TEXT NOT NULL UNIQUE,
            employee_id TEXT NOT NULL,
            factory_id TEXT NOT NULL,
            lane_id TEXT NOT NULL,
            direction TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            source TEXT NOT NULL,
            degraded_flag INTEGER NOT NULL DEFAULT 0,
            snapshot_version INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS support_cases (
            support_case_id TEXT PRIMARY KEY,
            factory_id TEXT NOT NULL,
            lane_id TEXT NOT NULL,
            guard_id TEXT NOT NULL,
            employee_id TEXT,
            reason TEXT NOT NULL,
            linked_scan_id TEXT,
            is_emergency INTEGER NOT NULL DEFAULT 0,
            occurred_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS outbox_events (
            event_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS local_audit_evidence (
            audit_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS processed_events (
            consumer TEXT NOT NULL,
            event_id TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            conflict_flagged INTEGER NOT NULL DEFAULT 0,
            processed_at TEXT NOT NULL,
            PRIMARY KEY (consumer, event_id)
        );
        """
    )


class SqliteUnitOfWork(IUnitOfWork):
    """Concrete SQLite Unit of Work owning begin / commit / rollback on a connection.

    TODO [Package 4]:
    Implement `begin(timeout_ms)`, `commit()`, and `rollback()` using `BEGIN IMMEDIATE`, `COMMIT`, and `ROLLBACK`.
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock | None = None) -> None:
        self._conn = conn
        self._lock = lock or threading.RLock()
        self._in_tx = False

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def begin(self, timeout_ms: int | None = None) -> None:
        """TODO [Package 4]: Set `PRAGMA busy_timeout` if `timeout_ms` is given and execute `BEGIN IMMEDIATE`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteUnitOfWork.begin")

    def commit(self) -> None:
        """TODO [Package 4]: Execute `COMMIT`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteUnitOfWork.commit")

    def rollback(self) -> None:
        """TODO [Package 4]: Execute `ROLLBACK`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteUnitOfWork.rollback")


class SqliteScanRepository(IScanRepository):
    """SQLite implementation of IScanRepository operating within SqliteUnitOfWork's transaction context."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock | None = None) -> None:
        self._conn = conn
        self._lock = lock or threading.RLock()
        self.simulate_write_failure: bool = False

    def seed_lane(self, lane: Lane) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO lanes (lane_id, station_id, factory_id, direction, active_cycle_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (lane.laneId, lane.stationId, lane.factoryId, lane.direction.value, lane.activeCycleId),
            )

    def get_command(self, commandId: str) -> GateCommand | None:
        raise NotImplementedError("TODO [Package 4]: Implement SqliteScanRepository.get_command (keep issued_at)")

    def save_command(self, command: GateCommand) -> None:
        raise NotImplementedError("TODO [Package 4]: Implement SqliteScanRepository.save_command (history only)")

    def get_scan(self, scanId: str) -> ScanAttempt | None:
        """TODO [Package 4]: Query `scan_attempts` by `scan_id` and return `ScanAttempt` or `None`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteScanRepository.get_scan")

    def save_scan(self, scan: ScanAttempt, audit_payload: dict[str, object] | None = None) -> None:
        """TODO [Package 4]: Stage `ScanAttempt` (and optional `audit_payload`) inside the active UnitOfWork transaction."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteScanRepository.save_scan")

    def get_lane(self, laneId: str) -> Lane | None:
        """TODO [Package 4]: Query `lanes` by `lane_id` and return `Lane` or `None`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteScanRepository.get_lane")

    def get_active_cycle_for_lane(self, laneId: str) -> GateCycle | None:
        """TODO [Package 4]: Look up `active_cycle_id` on `lanes` and return the corresponding `GateCycle`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteScanRepository.get_active_cycle_for_lane")

    def atomic_reserve_lane_claim_token_and_accept(
        self,
        scan: ScanAttempt,
        reservation: TokenReservation,
        cycle: GateCycle,
        command: GateCommand,
        timeout_ms: int | None = None,
    ) -> tuple[bool, str]:
        """Execute Flow A4 inside the active UnitOfWork transaction (ADR-02, ADR-11).

        TODO [Package 4]:
        - If lane is busy -> return `(False, "LANE_BUSY")` without claiming `tokenId`.
        - If `tokenId` is already claimed -> return `(False, "TOKEN_ALREADY_CLAIMED")`.
        - If `busy_timeout` expires or write fails -> return `(False, "STORAGE_FAILURE")`.
        - Otherwise stage `token_reservations`, `gate_cycles`, `lanes.active_cycle_id`, `scan_attempts`, and `gate_commands`
          and return `(True, "ACCEPTED")`.
        """
        raise NotImplementedError("TODO [Package 4]: Implement SqliteScanRepository.atomic_reserve_lane_claim_token_and_accept")

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
        """TODO [Package 4]: Update `scan_attempts.state`, `gate_commands.outcome`, release lane if requested,
        and insert `outboxEvent` / `auditPayload` inside the active UnitOfWork transaction.
        """
        raise NotImplementedError("TODO [Package 4]: Implement SqliteScanRepository.update_opening_outcome")

    def get_cycle_by_command(self, commandId: str) -> GateCycle | None:
        """TODO [Package 4]: Look up `cycle_id` in `gate_commands` by `command_id` and return `GateCycle`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteScanRepository.get_cycle_by_command")

    def get_cycle(self, cycleId: str) -> GateCycle | None:
        """TODO [Package 4]: Query `gate_cycles` by `cycle_id` and return `GateCycle` or `None`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteScanRepository.get_cycle")

    def update_cycle(
        self,
        cycle: GateCycle,
        releaseLane: bool = False,
        command: GateCommand | None = None,
    ) -> None:
        """TODO [Package 4]: Upsert `gate_cycles`, optionally upsert `gate_commands`, and update `lanes.active_cycle_id`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteScanRepository.update_cycle")

    def save_support_case(
        self,
        case: SupportCase,
        outboxEvent: dict[str, object] | None = None,
        auditPayload: dict[str, object] | None = None,
    ) -> None:
        """TODO [Package 4]: Stage `SupportCase` (and optional `outboxEvent`, `auditPayload`) in `support_cases`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteScanRepository.save_support_case")

    def get_support_case(self, supportCaseId: str) -> SupportCase | None:
        """TODO [Package 4]: Query `support_cases` by `support_case_id`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteScanRepository.get_support_case")


class SqliteAttendanceRepository(IAttendanceRepository):
    """SQLite implementation of IAttendanceRepository enforcing unique passageEventId and scanId."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock | None = None) -> None:
        self._conn = conn
        self._lock = lock or threading.RLock()

    def store_passage_and_attendance_atomic(
        self,
        passage: PassageEvent,
        transaction: AttendanceTransaction | None,
        cycle: GateCycle,
        outboxEvent: dict[str, object],
        auditPayload: dict[str, object],
    ) -> bool:
        """TODO [Package 4]:
        1. Ignore duplicate `passage.passageEventId` (return False if already in `passage_events`).
        2. Insert `passage_events` (a GateCycle may have multiple PassageEvents), update `gate_cycles.passage_status`,
           and if `transaction is not None` insert at most one `attendance_transactions` row per `scan_id`.
        3. Insert `outbox_events` and `local_audit_evidence` in the same UnitOfWork transaction.
        """
        raise NotImplementedError("TODO [Package 4]: Implement SqliteAttendanceRepository.store_passage_and_attendance_atomic")

    def get_by_scan_id(self, scanId: str) -> AttendanceTransaction | None:
        """TODO [Package 4]: Query `attendance_transactions` by `scan_id`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteAttendanceRepository.get_by_scan_id")

    def list_transactions(self) -> list[AttendanceTransaction]:
        """TODO [Package 4]: Return all rows from `attendance_transactions` ordered by `occurred_at`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteAttendanceRepository.list_transactions")


class SqliteOutboxRepository(IOutboxRepository):
    """SQLite implementation of IOutboxRepository and simulated consumer deduplication (Table 4.7, ADR-05)."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock | None = None) -> None:
        self._conn = conn
        self._lock = lock or threading.RLock()

    def append_event(self, event: dict[str, object]) -> None:
        """TODO [Package 4]: Stage event JSON in `outbox_events`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteOutboxRepository.append_event")

    def list_pending_events(self) -> list[dict[str, object]]:
        """TODO [Package 4]: Return all events from `outbox_events`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteOutboxRepository.list_pending_events")

    def check_and_mark_processed(
        self,
        consumer: str,
        eventId: str,
        payload: dict[str, object],
    ) -> DedupStatus:
        """TODO [Package 4]: Simulated consumer deduplication keyed by `(consumer, eventId)` in `processed_events`:
        - New `(consumer, eventId)` -> insert and return `DedupStatus.APPLIED`.
        - Same `(consumer, eventId)` with identical payload -> return `DedupStatus.DUPLICATE_IGNORED`.
        - Same `(consumer, eventId)` with different payload -> flag conflict and return `DedupStatus.CONFLICT_FLAGGED`.
        """
        raise NotImplementedError("TODO [Package 4]: Implement SqliteOutboxRepository.check_and_mark_processed")


class SqliteSnapshotRepository(ISnapshotRepository):
    """Thread-safe holder for the factory's active EligibilitySnapshot."""

    def __init__(self, initial_snapshot: EligibilitySnapshot) -> None:
        self._snapshot = initial_snapshot
        self._lock = threading.RLock()

    def get_snapshot(self) -> EligibilitySnapshot:
        with self._lock:
            return self._snapshot

    def save_snapshot(self, snapshot: EligibilitySnapshot) -> None:
        with self._lock:
            self._snapshot = snapshot
