"""[PACKAGE 4] SQLite Unit of Work and Persistence adapters for M1 Gate Core (Figure 4.2, Table 4.7, §5.6.1, ADR-02, ADR-05, ADR-11).

Transaction rules:
- SqliteUnitOfWork implements IUnitOfWork (begin / commit / rollback).
- Every repository receives the transaction context from IUnitOfWork and NEVER opens or commits transactions on its own.
- Supports 1 GateCycle -> 0..* PassageEvent (each passageEventId accepted once; at most 1 original AttendanceTransaction per scanId).
- Supports simulated consumer deduplication keyed by (consumer, eventId) with payload conflict detection.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime
from types import TracebackType

from tmc_gate.domain.enums import (
    ClosureStatus,
    CommandOutcome,
    CommandType,
    DedupStatus,
    Direction,
    PassageStatus,
    ScanState,
)
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
            token_id TEXT NOT NULL,
            scan_id TEXT NOT NULL UNIQUE,
            factory_id TEXT NOT NULL,
            claimed_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            PRIMARY KEY (factory_id, token_id)
        );

        CREATE TABLE IF NOT EXISTS gate_cycles (
            cycle_id TEXT PRIMARY KEY,
            lane_id TEXT NOT NULL,
            scan_id TEXT,
            support_case_id TEXT,
            passage_status TEXT NOT NULL,
            closure_status TEXT NOT NULL,
            observation_deadline TEXT NOT NULL,
            lane_clear_received INTEGER NOT NULL DEFAULT 0,
            close_command_id TEXT
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
            occurred_at TEXT NOT NULL,
            review_reason TEXT
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
    # Upgrade existing factory files without discarding stored evidence.
    with SqliteUnitOfWork(conn) as uow:
        for table, column in (("gate_cycles", "close_command_id"), ("passage_events", "review_reason")):
            if column not in {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
        token_pk = {row["name"] for row in conn.execute("PRAGMA table_info(token_reservations)") if row["pk"]}
        if token_pk == {"token_id"}:
            conn.execute("ALTER TABLE token_reservations RENAME TO token_reservations_legacy")
            conn.execute(
                """CREATE TABLE token_reservations (
                    token_id TEXT NOT NULL, scan_id TEXT NOT NULL UNIQUE, factory_id TEXT NOT NULL,
                    claimed_at TEXT NOT NULL, expires_at TEXT NOT NULL, PRIMARY KEY (factory_id, token_id)
                )"""
            )
            conn.execute("INSERT INTO token_reservations SELECT * FROM token_reservations_legacy")
            conn.execute("DROP TABLE token_reservations_legacy")
        uow.commit()


def _require_transaction(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        raise RuntimeError("Repository writes require an active UnitOfWork transaction")


def _append_outbox(conn: sqlite3.Connection, event: dict[str, object]) -> None:
    conn.execute(
        "INSERT INTO outbox_events (event_id, payload_json, created_at) VALUES (?, ?, ?)",
        (event["eventId"], json.dumps(event, sort_keys=True), datetime.now(UTC).isoformat()),
    )


def _append_audit(conn: sqlite3.Connection, payload: dict[str, object]) -> None:
    conn.execute(
        "INSERT INTO local_audit_evidence (audit_id, payload_json, created_at) VALUES (?, ?, ?)",
        (f"audit-{uuid.uuid4().hex}", json.dumps(payload, sort_keys=True), datetime.now(UTC).isoformat()),
    )


def _read_cycle(row: sqlite3.Row) -> GateCycle:
    return GateCycle(
        cycleId=row["cycle_id"], laneId=row["lane_id"], scanId=row["scan_id"],
        supportCaseId=row["support_case_id"], passageStatus=PassageStatus(row["passage_status"]),
        closureStatus=ClosureStatus(row["closure_status"]),
        observationDeadline=datetime.fromisoformat(row["observation_deadline"]),
        laneClearReceived=bool(row["lane_clear_received"]), closeCommandId=row["close_command_id"],
    )


def _read_command(row: sqlite3.Row) -> GateCommand:
    return GateCommand(
        commandId=row["command_id"], cycleId=row["cycle_id"], laneId=row["lane_id"],
        scanId=row["scan_id"], supportCaseId=row["support_case_id"],
        commandType=CommandType(row["command_type"]), outcome=CommandOutcome(row["outcome"]),
        issuedAt=datetime.fromisoformat(row["issued_at"]) if row["issued_at"] is not None else None,
    )


def _read_attendance(row: sqlite3.Row) -> AttendanceTransaction:
    return AttendanceTransaction(
        transactionId=row["transaction_id"], scanId=row["scan_id"], passageEventId=row["passage_event_id"],
        employeeId=row["employee_id"], factoryId=row["factory_id"], laneId=row["lane_id"],
        direction=Direction(row["direction"]), occurredAt=datetime.fromisoformat(row["occurred_at"]),
        source=row["source"], degradedFlag=bool(row["degraded_flag"]), snapshotVersion=row["snapshot_version"],
    )


class SqliteUnitOfWork(IUnitOfWork):
    """Own transaction boundaries and hold the connection lock until commit or rollback."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock | None = None) -> None:
        self._conn = conn
        self._lock = lock or threading.RLock()
        self._in_tx = False
        self._owner_thread: int | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def begin(self, timeout_ms: int | None = None) -> None:
        """Acquire the write reservation using the caller's remaining scan budget."""
        started_ms = time.monotonic_ns() // 1_000_000
        acquired = self._lock.acquire() if timeout_ms is None else self._lock.acquire(timeout=max(0, timeout_ms) / 1000)
        if not acquired:
            raise sqlite3.OperationalError("UnitOfWork lock timeout")
        if self._in_tx or self._conn.in_transaction:
            self._lock.release()
            raise RuntimeError("UnitOfWork transaction is already active")
        try:
            if timeout_ms is not None:
                remaining_ms = max(0, int(timeout_ms) - (time.monotonic_ns() // 1_000_000 - started_ms))
                self._conn.execute(f"PRAGMA busy_timeout = {remaining_ms}")
            self._conn.execute("BEGIN IMMEDIATE")
            self._in_tx = True
            self._owner_thread = threading.get_ident()
        except BaseException:
            try:
                self._conn.rollback()
            finally:
                self._lock.release()
            raise

    def commit(self) -> None:
        """Commit staged writes; a failed commit rolls back before releasing the lock."""
        try:
            self._conn.commit()
        finally:
            try:
                if self._conn.in_transaction:
                    self._conn.rollback()
            finally:
                self._finish()

    def rollback(self) -> None:
        """Discard staged writes and release the lock, including after SQLite aborts."""
        try:
            self._conn.rollback()
        finally:
            self._finish()

    def _finish(self) -> None:
        if self._in_tx:
            self._in_tx = False
            self._owner_thread = None
            self._lock.release()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        # Commit is explicit; even a read-only or early-return path must release BEGIN IMMEDIATE.
        if self._in_tx and self._owner_thread == threading.get_ident():
            self.rollback()


class SqliteScanRepository(IScanRepository):
    """SQLite implementation of IScanRepository operating within SqliteUnitOfWork's transaction context."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock | None = None) -> None:
        self._conn = conn
        self._lock = lock or threading.RLock()
        self.simulate_write_failure: bool = False

    def seed_lane(self, lane: Lane) -> None:
        """Bootstrap lane configuration while preserving a reservation across restarts."""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO lanes (lane_id, station_id, factory_id, direction, active_cycle_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(lane_id) DO UPDATE SET station_id = excluded.station_id,
                    factory_id = excluded.factory_id, direction = excluded.direction
                """,
                (lane.laneId, lane.stationId, lane.factoryId, lane.direction.value, lane.activeCycleId),
            )

    def get_command(self, commandId: str) -> GateCommand | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM gate_commands WHERE command_id = ?", (commandId,)).fetchone()
            return _read_command(row) if row is not None else None

    def save_command(self, command: GateCommand) -> None:
        """Stage command history without touching the cycle or lane reservation."""
        with self._lock:
            _require_transaction(self._conn)
            self._conn.execute(
                """INSERT INTO gate_commands
                    (command_id, cycle_id, lane_id, scan_id, support_case_id, command_type, outcome, issued_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(command_id) DO UPDATE SET
                        cycle_id = excluded.cycle_id, lane_id = excluded.lane_id, scan_id = excluded.scan_id,
                        support_case_id = excluded.support_case_id, command_type = excluded.command_type,
                        outcome = excluded.outcome""",
                (command.commandId, command.cycleId, command.laneId, command.scanId, command.supportCaseId,
                 command.commandType.value, command.outcome.value,
                 command.issuedAt.isoformat() if command.issuedAt is not None else None),
            )

    def get_command_by_support_case(self, supportCaseId: str, commandType: str) -> GateCommand | None:
        """Find the command for both the support case and requested command type."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM gate_commands WHERE support_case_id = ? AND command_type = ? ORDER BY rowid LIMIT 1",
                (supportCaseId, commandType),
            ).fetchone()
            return _read_command(row) if row is not None else None

    def get_scan(self, scanId: str) -> ScanAttempt | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM scan_attempts WHERE scan_id = ?", (scanId,)).fetchone()
            if row is None:
                return None
            return ScanAttempt(
                scanId=row["scan_id"], tokenId=row["token_id"], employeeId=row["employee_id"],
                factoryId=row["factory_id"], laneId=row["lane_id"], state=ScanState(row["state"]),
                createdAt=datetime.fromisoformat(row["created_at"]), degradedFlag=bool(row["degraded_flag"]),
                snapshotVersion=row["snapshot_version"], reason=row["reason"],
            )

    def save_scan(self, scan: ScanAttempt, audit_payload: dict[str, object] | None = None) -> None:
        """Stage the scan and optional durable audit evidence in the current UoW."""
        with self._lock:
            _require_transaction(self._conn)
            self._conn.execute(
                """INSERT INTO scan_attempts
                    (scan_id, token_id, employee_id, factory_id, lane_id, state, degraded_flag,
                     snapshot_version, reason, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scan_id) DO UPDATE SET
                        token_id = excluded.token_id, employee_id = excluded.employee_id,
                        factory_id = excluded.factory_id, lane_id = excluded.lane_id, state = excluded.state,
                        degraded_flag = excluded.degraded_flag, snapshot_version = excluded.snapshot_version,
                        reason = excluded.reason""",
                (scan.scanId, scan.tokenId, scan.employeeId, scan.factoryId, scan.laneId, scan.state.value,
                 int(scan.degradedFlag), scan.snapshotVersion, scan.reason, scan.createdAt.isoformat()),
            )
            if audit_payload is not None:
                _append_audit(self._conn, audit_payload)

    def get_lane(self, laneId: str) -> Lane | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM lanes WHERE lane_id = ?", (laneId,)).fetchone()
            if row is None:
                return None
            return Lane(row["lane_id"], row["station_id"], row["factory_id"],
                        Direction(row["direction"]), row["active_cycle_id"])

    def get_active_cycle_for_lane(self, laneId: str) -> GateCycle | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT gate_cycles.* FROM lanes JOIN gate_cycles ON gate_cycles.cycle_id = lanes.active_cycle_id
                   WHERE lanes.lane_id = ?""", (laneId,),
            ).fetchone()
            return _read_cycle(row) if row is not None else None

    def atomic_reserve_lane_claim_token_and_accept(
        self,
        scan: ScanAttempt,
        reservation: TokenReservation,
        cycle: GateCycle,
        command: GateCommand,
        timeout_ms: int | None = None,
    ) -> tuple[bool, str]:
        """Execute Flow A4 inside the active UnitOfWork transaction (ADR-02, ADR-11).

        - If lane is busy -> return `(False, "LANE_BUSY")` without claiming `tokenId`.
        - If `tokenId` is already claimed -> return `(False, "TOKEN_ALREADY_CLAIMED")`.
        - If `busy_timeout` expires or write fails -> return `(False, "STORAGE_FAILURE")`.
        - Otherwise stage `token_reservations`, `gate_cycles`, `lanes.active_cycle_id`, `scan_attempts`, and `gate_commands`
          and return `(True, "ACCEPTED")`.
        """
        with self._lock:
            # BEGIN IMMEDIATE and its timeout belong to UoW. The caller must roll back any failed result.
            if not self._conn.in_transaction:
                return False, "STORAGE_FAILURE"
            try:
                lane = self.get_lane(scan.laneId)
                if lane is None:
                    return False, "STORAGE_FAILURE"
                if lane.activeCycleId is not None:
                    return False, "LANE_BUSY"
                claimed = self._conn.execute(
                    "SELECT 1 FROM token_reservations WHERE factory_id = ? AND token_id = ?",
                    (reservation.factoryId, reservation.tokenId),
                ).fetchone()
                if claimed is not None:
                    return False, "TOKEN_ALREADY_CLAIMED"
                if self.simulate_write_failure:
                    return False, "STORAGE_FAILURE"
                if self.get_scan(scan.scanId) is not None:
                    return False, "STORAGE_FAILURE"
                self._conn.execute(
                    """INSERT INTO token_reservations (token_id, scan_id, factory_id, claimed_at, expires_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (reservation.tokenId, reservation.scanId, reservation.factoryId,
                     reservation.claimedAt.isoformat(), reservation.expiresAt.isoformat()),
                )
                self.update_cycle(cycle, command=command)
                self.save_scan(scan)
                return True, "ACCEPTED"
            except sqlite3.Error:
                return False, "STORAGE_FAILURE"

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
        """Update `scan_attempts.state`, `gate_commands.outcome`, release lane if requested,
        and insert `outboxEvent` / `auditPayload` inside the active UnitOfWork transaction.
        """
        with self._lock:
            _require_transaction(self._conn)
            self._conn.execute("UPDATE scan_attempts SET state = ?, reason = ? WHERE scan_id = ?",
                               (scanState, commandOutcome, scanId))
            self._conn.execute("UPDATE gate_commands SET outcome = ? WHERE command_id = ?",
                               (commandOutcome, commandId))
            if releaseLane:
                self._conn.execute("UPDATE gate_cycles SET closure_status = ? WHERE cycle_id = ?",
                                   (ClosureStatus.CLOSED.value, cycleId))
                self._conn.execute("UPDATE lanes SET active_cycle_id = NULL WHERE active_cycle_id = ?", (cycleId,))
            if outboxEvent is not None:
                _append_outbox(self._conn, outboxEvent)
            if auditPayload is not None:
                _append_audit(self._conn, auditPayload)

    def get_cycle_by_command(self, commandId: str) -> GateCycle | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT gate_cycles.* FROM gate_commands JOIN gate_cycles USING (cycle_id)
                   WHERE command_id = ?""", (commandId,),
            ).fetchone()
            return _read_cycle(row) if row is not None else None

    def get_cycle(self, cycleId: str) -> GateCycle | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM gate_cycles WHERE cycle_id = ?", (cycleId,)).fetchone()
            return _read_cycle(row) if row is not None else None

    def update_cycle(
        self,
        cycle: GateCycle,
        releaseLane: bool = False,
        command: GateCommand | None = None,
    ) -> None:
        """Stage cycle state, optional command history, and the corresponding lane reservation."""
        with self._lock:
            _require_transaction(self._conn)
            self._conn.execute(
                """INSERT INTO gate_cycles
                    (cycle_id, lane_id, scan_id, support_case_id, passage_status, closure_status,
                     observation_deadline, lane_clear_received, close_command_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cycle_id) DO UPDATE SET
                        lane_id = excluded.lane_id, scan_id = excluded.scan_id,
                        support_case_id = excluded.support_case_id, passage_status = excluded.passage_status,
                        closure_status = excluded.closure_status, observation_deadline = excluded.observation_deadline,
                        lane_clear_received = excluded.lane_clear_received, close_command_id = excluded.close_command_id""",
                (cycle.cycleId, cycle.laneId, cycle.scanId, cycle.supportCaseId, cycle.passageStatus.value,
                 cycle.closureStatus.value, cycle.observationDeadline.isoformat(), int(cycle.laneClearReceived),
                 cycle.closeCommandId),
            )
            if command is not None:
                self.save_command(command)
            if releaseLane or not cycle.is_active_or_unresolved():
                self._conn.execute(
                    "UPDATE lanes SET active_cycle_id = NULL WHERE lane_id = ? AND active_cycle_id = ?",
                    (cycle.laneId, cycle.cycleId),
                )
            else:
                updated = self._conn.execute(
                    """UPDATE lanes SET active_cycle_id = ? WHERE lane_id = ?
                       AND (active_cycle_id IS NULL OR active_cycle_id = ?)""",
                    (cycle.cycleId, cycle.laneId, cycle.cycleId),
                )
                if updated.rowcount != 1:
                    raise sqlite3.IntegrityError("Lane is missing or reserved by another GateCycle")

    def save_support_case(
        self,
        case: SupportCase,
        outboxEvent: dict[str, object] | None = None,
        auditPayload: dict[str, object] | None = None,
    ) -> bool:
        """Stage `SupportCase` and evidence, returning False for a duplicate supportCaseId.

        Insert only (PRIMARY KEY supportCaseId), never INSERT OR REPLACE: Gate Core checks for an existing case
        in the same Unit of Work and must never overwrite stored evidence.
        """
        with self._lock:
            _require_transaction(self._conn)
            inserted = self._conn.execute(
                """INSERT INTO support_cases
                    (support_case_id, factory_id, lane_id, guard_id, employee_id, reason,
                     linked_scan_id, is_emergency, occurred_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(support_case_id) DO NOTHING""",
                (case.supportCaseId, case.factoryId, case.laneId, case.guardId, case.employeeId, case.reason,
                 case.linkedScanId, int(case.isEmergency), case.occurredAt.isoformat()),
            )
            if inserted.rowcount == 0:
                return False
            if outboxEvent is not None:
                _append_outbox(self._conn, outboxEvent)
            if auditPayload is not None:
                _append_audit(self._conn, auditPayload)
            return True

    def get_support_case(self, supportCaseId: str) -> SupportCase | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM support_cases WHERE support_case_id = ?", (supportCaseId,)).fetchone()
            if row is None:
                return None
            return SupportCase(
                supportCaseId=row["support_case_id"], factoryId=row["factory_id"], laneId=row["lane_id"],
                guardId=row["guard_id"], employeeId=row["employee_id"], reason=row["reason"],
                linkedScanId=row["linked_scan_id"], isEmergency=bool(row["is_emergency"]),
                occurredAt=datetime.fromisoformat(row["occurred_at"]),
            )


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
        """Stage all passage evidence together; the caller owns commit and rollback.
        1. Ignore duplicate `passage.passageEventId` (return False if already in `passage_events`).
        2. Insert `passage_events` (a GateCycle may have multiple PassageEvents), update `gate_cycles.passage_status`,
           and if `transaction is not None` insert at most one `attendance_transactions` row per `scan_id`.
        3. Insert `outbox_events` and `local_audit_evidence` in the same UnitOfWork transaction.
        """
        with self._lock:
            _require_transaction(self._conn)
            inserted = self._conn.execute(
                """INSERT INTO passage_events
                    (passage_event_id, command_id, cycle_id, lane_id, occurred_at, review_reason)
                    VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(passage_event_id) DO NOTHING""",
                (passage.passageEventId, passage.commandId, passage.cycleId, passage.laneId,
                 passage.occurredAt.isoformat(), passage.reviewReason),
            )
            if inserted.rowcount == 0:
                return False
            self._conn.execute("UPDATE gate_cycles SET passage_status = ? WHERE cycle_id = ?",
                               (cycle.passageStatus.value, cycle.cycleId))
            if transaction is not None:
                self._conn.execute(
                    """INSERT INTO attendance_transactions
                        (transaction_id, scan_id, passage_event_id, employee_id, factory_id, lane_id, direction,
                         occurred_at, source, degraded_flag, snapshot_version)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(scan_id) DO NOTHING""",
                    (transaction.transactionId, transaction.scanId, transaction.passageEventId,
                     transaction.employeeId, transaction.factoryId, transaction.laneId, transaction.direction.value,
                     transaction.occurredAt.isoformat(), transaction.source, int(transaction.degradedFlag),
                     transaction.snapshotVersion),
                )
            _append_outbox(self._conn, outboxEvent)
            _append_audit(self._conn, auditPayload)
            return True

    def get_by_scan_id(self, scanId: str) -> AttendanceTransaction | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM attendance_transactions WHERE scan_id = ?", (scanId,)).fetchone()
            return _read_attendance(row) if row is not None else None

    def list_transactions(self) -> list[AttendanceTransaction]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM attendance_transactions ORDER BY occurred_at, transaction_id")
            return [_read_attendance(row) for row in rows]

    def list_passages_for_cycle(self, cycleId: str) -> list[PassageEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM passage_events WHERE cycle_id = ? ORDER BY occurred_at, passage_event_id", (cycleId,),
            )
            return [PassageEvent(
                passageEventId=row["passage_event_id"], commandId=row["command_id"], cycleId=row["cycle_id"],
                laneId=row["lane_id"], occurredAt=datetime.fromisoformat(row["occurred_at"]),
                reviewReason=row["review_reason"],
            ) for row in rows]


class SqliteOutboxRepository(IOutboxRepository):
    """SQLite implementation of IOutboxRepository and simulated consumer deduplication (Table 4.7, ADR-05)."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock | None = None) -> None:
        self._conn = conn
        self._lock = lock or threading.RLock()

    def append_event(self, event: dict[str, object]) -> None:
        """Stage an outgoing event in the caller's UnitOfWork transaction."""
        with self._lock:
            _require_transaction(self._conn)
            _append_outbox(self._conn, event)

    def list_pending_events(self) -> list[dict[str, object]]:
        with self._lock:
            rows = self._conn.execute("SELECT payload_json FROM outbox_events ORDER BY created_at, rowid")
            return [json.loads(row["payload_json"]) for row in rows]

    def check_and_mark_processed(
        self,
        consumer: str,
        eventId: str,
        payload: dict[str, object],
    ) -> DedupStatus:
        """Simulated consumer deduplication keyed by `(consumer, eventId)` in `processed_events`:
        - New `(consumer, eventId)` -> insert and return `DedupStatus.APPLIED`.
        - Same `(consumer, eventId)` with identical payload -> return `DedupStatus.DUPLICATE_IGNORED`.
        - Same `(consumer, eventId)` with different payload -> flag conflict and return `DedupStatus.CONFLICT_FLAGGED`.
        """
        with self._lock:
            _require_transaction(self._conn)
            payload_hash = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            inserted = self._conn.execute(
                """INSERT INTO processed_events (consumer, event_id, payload_hash, processed_at)
                   VALUES (?, ?, ?, ?) ON CONFLICT(consumer, event_id) DO NOTHING""",
                (consumer, eventId, payload_hash, datetime.now(UTC).isoformat()),
            )
            if inserted.rowcount == 1:
                return DedupStatus.APPLIED
            row = self._conn.execute(
                "SELECT payload_hash FROM processed_events WHERE consumer = ? AND event_id = ?", (consumer, eventId),
            ).fetchone()
            if row["payload_hash"] == payload_hash:
                return DedupStatus.DUPLICATE_IGNORED
            self._conn.execute(
                "UPDATE processed_events SET conflict_flagged = 1 WHERE consumer = ? AND event_id = ?",
                (consumer, eventId),
            )
            return DedupStatus.CONFLICT_FLAGGED


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
