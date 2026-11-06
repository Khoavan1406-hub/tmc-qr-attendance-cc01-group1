"""[PACKAGE 3] PR2 regression probes from cross-review (06/11): window start, late PASSAGE, close once, emergency vs close.

Run with PYTHONPATH=src (tests/ is on sys.path via pytest rootdir).
The existing fake ports are reused; concurrent reads return independent objects,
as a database repository would, and writes are serialized by a test UoW lock.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from threading import Barrier, RLock
from unittest.mock import patch

import tmc_gate.application.processor as processor_module
from test_pkg3_rules import LANE, _fixture_with_clock, _open
from tmc_gate.domain.enums import ClosureStatus, CommandOutcome, PassageStatus, ScanState


def test_passage_after_deadline_without_timer_callback_is_review_only():
    processor, facade, repo, attendance, turnstile, obs = _fixture_with_clock(0)
    opened = _open(facade)
    cycle = repo.get_cycle_by_command(opened.commandId)
    received_at = cycle.observationDeadline + timedelta(seconds=1)
    assert cycle.passageStatus == PassageStatus.WAITING
    with patch.object(processor_module, "datetime") as clock:
        clock.now.return_value = received_at
        processor.onPassage("received-too-late", opened.commandId, LANE)
    assert attendance.list_transactions() == [], "Late arrival produced automatic attendance before the timer callback ran"
    assert attendance.passages["received-too-late"].reviewReason == "LATE_PASSAGE"
    assert turnstile.close_calls == []


def test_observation_window_starts_at_open_confirmation():
    processor, facade, repo, _, turnstile, _ = _fixture_with_clock(0)
    started_at = datetime.now(UTC)
    confirmed_at = started_at + timedelta(milliseconds=500)
    original_open = turnstile.openGate
    with patch.object(processor_module, "datetime") as clock:
        clock.now.return_value = started_at

        def confirm_after_500ms(**kwargs):
            clock.now.return_value = confirmed_at
            return original_open(**kwargs)

        turnstile.openGate = confirm_after_500ms
        opened = facade.submitScan("window-start", "QR-VALID-WINDOW", LANE, now=started_at)
    cycle = repo.get_cycle_by_command(opened.commandId)
    assert cycle.observationDeadline == confirmed_at + timedelta(seconds=15)


def test_final_close_result_is_persisted_in_gate_command():
    processor, facade, repo, _, _, _ = _fixture_with_clock(0)
    opened = _open(facade)
    processor.onLaneClear(opened.commandId, LANE)
    cycle = repo.get_cycle_by_command(opened.commandId)
    assert cycle.closureStatus == ClosureStatus.CLOSED
    assert repo.commands[cycle.closeCommandId].outcome == CommandOutcome.CLOSED


def test_simultaneous_lane_clear_only_sends_one_close_command():
    processor, facade, repo, _, turnstile, _ = _fixture_with_clock(0)
    opened = _open(facade)
    original_read = repo.get_cycle_by_command
    readers = Barrier(2, timeout=3)

    def independent_database_read(command_id):
        snapshot = deepcopy(original_read(command_id))
        readers.wait()
        return snapshot

    repo.get_cycle_by_command = independent_database_read
    lock = RLock()
    uow = processor._uow
    begin, commit, rollback = uow.begin, uow.commit, uow.rollback

    def locked_begin(timeout_ms=None):
        lock.acquire()
        begin(timeout_ms)

    def locked_commit():
        try:
            commit()
        finally:
            lock.release()

    def locked_rollback():
        try:
            rollback()
        finally:
            lock.release()

    uow.begin, uow.commit, uow.rollback = locked_begin, locked_commit, locked_rollback
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(processor.onLaneClear, opened.commandId, LANE) for _ in range(2)]
        for call in calls:
            call.result(timeout=5)
    assert len(turnstile.close_calls) == 1, f"Sent {len(turnstile.close_calls)} distinct close commands"


def test_pending_close_response_must_not_cancel_an_emergency_hold():
    processor, facade, repo, _, turnstile, _ = _fixture_with_clock(0)
    opened = _open(facade)
    original_close = turnstile.closeGate

    def emergency_before_close_response(**kwargs):
        facade.requestEmergencyRelease("emergency-during-close", LANE)
        assert repo.get_active_cycle_for_lane(LANE).closureStatus == ClosureStatus.EMERGENCY_HOLD
        return original_close(**kwargs)

    turnstile.closeGate = emergency_before_close_response
    processor.onLaneClear(opened.commandId, LANE)
    active = repo.get_active_cycle_for_lane(LANE)
    assert active is not None, "Old close confirmation released a lane whose emergency hold was never reset"
    assert active.closureStatus == ClosureStatus.EMERGENCY_HOLD


def test_post_reset_closure_does_not_reuse_pre_emergency_confirmation():
    processor, facade, repo, _, turnstile, _ = _fixture_with_clock(0)
    opened = _open(facade)
    turnstile.close_outcome = CommandOutcome.UNDETERMINED
    turnstile.query_outcome = CommandOutcome.UNDETERMINED
    processor.onLaneClear(opened.commandId, LANE)
    first_close = repo.get_active_cycle_for_lane(LANE).closeCommandId
    emergency = facade.requestEmergencyRelease("emergency-after-uncertain-close", LANE)
    facade.requestEmergencyReset("emergency-after-uncertain-close", LANE)
    assert repo.get_active_cycle_for_lane(LANE).closureStatus == ClosureStatus.OPENED
    # The pre-emergency close had completed, then emergency release opened the gate.
    # Querying that old command can truthfully return CLOSED without proving a new close.
    turnstile.query_outcome = CommandOutcome.CLOSED
    turnstile.close_outcome = CommandOutcome.CLOSED
    processor.onLaneClear(emergency.commandId, LANE)
    assert len(turnstile.close_calls) == 2, "Post-reset lane was released using only the old close result"
    assert turnstile.close_calls[-1][0] != first_close


def test_undetermined_is_committed_before_one_query():
    processor, facade, repo, _, turnstile, _ = _fixture_with_clock(0)
    turnstile.open_outcome = CommandOutcome.UNDETERMINED
    queried = []

    def query_after_persist(**kwargs):
        assert repo.get_scan("s1").state == ScanState.UNDETERMINED
        assert repo.commands[kwargs["commandId"]].outcome == CommandOutcome.UNDETERMINED
        queried.append(kwargs["commandId"])
        return CommandOutcome.UNDETERMINED

    turnstile.queryStatus = query_after_persist
    assert _open(facade).state == ScanState.PENDING_RECONCILIATION
    assert len(queried) == 1


def test_timely_passage_after_closed_lane_preserves_attendance_and_release():
    processor, facade, repo, attendance, _, _ = _fixture_with_clock(0)
    opened = _open(facade)
    processor.onLaneClear(opened.commandId, LANE)
    processor.onPassage("timely-after-close", opened.commandId, LANE)
    assert len(attendance.list_transactions()) == 1
    assert repo.get_active_cycle_for_lane(LANE) is None


def test_expired_deadline_before_acceptance_neither_claims_nor_sends():
    _, facade, repo, _, turnstile, _ = _fixture_with_clock(0)
    started_at = datetime.now(UTC)
    result = facade.submitScan("expired", "QR-VALID-EXPIRED", LANE, now=started_at, deadline=started_at)
    assert result.state == ScanState.CANNOT_PROCESS
    assert repo.tokens == {}
    assert turnstile.open_calls == []


def test_deadline_budget_reduces_after_time_spent_before_send():
    _, facade, _, _, turnstile, _ = _fixture_with_clock(1_700)
    assert _open(facade).state == ScanState.OPENED
    assert turnstile.open_timeouts == [300]
