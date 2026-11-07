"""[PACKAGE 5] Regressions from the PR #11 review of d3d88e2 (probes written by Khoa Văn).

1. A transport failure combined with an injected fault must stay UNDETERMINED (never CONTROLLER_ERROR),
   so the processor keeps the lane reserved.
2. A status query must match both commandId and laneId.

The adapter and processor logic are real; persistence remains the Package 3 fakes. Not SQLite E2E tests.
"""

import pytest

from test_pkg5_adapter_with_processor import _processor
from tmc_gate.adapters.turnstile_adapter import (
    ControllerApiClient,
    FAULT_CONTROLLER_REJECTS,
    FaultyTurnstileAdapter,
    SimulatedTurnstileAdapter,
)
from tmc_gate.domain.enums import CommandOutcome, ScanState


@pytest.mark.parametrize(
    ("method", "mode_attribute"),
    [
        ("openGate", "simulated_open_mode"),
        ("closeGate", "simulated_close_mode"),
        ("emergencyRelease", "simulated_emergency_mode"),
        ("resetEmergency", "simulated_reset_mode"),
    ],
)
def test_transport_failure_cannot_become_confirmed_nonexecution(method, mode_attribute):
    client = ControllerApiClient()
    setattr(client, mode_attribute, "NETWORK_ERROR")
    adapter = FaultyTurnstileAdapter(client, fault=FAULT_CONTROLLER_REJECTS)
    kwargs = {"commandId": "cmd-network-failure", "laneId": "LANE-BD-01"}
    if method == "openGate":
        kwargs["timeout_ms"] = 700

    outcome = getattr(adapter, method)(**kwargs)

    assert outcome == CommandOutcome.UNDETERMINED, (
        f"{method}: controller raised ConnectionError, but adapter returned {outcome.value}"
    )
    assert len(client.sent_commands()) == 1


def test_processor_keeps_lane_reserved_after_transport_failure_with_fault_injection():
    client = ControllerApiClient()
    client.simulated_open_mode = "NETWORK_ERROR"
    adapter = FaultyTurnstileAdapter(client, fault=FAULT_CONTROLLER_REJECTS)
    processor, scan_repo, _ = _processor(adapter)

    result = processor.processScan("scan-network-failure", "QR-network-failure", "LANE-BD-01")

    assert result.state == ScanState.PENDING_RECONCILIATION, (
        f"Transport failure reported as {result.state.value}; "
        f"active cycle after request: {scan_repo.get_active_cycle_for_lane('LANE-BD-01')}"
    )
    assert scan_repo.get_active_cycle_for_lane("LANE-BD-01") is not None


@pytest.mark.parametrize("query_reply", ["CONFIRMED", "ERROR"])
def test_query_for_wrong_lane_does_not_supply_another_lanes_close_result(query_reply):
    client = ControllerApiClient()
    client.simulated_close_mode = "RECEIPT_ONLY"
    adapter = SimulatedTurnstileAdapter(client)
    assert adapter.closeGate("close-lane-1", "LANE-BD-01") == CommandOutcome.UNDETERMINED
    client.simulated_query_status = query_reply

    wrong_lane = adapter.queryStatus("close-lane-1", "LANE-BD-02")

    assert wrong_lane == CommandOutcome.UNDETERMINED, (
        f"Close command belongs to LANE-BD-01, but query for LANE-BD-02 returned {wrong_lane.value}"
    )
    correct_lane = adapter.queryStatus("close-lane-1", "LANE-BD-01")
    assert correct_lane == (
        CommandOutcome.CLOSED if query_reply == "CONFIRMED" else CommandOutcome.CONTROLLER_ERROR
    )
    assert len(client.sent_commands()) == 1


@pytest.mark.parametrize("fault", ["RECEIPT_ONLY", "CONTROLLER_REJECTS", "TRANSPORT_TIMEOUT"])
@pytest.mark.parametrize(
    ("method", "mode_attribute"),
    [
        ("openGate", "simulated_open_mode"),
        ("closeGate", "simulated_close_mode"),
        ("emergencyRelease", "simulated_emergency_mode"),
        ("resetEmergency", "simulated_reset_mode"),
    ],
)
def test_transport_failure_wins_over_every_injected_fault(fault, method, mode_attribute):
    client = ControllerApiClient()
    setattr(client, mode_attribute, "NETWORK_ERROR")
    adapter = FaultyTurnstileAdapter(client, fault=fault)
    kwargs = {"commandId": "cmd-net", "laneId": "LANE-BD-01"}
    if method == "openGate":
        kwargs["timeout_ms"] = 700

    assert getattr(adapter, method)(**kwargs) == CommandOutcome.UNDETERMINED


def test_query_for_wrong_lane_does_not_confirm_an_open_command():
    client = ControllerApiClient()
    client.simulated_open_mode = "RECEIPT_ONLY"
    adapter = SimulatedTurnstileAdapter(client)
    assert adapter.openGate("open-lane-1", "LANE-BD-01", timeout_ms=700) == CommandOutcome.UNDETERMINED
    client.simulated_query_status = "CONFIRMED"

    assert adapter.queryStatus("open-lane-1", "LANE-BD-02") == CommandOutcome.UNDETERMINED
    assert adapter.queryStatus("open-lane-1", "LANE-BD-01") == CommandOutcome.OPEN_CONFIRMED
