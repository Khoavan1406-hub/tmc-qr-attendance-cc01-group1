"""PR2 follow-up regressions (Codex review 06/11): stale close results, PASSAGE vs emergency in one transaction, confirmed close history, command issuedAt."""

from copy import deepcopy

from test_pkg3_rules import LANE, _fixture_with_clock, _open
from tmc_gate.domain.enums import ClosureStatus, CommandOutcome, ScanState


def test_old_close_result_cannot_release_lane_reserved_by_new_scan():
    processor, facade, repo, _, turnstile, _ = _fixture_with_clock(0)
    # Return a copy, as a real database read would.
    repo.get_command = lambda command_id: deepcopy(repo.commands.get(command_id))
    first = _open(facade)
    second_cycle_id = []
    original_close = turnstile.closeGate

    def return_old_close_after_lane_reused(**kwargs):
        result = original_close(**kwargs)
        # A concurrent duplicate signal queries the command while the first caller
        # still waits for its response. The query confirms closure and frees the lane.
        turnstile.query_outcome = CommandOutcome.CLOSED
        processor.onLaneClear(first.commandId, LANE)
        assert repo.get_active_cycle_for_lane(LANE) is None
        second = _open(facade, scan_id="s2", token="QR-VALID-2")
        assert second.state == ScanState.OPENED
        second_cycle_id.append(repo.get_active_cycle_for_lane(LANE).cycleId)
        return result

    turnstile.closeGate = return_old_close_after_lane_reused
    processor.onLaneClear(first.commandId, LANE)
    active = repo.get_active_cycle_for_lane(LANE)
    assert active is not None, "The first command's late result cleared the new scan's reservation"
    assert active.cycleId == second_cycle_id[0]


def test_passage_must_recheck_scan_after_emergency_before_transaction():
    processor, facade, repo, attendance, _, _ = _fixture_with_clock(0)
    opened = _open(facade)
    original_cycle_read = repo.get_cycle_by_command
    original_scan_read = repo.get_scan
    # SQL reads return detached data, not a shared mutable dictionary object.
    repo.get_cycle_by_command = lambda command_id: deepcopy(original_cycle_read(command_id))
    repo.get_scan = lambda scan_id: deepcopy(original_scan_read(scan_id))
    uow = processor._uow
    original_begin = uow.begin
    emergency_done = []

    def emergency_wins_before_passage_transaction(timeout_ms=None):
        uow.begin = original_begin
        facade.requestEmergencyRelease("case-concurrent-emergency", LANE)
        assert repo.get_scan("s1").state == ScanState.PENDING_RECONCILIATION
        assert repo.get_active_cycle_for_lane(LANE).closureStatus == ClosureStatus.EMERGENCY_HOLD
        emergency_done.append(True)
        return original_begin(timeout_ms)

    uow.begin = emergency_wins_before_passage_transaction
    processor.onPassage("passage-concurrent-with-emergency", opened.commandId, LANE)
    assert emergency_done
    assert attendance.list_transactions() == [], "PASSAGE used an OPENED snapshot read before emergency committed"
    assert repo.get_active_cycle_for_lane(LANE).closureStatus == ClosureStatus.EMERGENCY_HOLD
    assert attendance.passages["passage-concurrent-with-emergency"].reviewReason == "LATE_PASSAGE"


def test_recorded_command_keeps_issued_at_with_current_fake_repository():
    processor, facade, repo, _, turnstile, _ = _fixture_with_clock(0)
    opened = _open(facade)
    original_close = turnstile.closeGate
    original_issued_at = []

    def record_issue_time(**kwargs):
        original_issued_at.append(repo.commands[kwargs["commandId"]].issuedAt)
        return original_close(**kwargs)

    turnstile.closeGate = record_issue_time
    processor.onLaneClear(opened.commandId, LANE)
    cycle = repo.get_cycle_by_command(opened.commandId)
    assert original_issued_at[0] is not None
    assert repo.commands[cycle.closeCommandId].issuedAt == original_issued_at[0], "Fallback command reconstruction discarded issuedAt"


def test_unknown_late_reply_does_not_replace_confirmed_command_history():
    processor, facade, repo, _, turnstile, _ = _fixture_with_clock(0)
    repo.get_command = lambda command_id: deepcopy(repo.commands.get(command_id))
    opened = _open(facade)
    query_replies = iter([CommandOutcome.CLOSED, CommandOutcome.UNDETERMINED])
    turnstile.queryStatus = lambda **kwargs: next(query_replies)

    def delayed_unknown_after_query_confirmed(**kwargs):
        turnstile.close_calls.append((kwargs["commandId"], kwargs["laneId"]))
        processor.onLaneClear(opened.commandId, LANE)
        assert repo.commands[kwargs["commandId"]].outcome == CommandOutcome.CLOSED
        return CommandOutcome.UNDETERMINED

    turnstile.closeGate = delayed_unknown_after_query_confirmed
    # The duplicate signal's query confirms closure first. The original caller
    # then receives an uncertain response and its subsequent query also times out.
    processor.onLaneClear(opened.commandId, LANE)
    cycle = repo.get_cycle_by_command(opened.commandId)
    assert cycle.closureStatus == ClosureStatus.CLOSED
    assert repo.commands[cycle.closeCommandId].outcome == CommandOutcome.CLOSED


def test_provided_get_command_preserves_issued_at():
    processor, facade, repo, _, turnstile, _ = _fixture_with_clock(0)
    repo.get_command = lambda command_id: deepcopy(repo.commands.get(command_id))
    opened = _open(facade)
    original_close = turnstile.closeGate
    original_issued_at = []

    def record_issue_time(**kwargs):
        original_issued_at.append(repo.commands[kwargs["commandId"]].issuedAt)
        return original_close(**kwargs)

    turnstile.closeGate = record_issue_time
    processor.onLaneClear(opened.commandId, LANE)
    cycle = repo.get_cycle_by_command(opened.commandId)
    assert repo.commands[cycle.closeCommandId].issuedAt == original_issued_at[0]
