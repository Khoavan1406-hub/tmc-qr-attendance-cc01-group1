"""[PACKAGE 4] SQLite persistence adapters for M1 Gate Core (Figure 4.2, Table 4.7, §5.6.1, ADR-02, ADR-05, ADR-11).

Enforces atomic transactions for:
1. Reserve lane (active GateCycle) + Claim tokenId (TokenReservation) + Persist ScanAttempt(ACCEPTED).
2. Store PassageEvent + at most one original AttendanceTransaction per scanId + Outbox event + Audit evidence.
"""

from __future__ import annotations

import sqlite3
import threading

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
)


def create_sqlite_connection(db_path: str = ":memory:") -> sqlite3.Connection:
    """Create a thread-safe SQLite connection configured for explicit transactions."""
    conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
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
            degraded_flag INTEGER NOT NULL DEFAULT 0
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
        """
    )


class SqliteScanRepository(IScanRepository):
    """SQLite implementation of IScanRepository with atomic lane & token reservation."""

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

    def get_scan(self, scanId: str) -> ScanAttempt | None:
        """TODO [Package 4]: Query `scan_attempts` by `scan_id` and return `ScanAttempt` or `None`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteScanRepository.get_scan")

    def save_scan(self, scan: ScanAttempt, audit_payload: dict[str, object] | None = None) -> None:
        """TODO [Package 4]: Persist `ScanAttempt` (and optional `audit_payload` in `local_audit_evidence`) in a transaction."""
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
    ) -> tuple[bool, str]:
        """Execute Flow A4 in one atomic SQLite transaction (`BEGIN IMMEDIATE` ... `COMMIT`) (ADR-02, ADR-11).

        TODO [Package 4]:
        1. If `self.simulate_write_failure` is True, return `(False, "STORAGE_FAILURE")`.
        2. Check `lanes.active_cycle_id`: if not None, `ROLLBACK` and return `(False, "LANE_BUSY")` (do NOT claim tokenId!).
        3. Check `token_reservations`: if `reservation.tokenId` already exists, `ROLLBACK` and return `(False, "TOKEN_ALREADY_CLAIMED")`.
        4. Insert `token_reservations`, `gate_cycles`, update `lanes.active_cycle_id`, insert `scan_attempts`, and insert `gate_commands`.
        5. `COMMIT` and return `(True, "ACCEPTED")`.
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
        and insert `outboxEvent` / `auditPayload` in one transaction.
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

    def save_support_case(self, case: SupportCase) -> None:
        """TODO [Package 4]: Insert or replace `SupportCase` in `support_cases`."""
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
        2. Insert `passage_events`, update `gate_cycles.passage_status`, and if `transaction is not None`
           insert at most one `attendance_transactions` row per `scan_id`.
        3. Insert `outbox_events` and `local_audit_evidence` in the same transaction.
        """
        raise NotImplementedError("TODO [Package 4]: Implement SqliteAttendanceRepository.store_passage_and_attendance_atomic")

    def get_by_scan_id(self, scanId: str) -> AttendanceTransaction | None:
        """TODO [Package 4]: Query `attendance_transactions` by `scan_id`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteAttendanceRepository.get_by_scan_id")

    def list_transactions(self) -> list[AttendanceTransaction]:
        """TODO [Package 4]: Return all rows from `attendance_transactions` ordered by `occurred_at`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteAttendanceRepository.list_transactions")


class SqliteOutboxRepository(IOutboxRepository):
    """SQLite implementation of IOutboxRepository (Table 4.7, ADR-05)."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock | None = None) -> None:
        self._conn = conn
        self._lock = lock or threading.RLock()

    def append_event(self, event: dict[str, object]) -> None:
        """TODO [Package 4]: Insert event JSON into `outbox_events`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteOutboxRepository.append_event")

    def list_pending_events(self) -> list[dict[str, object]]:
        """TODO [Package 4]: Return all events from `outbox_events`."""
        raise NotImplementedError("TODO [Package 4]: Implement SqliteOutboxRepository.list_pending_events")


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
