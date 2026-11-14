"""[PACKAGE 4] File-backed SQLite persistence, concurrency, rollback and stub contracts."""

import json
import sqlite3
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tmc_gate.adapters.cross_cutting_stubs import (
    InMemoryAuditStub, InMemoryAuthorizationStub, InMemoryHealthStub,
)
from tmc_gate.adapters.sqlite_repos import (
    SqliteAttendanceRepository, SqliteOutboxRepository, SqliteScanRepository,
    SqliteUnitOfWork, create_sqlite_connection, init_sqlite_schema,
)
from tmc_gate.domain.enums import (
    ClosureStatus, CommandOutcome, CommandType, DedupStatus, Direction, PassageStatus, ScanState,
)
from tmc_gate.domain.models import (
    AttendanceTransaction, GateCommand, GateCycle, Lane, PassageEvent, ScanAttempt, SupportCase, TokenReservation,
)

NOW = datetime(2026, 11, 6, 1, 2, 3, 456789, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[tuple]:
    path = tmp_path / "factory.sqlite3"
    conn = create_sqlite_connection(str(path))
    init_sqlite_schema(conn)
    uow = SqliteUnitOfWork(conn)
    scans = SqliteScanRepository(conn, uow.lock)
    scans.seed_lane(Lane("lane-1", "station-1", "BD", Direction.ENTRY))
    scans.seed_lane(Lane("lane-2", "station-1", "BD", Direction.EXIT))
    try:
        yield (path, conn, uow, scans,
               SqliteAttendanceRepository(conn, uow.lock), SqliteOutboxRepository(conn, uow.lock))
    finally:
        uow.rollback()
        conn.close()


def _claim(lane_id: str = "lane-1", suffix: str = "1", token_id: str = "token-1", factory: str = "BD") -> tuple:
    scan = ScanAttempt(f"scan-{suffix}", factory, lane_id, ScanState.ACCEPTED, NOW,
                       tokenId=token_id, employeeId="employee-1", degradedFlag=True, snapshotVersion=7,
                       reason="ACCEPTED")
    reservation = TokenReservation(token_id, scan.scanId, factory, NOW, NOW + timedelta(seconds=60))
    cycle = GateCycle(f"cycle-{suffix}", lane_id, PassageStatus.WAITING, ClosureStatus.OPENED,
                      NOW + timedelta(seconds=15), scanId=scan.scanId)
    command = GateCommand(f"open-{suffix}", cycle.cycleId, lane_id, CommandType.OPEN,
                          scanId=scan.scanId, issuedAt=NOW)
    return scan, reservation, cycle, command


def _accept(uow: SqliteUnitOfWork, scans: SqliteScanRepository, args: tuple) -> None:
    with uow:
        assert scans.atomic_reserve_lane_claim_token_and_accept(*args) == (True, "ACCEPTED")
        uow.commit()


def _passage(claim: tuple, suffix: str = "1", late: bool = False) -> tuple:
    scan, _, cycle, command = claim
    passage = PassageEvent(f"passage-{suffix}", command.commandId, cycle.cycleId, cycle.laneId, NOW,
                           reviewReason="LATE_PASSAGE" if late else None)
    updated = replace(cycle, passageStatus=PassageStatus.NO_PASSAGE if late else PassageStatus.PASSED)
    transaction = None if late else AttendanceTransaction(
        f"attendance-{suffix}", scan.scanId, passage.passageEventId, scan.employeeId, scan.factoryId,
        scan.laneId, Direction.ENTRY, NOW, degradedFlag=scan.degradedFlag, snapshotVersion=scan.snapshotVersion,
    )
    event = {"eventId": f"event-{suffix}", "passageEventId": passage.passageEventId}
    audit = {"action": "PASSAGE_RECORDED", "passageEventId": passage.passageEventId}
    return passage, transaction, updated, event, audit


def _assert_other_writer(path: Path) -> None:
    other = create_sqlite_connection(str(path), busy_timeout_ms=0)
    try:
        with SqliteUnitOfWork(other) as uow:
            other.execute("UPDATE lanes SET station_id = station_id WHERE lane_id = 'lane-2'")
            uow.commit()
        assert not other.in_transaction
    finally:
        other.close()


def test_concurrent_claim_same_token(db: tuple) -> None:
    """Separate connections and free lanes race for the same factory token (ADR-11)."""
    path, conn, _, _, _, _ = db
    connections = [conn, create_sqlite_connection(str(path))]
    barrier = threading.Barrier(2, timeout=5)

    def worker(index: int) -> tuple[bool, str]:
        connection = connections[index]
        uow = SqliteUnitOfWork(connection)
        scans = SqliteScanRepository(connection, uow.lock)
        args = _claim(lane_id=f"lane-{index + 1}", suffix=str(index + 1))
        barrier.wait()
        uow.begin(timeout_ms=1500)
        try:
            result = scans.atomic_reserve_lane_claim_token_and_accept(*args, timeout_ms=1500)
            uow.commit() if result[0] else uow.rollback()
            return result
        finally:
            uow.rollback()
            assert not connection.in_transaction

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, range(2)))
        assert sorted(results) == [(False, "TOKEN_ALREADY_CLAIMED"), (True, "ACCEPTED")]
        for table in ("scan_attempts", "token_reservations", "gate_cycles", "gate_commands"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM lanes WHERE active_cycle_id IS NOT NULL").fetchone()[0] == 1
        _assert_other_writer(path)
    finally:
        connections[1].close()


def test_unit_of_work_rollback_and_event_id_dedup_and_conflict(db: tuple) -> None:
    """A simulated consumer receipt rolls back together with its business write."""
    path, conn, uow, scans, _, outbox = db
    event = {"eventId": "incoming-1", "amount": 1, "details": {"b": 2, "a": 1}}
    with pytest.raises(RuntimeError, match="mid-transaction"):
        with uow:
            scans.save_scan(_claim()[0], audit_payload={"action": "SCAN_EVALUATED"})
            outbox.append_event(event)
            assert outbox.check_and_mark_processed("hr-demo", "incoming-1", event) == DedupStatus.APPLIED
            raise RuntimeError("mid-transaction")
    assert not conn.in_transaction
    for table in ("scan_attempts", "outbox_events", "local_audit_evidence", "processed_events"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    _assert_other_writer(path)
    with uow:
        assert outbox.check_and_mark_processed("hr-demo", "incoming-1", event) == DedupStatus.APPLIED
        uow.commit()
    with uow:
        reordered = {"details": {"a": 1, "b": 2}, "amount": 1, "eventId": "incoming-1"}
        assert outbox.check_and_mark_processed("hr-demo", "incoming-1", reordered) == DedupStatus.DUPLICATE_IGNORED
        assert outbox.check_and_mark_processed("hr-demo", "incoming-1", {**event, "amount": 2}) == DedupStatus.CONFLICT_FLAGGED
        assert outbox.check_and_mark_processed("hr-demo", "incoming-1", event) == DedupStatus.DUPLICATE_IGNORED
        assert outbox.check_and_mark_processed("report-demo", "incoming-1", event) == DedupStatus.APPLIED
        uow.commit()
    assert conn.execute("SELECT conflict_flagged FROM processed_events WHERE consumer = 'hr-demo'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM processed_events").fetchone()[0] == 2


@pytest.mark.parametrize("ending", ["commit", "rollback", "exception", "uncommitted-exit"])
def test_uow_always_releases_transaction(db: tuple, ending: str) -> None:
    path, conn, uow, scans, _, _ = db
    try:
        with uow:
            scans.save_scan(_claim()[0])
            if ending == "exception":
                raise ValueError("abort")
            if ending in {"commit", "rollback"}:
                getattr(uow, ending)()
    except ValueError:
        assert ending == "exception"
    assert not conn.in_transaction
    assert (scans.get_scan("scan-1") is not None) == (ending == "commit")
    _assert_other_writer(path)


def test_uow_commit_error_rolls_back_and_releases_transaction(db: tuple) -> None:
    path, conn, uow, _, _, _ = db
    conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
    conn.execute("""CREATE TABLE deferred_child (parent_id INTEGER REFERENCES parent(id)
                    DEFERRABLE INITIALLY DEFERRED)""")
    with pytest.raises(sqlite3.IntegrityError):
        with uow:
            conn.execute("INSERT INTO deferred_child VALUES (42)")
            uow.commit()
    assert not conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM deferred_child").fetchone()[0] == 0
    _assert_other_writer(path)


@pytest.mark.parametrize("timeout_ms", [0, 43, 137])
def test_begin_uses_caller_timeout_and_cleans_up_lock_failure(db: tuple, timeout_ms: int) -> None:
    path, conn, uow, _, _, _ = db
    holder = create_sqlite_connection(str(path))
    holder_uow = SqliteUnitOfWork(holder)
    try:
        holder_uow.begin()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            uow.begin(timeout_ms=timeout_ms)
        actual = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert max(0, timeout_ms - 10) <= actual <= timeout_ms
        assert not conn.in_transaction
    finally:
        holder_uow.rollback()
        holder.close()
    with uow:
        uow.commit()
    _assert_other_writer(path)


def test_busy_lane_does_not_claim_a_new_token(db: tuple) -> None:
    _, conn, uow, scans, _, _ = db
    first = _claim()
    _accept(uow, scans, first)
    with uow:
        assert scans.atomic_reserve_lane_claim_token_and_accept(
            *_claim(suffix="2", token_id="token-2"),
        ) == (False, "LANE_BUSY")
        uow.rollback()
    assert conn.execute("SELECT COUNT(*) FROM token_reservations").fetchone()[0] == 1
    assert scans.get_scan("scan-2") is None
    assert scans.get_active_cycle_for_lane("lane-1") == first[2]


@pytest.mark.parametrize("failure_table", ["gate_cycles", "gate_commands", "scan_attempts"])
def test_accept_write_failure_has_no_partial_records(db: tuple, failure_table: str) -> None:
    path, conn, uow, scans, _, _ = db
    conn.execute(f"""CREATE TRIGGER fail_write BEFORE INSERT ON {failure_table}
                     BEGIN SELECT RAISE(ABORT, 'injected write failure'); END""")
    with uow:
        assert scans.atomic_reserve_lane_claim_token_and_accept(*_claim()) == (False, "STORAGE_FAILURE")
        uow.rollback()
    for table in ("token_reservations", "gate_cycles", "gate_commands", "scan_attempts"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert scans.get_lane("lane-1").activeCycleId is None
    assert not conn.in_transaction
    _assert_other_writer(path)


def test_claim_is_scoped_to_factory(db: tuple) -> None:
    _, _, uow, scans, _, _ = db
    scans.seed_lane(Lane("lane-la", "station-la", "LA", Direction.ENTRY))
    _accept(uow, scans, _claim())
    _accept(uow, scans, _claim("lane-la", "la", factory="LA"))


def test_command_history_preserves_issued_at_without_changing_cycle_or_lane(db: tuple) -> None:
    _, conn, uow, scans, _, _ = db
    args = _claim()
    _accept(uow, scans, args)
    issued_at = NOW.astimezone(timezone(timedelta(hours=7)))
    command = replace(args[3], commandId="close-1", commandType=CommandType.CLOSE, issuedAt=issued_at)
    before_cycle, before_lane = scans.get_cycle(args[2].cycleId), scans.get_lane("lane-1")
    with uow:
        scans.save_command(command)
        uow.commit()
    with uow:
        scans.save_command(replace(command, outcome=CommandOutcome.UNDETERMINED, issuedAt=None))
        uow.commit()
    assert scans.get_command(command.commandId) == replace(command, outcome=CommandOutcome.UNDETERMINED)
    assert conn.execute("SELECT issued_at FROM gate_commands WHERE command_id = 'close-1'").fetchone()[0] == issued_at.isoformat()
    assert scans.get_cycle(args[2].cycleId) == before_cycle
    assert scans.get_lane("lane-1") == before_lane
    assert scans.get_cycle_by_command(command.commandId) == before_cycle
    assert scans.get_scan(args[0].scanId) == args[0]


def test_get_command_by_support_case_filters_both_fields(db: tuple) -> None:
    _, _, uow, scans, _, _ = db
    base = replace(_claim()[3], supportCaseId="case-1", scanId=None)
    commands = [base, replace(base, commandId="emergency-1", commandType=CommandType.EMERGENCY_RELEASE),
                replace(base, commandId="other-case", supportCaseId="case-2"),
                replace(base, commandId="close-1", commandType=CommandType.CLOSE)]
    with uow:
        for command in commands:
            scans.save_command(command)
        uow.commit()
    assert scans.get_command_by_support_case("case-1", "OPEN") == base
    assert scans.get_command_by_support_case("case-1", "EMERGENCY_RELEASE") == commands[1]
    assert scans.get_command_by_support_case("missing", "OPEN") is None
    assert scans.get_command_by_support_case("case-1", "RESET") is None


def test_close_command_id_and_late_passage_survive_reopening(db: tuple) -> None:
    path, conn, uow, scans, attendance, _ = db
    args = _claim()
    _accept(uow, scans, args)
    passage, _, cycle, event, audit = _passage(args, late=True)
    cycle = replace(cycle, closeCommandId="close-1", laneClearReceived=True, closureStatus=ClosureStatus.CLOSE_UNCERTAIN)
    close_command = replace(args[3], commandId="close-1", commandType=CommandType.CLOSE,
                            outcome=CommandOutcome.UNDETERMINED)
    with uow:
        scans.update_cycle(cycle, command=close_command)
        assert attendance.store_passage_and_attendance_atomic(passage, None, cycle, event, audit)
        uow.commit()
    assert not conn.in_transaction
    reopened = create_sqlite_connection(str(path))
    try:
        init_sqlite_schema(reopened)
        restarted_scans, restarted_attendance = SqliteScanRepository(reopened), SqliteAttendanceRepository(reopened)
        restarted_scans.seed_lane(Lane("lane-1", "station-1", "BD", Direction.ENTRY))
        assert restarted_scans.get_cycle(cycle.cycleId) == cycle
        assert restarted_scans.get_active_cycle_for_lane(cycle.laneId) == cycle
        assert restarted_scans.get_command(cycle.closeCommandId) == close_command
        assert restarted_attendance.list_passages_for_cycle(cycle.cycleId) == [passage]
        assert restarted_attendance.get_by_scan_id(args[0].scanId) is None
        assert reopened.execute("SELECT review_reason FROM passage_events").fetchone()[0] == "LATE_PASSAGE"
    finally:
        reopened.close()


def test_support_case_is_insert_only_and_duplicate_has_no_side_effects(db: tuple) -> None:
    _, conn, uow, scans, _, outbox = db
    case = SupportCase("case-1", "BD", "lane-1", "guard-1", "Phone unavailable", NOW,
                       employeeId="employee-1", linkedScanId="scan-1", isEmergency=True)
    event = {"eventId": "case-event", "supportCaseId": case.supportCaseId}
    with uow:
        assert scans.save_support_case(case, event, {"action": "CASE_RECORDED"}) is True
        uow.commit()
    with uow:
        assert scans.save_support_case(replace(case, reason="Different evidence"),
                                      {"eventId": "duplicate-event"}, {"action": "SHOULD_NOT_BE_STORED"}) is False
        uow.commit()
    assert scans.get_support_case(case.supportCaseId) == case
    assert outbox.list_pending_events() == [event]
    assert conn.execute("SELECT COUNT(*) FROM local_audit_evidence").fetchone()[0] == 1


def test_multiple_passages_create_at_most_one_attendance_per_scan(db: tuple) -> None:
    _, conn, uow, scans, attendance, outbox = db
    args = _claim()
    _accept(uow, scans, args)
    first, second = _passage(args), _passage(args, "2")
    with uow:
        assert attendance.store_passage_and_attendance_atomic(*first) is True
        assert attendance.store_passage_and_attendance_atomic(*first) is False
        assert attendance.store_passage_and_attendance_atomic(*second) is True
        uow.commit()
    assert attendance.list_passages_for_cycle(args[2].cycleId) == [first[0], second[0]]
    assert attendance.get_by_scan_id(args[0].scanId) == first[1]
    assert attendance.list_transactions() == [first[1]]
    assert outbox.list_pending_events() == [first[3], second[3]]
    assert conn.execute("SELECT COUNT(*) FROM local_audit_evidence").fetchone()[0] == 2
    assert scans.get_cycle(args[2].cycleId).passageStatus == PassageStatus.PASSED


@pytest.mark.parametrize("failure_table", ["attendance_transactions", "outbox_events", "local_audit_evidence"])
def test_passage_write_failure_rolls_back_all_evidence(db: tuple, failure_table: str) -> None:
    path, conn, uow, scans, attendance, outbox = db
    args = _claim()
    _accept(uow, scans, args)
    conn.execute(f"""CREATE TRIGGER fail_write BEFORE INSERT ON {failure_table}
                     BEGIN SELECT RAISE(ABORT, 'injected write failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected write failure"):
        with uow:
            attendance.store_passage_and_attendance_atomic(*_passage(args))
            uow.commit()
    assert attendance.list_passages_for_cycle(args[2].cycleId) == []
    assert attendance.list_transactions() == []
    assert outbox.list_pending_events() == []
    assert conn.execute("SELECT COUNT(*) FROM local_audit_evidence").fetchone()[0] == 0
    assert scans.get_cycle(args[2].cycleId) == args[2]
    assert not conn.in_transaction
    _assert_other_writer(path)


@pytest.mark.parametrize("release", [True, False])
def test_opening_outcome_stages_scan_command_outbox_and_audit(db: tuple, release: bool) -> None:
    _, conn, uow, scans, _, outbox = db
    args = _claim()
    _accept(uow, scans, args)
    state = ScanState.CONTROLLER_ERROR if release else ScanState.OPENED
    outcome = CommandOutcome.CONTROLLER_ERROR if release else CommandOutcome.OPEN_CONFIRMED
    event = {"eventId": "opening-event", "outcome": outcome.value}
    with uow:
        scans.update_opening_outcome(args[0].scanId, args[2].cycleId, args[3].commandId,
                                     state.value, outcome.value, release, event, {"action": "OPEN_OUTCOME"})
        uow.commit()
    assert scans.get_scan(args[0].scanId).state == state
    assert scans.get_command(args[3].commandId).outcome == outcome
    assert scans.get_command(args[3].commandId).issuedAt == NOW
    assert scans.get_lane("lane-1").activeCycleId == (None if release else args[2].cycleId)
    expected_closure = ClosureStatus.CLOSED if release else ClosureStatus.OPENED
    assert scans.get_cycle(args[2].cycleId).closureStatus == expected_closure
    assert outbox.list_pending_events() == [event]
    assert json.loads(conn.execute("SELECT payload_json FROM local_audit_evidence").fetchone()[0]) == {"action": "OPEN_OUTCOME"}


def test_old_cycle_cannot_release_new_lane_reservation(db: tuple) -> None:
    _, _, uow, scans, _, _ = db
    old = _claim()
    _accept(uow, scans, old)
    with uow:
        scans.update_cycle(replace(old[2], closureStatus=ClosureStatus.CLOSED), releaseLane=True)
        uow.commit()
    new = _claim(suffix="2", token_id="token-2")
    _accept(uow, scans, new)
    with uow:
        scans.update_cycle(replace(old[2], closureStatus=ClosureStatus.CLOSED), releaseLane=True)
        scans.update_opening_outcome(old[0].scanId, old[2].cycleId, old[3].commandId,
                                     "CONTROLLER_ERROR", "CONTROLLER_ERROR", True)
        uow.commit()
    assert scans.get_lane("lane-1").activeCycleId == new[2].cycleId


def test_repositories_never_open_or_commit_transactions(db: tuple) -> None:
    path, conn, uow, scans, attendance, outbox = db
    statements = []
    uow.begin()
    conn.set_trace_callback(statements.append)
    try:
        assert scans.atomic_reserve_lane_claim_token_and_accept(*_claim())[0]
        attendance.store_passage_and_attendance_atomic(*_passage(_claim()))
        outbox.check_and_mark_processed("demo", "event-1", {"value": 1})
        assert conn.in_transaction
        assert not any(sql.lstrip().upper().startswith(("BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT")) for sql in statements)
        other = create_sqlite_connection(str(path))
        try:
            assert other.execute("SELECT COUNT(*) FROM scan_attempts").fetchone()[0] == 0
            assert SqliteOutboxRepository(other).list_pending_events() == []
        finally:
            other.close()
    finally:
        conn.set_trace_callback(None)
        uow.rollback()


def test_missing_rows_and_write_without_uow(db: tuple) -> None:
    _, conn, _, scans, attendance, outbox = db
    for getter in (scans.get_scan, scans.get_lane, scans.get_cycle, scans.get_cycle_by_command,
                   scans.get_active_cycle_for_lane, scans.get_command, scans.get_support_case,
                   attendance.get_by_scan_id):
        assert getter("missing") is None
    assert attendance.list_passages_for_cycle("missing") == []
    assert attendance.list_transactions() == []
    assert outbox.list_pending_events() == []
    with pytest.raises(RuntimeError, match="active UnitOfWork"):
        scans.save_scan(_claim()[0])
    with pytest.raises(RuntimeError, match="active UnitOfWork"):
        outbox.append_event({"eventId": "event-1"})
    assert scans.atomic_reserve_lane_claim_token_and_accept(*_claim()) == (False, "STORAGE_FAILURE")
    assert not conn.in_transaction


def test_upgrade_legacy_schema_keeps_existing_data(tmp_path: Path) -> None:
    conn = create_sqlite_connection(str(tmp_path / "legacy.sqlite3"))
    try:
        init_sqlite_schema(conn)
        conn.execute("ALTER TABLE gate_cycles DROP COLUMN close_command_id")
        conn.execute("ALTER TABLE passage_events DROP COLUMN review_reason")
        conn.execute("DROP TABLE token_reservations")
        conn.execute("""CREATE TABLE token_reservations (
                        token_id TEXT PRIMARY KEY, scan_id TEXT NOT NULL UNIQUE, factory_id TEXT NOT NULL,
                        claimed_at TEXT NOT NULL, expires_at TEXT NOT NULL)""")
        values = ("legacy-token", "legacy-scan", "BD", NOW.isoformat(), NOW.isoformat())
        conn.execute("INSERT INTO token_reservations VALUES (?, ?, ?, ?, ?)", values)
        init_sqlite_schema(conn)
        init_sqlite_schema(conn)
        assert tuple(conn.execute("SELECT * FROM token_reservations").fetchone()) == values
        assert "close_command_id" in {row["name"] for row in conn.execute("PRAGMA table_info(gate_cycles)")}
        assert "review_reason" in {row["name"] for row in conn.execute("PRAGMA table_info(passage_events)")}
        assert not conn.in_transaction
    finally:
        conn.close()


def test_committed_context_exit_cannot_rollback_another_thread(db: tuple) -> None:
    """A commit releases the lock before __exit__; a new owner must keep its transaction."""
    _, conn, uow, scans, _, _ = db
    started, finished = threading.Event(), threading.Event()

    def worker() -> None:
        with uow:
            scans.save_scan(_claim(suffix="2")[0])
            started.set()
            assert finished.wait(timeout=5)
            uow.commit()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with uow:
            scans.save_scan(_claim()[0])
            uow.commit()
            future = pool.submit(worker)
            assert started.wait(timeout=5)
        assert conn.in_transaction
        finished.set()
        future.result(timeout=5)
    assert scans.get_scan("scan-1") is not None
    assert scans.get_scan("scan-2") is not None
    assert not conn.in_transaction


def test_attendance_list_is_ordered_and_passage_does_not_reserve_closed_lane(db: tuple) -> None:
    _, _, uow, scans, attendance, _ = db
    first, second = _claim(), _claim("lane-2", "2", "token-2")
    _accept(uow, scans, first)
    _accept(uow, scans, second)
    with uow:
        scans.update_cycle(replace(first[2], closureStatus=ClosureStatus.CLOSED), releaseLane=True)
        for claim, suffix, when in ((first, "1", NOW + timedelta(seconds=1)), (second, "2", NOW)):
            passage, transaction, cycle, event, audit = _passage(claim, suffix)
            attendance.store_passage_and_attendance_atomic(
                replace(passage, occurredAt=when), replace(transaction, occurredAt=when), cycle, event, audit,
            )
        uow.commit()
    assert [transaction.scanId for transaction in attendance.list_transactions()] == ["scan-2", "scan-1"]
    assert scans.get_lane("lane-1").activeCycleId is None
    assert scans.get_cycle(first[2].cycleId).closureStatus == ClosureStatus.CLOSED


def test_audit_and_health_stubs_capture_records() -> None:
    audit, health = InMemoryAuditStub(), InMemoryHealthStub()
    record, metric = {"action": "GATE_OPEN_CONFIRMED"}, {"alert": "CONTROLLER_ERROR"}
    audit.append_async(record)
    audit.append_async({"action": "PASSAGE_RECORDED"})
    health.report_event_async(metric)
    assert audit.records == [record, {"action": "PASSAGE_RECORDED"}]
    assert health.events == [metric]


@pytest.mark.parametrize("action", ["RECORD_ASSISTED", "ASSISTED_OPEN", "EMERGENCY_RELEASE", "EMERGENCY_RESET"])
def test_authorization_stub_checks_guard_role_action_and_factory(action: str) -> None:
    auth = InMemoryAuthorizationStub({"guard-1": {"BD"}})
    assert auth.is_authorized("guard-1", "SECURITY_GUARD", action, "BD")
    assert not auth.is_authorized("guard-1", "SECURITY_GUARD", action, "LA")
    assert not auth.is_authorized("missing", "SECURITY_GUARD", action, "BD")
    assert not auth.is_authorized("guard-1", "HR", action, "BD")
    assert not auth.is_authorized("guard-1", "SECURITY_GUARD", "UNKNOWN_ACTION", "BD")
    assert not InMemoryAuthorizationStub({}).is_authorized("GUARD-BD-01", "SECURITY_GUARD", action, "BD")
