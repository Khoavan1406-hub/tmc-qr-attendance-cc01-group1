"""[PACKAGE 5] GateTransactionProcessor driven through the real M2 adapters instead of FakeTurnstilePort.

Repositories and UnitOfWork are still the Package 3 in-memory doubles (SQLite is Package 4), so this is an
adapter integration test, not the Flow A E2E test on SQLite.
"""

from datetime import UTC, datetime

from test_pkg3_processor import (
    FakeAccessDecision,
    FakeAttendanceRepo,
    FakeAuditAndHealth,
    FakeScanRepo,
    FakeSnapshotRepo,
    FakeUnitOfWork,
)
from tmc_gate.adapters.turnstile_adapter import (
    ACTION_CLOSE,
    ACTION_EMERGENCY_RELEASE,
    ACTION_OPEN,
    ACTION_RESET,
    FAULT_CONTROLLER_REJECTS,
    FAULT_RECEIPT_ONLY,
    ControllerApiClient,
    FaultyTurnstileAdapter,
    SimulatedTurnstileAdapter,
)
from tmc_gate.application.processor import GateTransactionProcessor
from tmc_gate.domain.enums import ClosureStatus, ScanState
from tmc_gate.domain.models import SupportCase
from tmc_gate.ports.turnstile import IControllerStatusQuery, ITurnstileControl


def _processor(adapter: ITurnstileControl) -> tuple[GateTransactionProcessor, FakeScanRepo, FakeAttendanceRepo]:
    scan_repo = FakeScanRepo()
    att_repo = FakeAttendanceRepo(scan_repo)
    obs = FakeAuditAndHealth()
    processor = GateTransactionProcessor(
        factory_id="BD",
        access_decision=FakeAccessDecision(),
        scan_repo=scan_repo,
        attendance_repo=att_repo,
        open_gate_port=adapter,
        close_gate_port=adapter,
        emergency_gate_port=adapter,
        audit_api=obs,
        health_api=obs,
        uow=FakeUnitOfWork(),
        status_query_port=adapter if isinstance(adapter, IControllerStatusQuery) else None,
        snapshot_repo=FakeSnapshotRepo(),
    )
    return processor, scan_repo, att_repo


def _actions(client: ControllerApiClient) -> list[str]:
    return [c["action"] for c in client.sent_commands()]


def test_flow_a_with_simulated_adapter_open_passage_lane_clear_close() -> None:
    client = ControllerApiClient()
    processor, scan_repo, att_repo = _processor(SimulatedTurnstileAdapter(client))

    result = processor.processScan(scanId="scan-p5-1", qrToken="QR-P5-1", laneId="LANE-BD-01")
    assert result.state == ScanState.OPENED
    assert len(att_repo.list_transactions()) == 0  # opening alone is not attendance

    processor.onPassage(passageEventId="pas-p5-1", commandId=result.commandId, laneId="LANE-BD-01")
    assert att_repo.get_by_scan_id("scan-p5-1") is not None

    processor.onLaneClear(commandId=result.commandId, laneId="LANE-BD-01")
    processor.onLaneClear(commandId=result.commandId, laneId="LANE-BD-01")  # repeated LANE_CLEAR

    assert _actions(client) == [ACTION_OPEN, ACTION_CLOSE]  # closeGate sent exactly once
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01") is None


def test_flow_a_receipt_only_queries_once_and_never_resends_open() -> None:
    client = ControllerApiClient()
    client.simulated_open_mode = "RECEIPT_ONLY"
    processor, scan_repo, _ = _processor(SimulatedTurnstileAdapter(client))

    result = processor.processScan(scanId="scan-p5-2", qrToken="QR-P5-2", laneId="LANE-BD-01")

    assert result.state == ScanState.PENDING_RECONCILIATION
    assert _actions(client) == [ACTION_OPEN]
    assert [e["kind"] for e in client.command_log] == ["COMMAND", "QUERY"]
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01") is not None  # lane stays reserved


def test_flow_a_late_controller_reply_beyond_700ms_is_undetermined() -> None:
    client = ControllerApiClient()
    client.simulated_latency_ms = 900  # longer than min(700 ms, remaining)
    processor, _, _ = _processor(SimulatedTurnstileAdapter(client))

    result = processor.processScan(scanId="scan-p5-3", qrToken="QR-P5-3", laneId="LANE-BD-01")

    assert result.state == ScanState.PENDING_RECONCILIATION
    assert _actions(client) == [ACTION_OPEN]


def test_flow_a_controller_error_with_faulty_adapter() -> None:
    client = ControllerApiClient()
    processor, _, att_repo = _processor(FaultyTurnstileAdapter(client, fault=FAULT_CONTROLLER_REJECTS))

    result = processor.processScan(scanId="scan-p5-4", qrToken="QR-P5-4", laneId="LANE-BD-01")

    assert result.state == ScanState.CONTROLLER_ERROR
    assert len(att_repo.list_transactions()) == 0


def test_flow_a_faulty_adapter_without_query_goes_to_pending_reconciliation() -> None:
    client = ControllerApiClient()
    processor, _, _ = _processor(FaultyTurnstileAdapter(client, fault=FAULT_RECEIPT_ONLY))

    result = processor.processScan(scanId="scan-p5-5", qrToken="QR-P5-5", laneId="LANE-BD-01")

    assert result.state == ScanState.PENDING_RECONCILIATION
    assert [e["kind"] for e in client.command_log] == ["COMMAND"]  # no query port, no resend


def test_flow_b_emergency_release_and_reset_through_simulated_adapter() -> None:
    client = ControllerApiClient()
    processor, scan_repo, _ = _processor(SimulatedTurnstileAdapter(client))
    case = SupportCase(
        supportCaseId="emg-p5-1",
        factoryId="BD",
        laneId="LANE-BD-01",
        guardId="GUARD-BD-01",
        reason="Fire alarm",
        occurredAt=datetime.now(UTC),
        isEmergency=True,
    )
    assert processor.recordIntervention(case=case) is True

    released = processor.requestEmergencyRelease(supportCaseId="emg-p5-1", laneId="LANE-BD-01")
    assert released.state == ScanState.OPENED
    cycle = scan_repo.get_active_cycle_for_lane("LANE-BD-01")
    assert cycle is not None and cycle.closureStatus == ClosureStatus.EMERGENCY_HOLD

    # Reset not confirmed -> lane stays in EMERGENCY_HOLD
    client.simulated_reset_mode = "TIMEOUT"
    pending = processor.requestEmergencyReset(supportCaseId="emg-p5-1", laneId="LANE-BD-01")
    assert pending.state != ScanState.OPENED
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01").closureStatus == ClosureStatus.EMERGENCY_HOLD

    # Reset confirmed -> lane leaves EMERGENCY_HOLD
    # Repeating the request queries the stored RESET command; it is not sent again with a new commandId
    client.simulated_query_status = "CONFIRMED"
    repeat = processor.requestEmergencyReset(supportCaseId="emg-p5-1", laneId="LANE-BD-01")
    assert repeat.commandId == pending.commandId
    assert _actions(client).count(ACTION_RESET) == 1
    after = scan_repo.get_active_cycle_for_lane("LANE-BD-01")
    assert after is None or after.closureStatus != ClosureStatus.EMERGENCY_HOLD
    assert _actions(client)[0] == ACTION_EMERGENCY_RELEASE
    assert ACTION_RESET in _actions(client)
