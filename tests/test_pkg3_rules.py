"""[PACKAGE 3] Rule tests for the decisions locked on 05-06/11 (deadline, UNDETERMINED, closeGate once, UoW, emergency, late PASSAGE)."""

from datetime import UTC, datetime, timedelta

import pytest

from test_pkg3_processor import _build_fixture
from tmc_gate.application.processor import GateTransactionProcessor
from tmc_gate.domain.enums import ClosureStatus, CommandOutcome, PassageStatus, ScanState

LANE = "LANE-BD-01"


class _Clock:
    """Fake monotonic clock in ms; each call can advance by a fixed step."""

    def __init__(self, step_ms: int = 0) -> None:
        self.value = 0
        self.step_ms = step_ms

    def __call__(self) -> int:
        current = self.value
        self.value += self.step_ms
        return current


def _fixture_with_clock(step_ms: int):
    processor, facade, uow, scan_repo, att_repo, turnstile, outbox, obs = _build_fixture()
    processor._monotonic_ms = _Clock(step_ms)  # noqa: SLF001 - test seam
    return processor, facade, scan_repo, att_repo, turnstile, obs


def _open(facade, scan_id="s1", token="QR-VALID-1", deadline_ms=2_000):
    now = datetime.now(UTC)
    return facade.submitScan(
        scanId=scan_id, qrToken=token, laneId=LANE, now=now, deadline=now + timedelta(milliseconds=deadline_ms)
    )


# --- openGate budget and deadline (Flow A) ---

def test_open_gate_receives_700ms_when_enough_time_remains() -> None:
    _, facade, _, _, turnstile, _ = _fixture_with_clock(step_ms=0)
    res = _open(facade, deadline_ms=2_000)
    assert res.state == ScanState.OPENED
    assert turnstile.open_timeouts == [700]


def test_open_gate_receives_remaining_time_when_below_700ms() -> None:
    _, facade, _, _, turnstile, _ = _fixture_with_clock(step_ms=0)
    _open(facade, deadline_ms=300)
    assert turnstile.open_timeouts == [300]


def test_deadline_expired_after_accepted_releases_lane_keeps_token_claimed() -> None:
    # The clock jumps 3 s between the start of processScan and the moment before openGate
    _, facade, scan_repo, _, turnstile, _ = _fixture_with_clock(step_ms=3_000)
    res = _open(facade, deadline_ms=2_000)
    assert res.state == ScanState.CANNOT_PROCESS
    assert res.reason == "OPEN_DEADLINE_EXPIRED_BEFORE_COMMAND"
    assert turnstile.open_calls == []
    assert scan_repo.get_active_cycle_for_lane(LANE) is None  # lane released
    assert "tok-QR-VALID-1" in scan_repo.tokens  # token stays claimed
    # Same QR again with a new scanId is rejected as replay
    again = _open(facade, scan_id="s2", deadline_ms=2_000)
    assert again.state == ScanState.REJECTED


# --- UNDETERMINED handling ---

def test_undetermined_then_query_undetermined_goes_to_pending_reconciliation() -> None:
    _, facade, scan_repo, _, turnstile, _ = _fixture_with_clock(step_ms=0)
    turnstile.open_outcome = CommandOutcome.UNDETERMINED
    turnstile.query_outcome = CommandOutcome.UNDETERMINED
    res = _open(facade)
    assert res.state == ScanState.PENDING_RECONCILIATION
    assert scan_repo.get_active_cycle_for_lane(LANE) is not None  # lane kept
    assert "tok-QR-VALID-1" in scan_repo.tokens  # token kept


def test_undetermined_then_query_open_confirmed_becomes_opened() -> None:
    _, facade, _, _, turnstile, _ = _fixture_with_clock(step_ms=0)
    turnstile.open_outcome = CommandOutcome.UNDETERMINED
    turnstile.query_outcome = CommandOutcome.OPEN_CONFIRMED
    assert _open(facade).state == ScanState.OPENED


def test_uncertain_open_is_never_resent_for_same_scan_id() -> None:
    _, facade, _, _, turnstile, _ = _fixture_with_clock(step_ms=0)
    turnstile.open_outcome = CommandOutcome.UNDETERMINED
    _open(facade)
    _open(facade)  # kiosk retries with the same scanId
    assert len(turnstile.open_calls) == 1


def test_undetermined_without_query_port_goes_to_pending_reconciliation() -> None:
    processor, facade, _, _, turnstile, _ = _fixture_with_clock(step_ms=0)
    processor._status_query_port = None  # noqa: SLF001
    turnstile.open_outcome = CommandOutcome.UNDETERMINED
    assert _open(facade).state == ScanState.PENDING_RECONCILIATION


# --- closeGate exactly once ---

def test_duplicate_lane_clear_sends_close_gate_once() -> None:
    processor, facade, scan_repo, _, turnstile, _ = _fixture_with_clock(step_ms=0)
    res = _open(facade)
    processor.onLaneClear(commandId=res.commandId, laneId=LANE)
    processor.onLaneClear(commandId=res.commandId, laneId=LANE)
    assert len(turnstile.close_calls) == 1


def test_lane_clear_after_close_uncertain_queries_without_new_close() -> None:
    processor, facade, scan_repo, _, turnstile, _ = _fixture_with_clock(step_ms=0)
    res = _open(facade)
    turnstile.close_outcome = CommandOutcome.UNDETERMINED
    turnstile.query_outcome = CommandOutcome.UNDETERMINED
    processor.onLaneClear(commandId=res.commandId, laneId=LANE)
    cycle = scan_repo.get_cycle_by_command(res.commandId)
    assert cycle.closureStatus == ClosureStatus.CLOSE_UNCERTAIN
    first_close_id = cycle.closeCommandId
    turnstile.query_outcome = CommandOutcome.CLOSED
    processor.onLaneClear(commandId=res.commandId, laneId=LANE)
    assert len(turnstile.close_calls) == 1
    assert cycle.closeCommandId == first_close_id
    assert cycle.closureStatus == ClosureStatus.CLOSED
    assert scan_repo.get_active_cycle_for_lane(LANE) is None


# --- passage ---

def test_duplicate_passage_event_id_is_ignored() -> None:
    processor, facade, _, att_repo, _, _ = _fixture_with_clock(step_ms=0)
    res = _open(facade)
    processor.onPassage("pas-1", res.commandId, LANE)
    processor.onPassage("pas-1", res.commandId, LANE)
    assert len(att_repo.list_transactions()) == 1


def test_second_distinct_passage_is_kept_but_not_counted() -> None:
    processor, facade, _, att_repo, _, obs = _fixture_with_clock(step_ms=0)
    res = _open(facade)
    processor.onPassage("pas-1", res.commandId, LANE)
    processor.onPassage("pas-2", res.commandId, LANE)
    assert len(att_repo.list_transactions()) == 1
    assert {"pas-1", "pas-2"} <= set(att_repo.passages)
    assert any(a.get("alert") == "SECOND_PASSAGE_ANOMALY" for a in obs.alerts)



def test_late_passage_after_no_passage_is_stored_for_review_without_auto_attendance() -> None:
    processor, facade, scan_repo, att_repo, turnstile, obs = _fixture_with_clock(step_ms=0)
    res = _open(facade)
    cycle = scan_repo.get_cycle_by_command(res.commandId)
    assert processor.handlePassageTimeout(cycle.cycleId) is True
    close_before = len(turnstile.close_calls)

    processor.onPassage("pas-late", res.commandId, LANE)
    processor.onPassage("pas-late", res.commandId, LANE)  # repeated signal is ignored

    assert att_repo.list_transactions() == []
    assert att_repo.passages["pas-late"].reviewReason == "LATE_PASSAGE"
    assert len(att_repo.list_passages_for_cycle(cycle.cycleId)) == 1
    after = scan_repo.get_cycle(cycle.cycleId)
    assert after.passageStatus == PassageStatus.NO_PASSAGE  # timer not restarted
    assert scan_repo.get_scan("s1").state == ScanState.PENDING_RECONCILIATION
    assert len(turnstile.close_calls) == close_before  # no closeGate from a late passage
    assert sum(a.get("alert") == "LATE_PASSAGE_FOR_REVIEW" for a in obs.alerts) == 1


def test_passage_after_emergency_reconciliation_is_kept_without_attendance() -> None:
    processor, facade, scan_repo, att_repo, _, _ = _fixture_with_clock(step_ms=0)
    res = _open(facade)
    facade.requestEmergencyRelease("case-emg-2", LANE)
    assert scan_repo.get_scan("s1").state == ScanState.PENDING_RECONCILIATION

    processor.onPassage("pas-emg", res.commandId, LANE)  # still inside the 15-second window

    assert att_repo.list_transactions() == []
    assert att_repo.passages["pas-emg"].reviewReason == "LATE_PASSAGE"


def test_timely_passage_still_creates_one_attendance() -> None:
    processor, facade, _, att_repo, _, _ = _fixture_with_clock(step_ms=0)
    res = _open(facade)
    processor.onPassage("pas-ok", res.commandId, LANE)
    assert len(att_repo.list_transactions()) == 1
    assert att_repo.passages["pas-ok"].reviewReason is None


# --- UoW and emergency ---

def test_processor_requires_unit_of_work() -> None:
    with pytest.raises(TypeError):
        GateTransactionProcessor(  # type: ignore[call-arg]
            factory_id="BD",
            access_decision=None,
            scan_repo=None,
            attendance_repo=None,
            open_gate_port=None,
            close_gate_port=None,
            emergency_gate_port=None,
            audit_api=None,
            health_api=None,
        )


def test_emergency_release_keeps_scan_link_of_running_cycle() -> None:
    _, facade, scan_repo, att_repo, _, _ = _fixture_with_clock(step_ms=0)
    res = _open(facade)
    cycle_before = scan_repo.get_cycle_by_command(res.commandId)
    facade.requestEmergencyRelease("case-emg-1", LANE)
    cycle_after = scan_repo.get_active_cycle_for_lane(LANE)
    assert cycle_after.cycleId == cycle_before.cycleId
    assert cycle_after.scanId == "s1"
    assert cycle_after.closureStatus == ClosureStatus.EMERGENCY_HOLD
    assert scan_repo.get_scan("s1").state == ScanState.PENDING_RECONCILIATION


def test_assisted_open_uses_its_own_timeout_not_700ms() -> None:
    processor, facade, scan_repo, _, turnstile, _ = _fixture_with_clock(step_ms=0)
    from tmc_gate.domain.models import SupportCase

    case = SupportCase(
        supportCaseId="case-ast-1",
        factoryId="BD",
        laneId=LANE,
        guardId="G-1",
        reason="NO_PHONE",
        occurredAt=datetime.now(UTC),
    )
    facade.recordIntervention(case)
    facade.requestAssistedOpen("case-ast-1", LANE)
    assert turnstile.open_timeouts == [processor.ASSISTED_OPEN_TIMEOUT_MS]
    assert processor.ASSISTED_OPEN_TIMEOUT_MS != processor.OPEN_COMMAND_BUDGET_MS


def test_assisted_open_default_timeout_is_3000ms() -> None:
    assert GateTransactionProcessor.ASSISTED_OPEN_TIMEOUT_MS == 3_000  # Table 5.5, decided 06/11
    assert GateTransactionProcessor.OPEN_COMMAND_BUDGET_MS == 700  # Flow A only
