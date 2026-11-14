"""[PACKAGE 5] Contract tests illustrating LSP with SimulatedTurnstileAdapter and FaultyTurnstileAdapter (§7.1.3 LSP).

Mandatory part: every ITurnstileControl implementation must pass the same scenarios.
Optional part: only adapters that implement IControllerStatusQuery are checked for queryStatus.
"""

from collections.abc import Callable

import pytest

from tmc_gate.adapters.turnstile_adapter import (
    ACTION_CLOSE,
    ACTION_EMERGENCY_RELEASE,
    ACTION_OPEN,
    ACTION_RESET,
    FAULT_CONTROLLER_REJECTS,
    FAULT_RECEIPT_ONLY,
    FAULT_TRANSPORT_TIMEOUT,
    ControllerApiClient,
    FaultyTurnstileAdapter,
    SimulatedTurnstileAdapter,
)
from tmc_gate.domain.enums import CommandOutcome
from tmc_gate.ports.turnstile import (
    CLOSE_RESULTS,
    OPEN_RESULTS,
    RELEASE_RESULTS,
    RESET_RESULTS,
    IControllerStatusQuery,
    IPassageEvents,
    ITurnstileControl,
)

FLOW_A_TIMEOUT_MS = 700

AdapterFactory = Callable[[ControllerApiClient], ITurnstileControl]

COMPLIANT_ADAPTERS: dict[str, AdapterFactory] = {
    "simulated": lambda client: SimulatedTurnstileAdapter(client),
    "faulty-no-fault": lambda client: FaultyTurnstileAdapter(client),
}

RESULT_SETS = {
    ACTION_OPEN: OPEN_RESULTS,
    ACTION_CLOSE: CLOSE_RESULTS,
    ACTION_EMERGENCY_RELEASE: RELEASE_RESULTS,
    ACTION_RESET: RESET_RESULTS,
}
CONFIRMED = {
    ACTION_OPEN: ("OPEN_CONFIRMED", CommandOutcome.OPEN_CONFIRMED),
    ACTION_CLOSE: ("CLOSED", CommandOutcome.CLOSED),
    ACTION_EMERGENCY_RELEASE: ("EMERGENCY_RELEASED", CommandOutcome.EMERGENCY_RELEASED),
    ACTION_RESET: ("RESET_CONFIRMED", CommandOutcome.RESET_CONFIRMED),
}
MODE_ATTR = {
    ACTION_OPEN: "simulated_open_mode",
    ACTION_CLOSE: "simulated_close_mode",
    ACTION_EMERGENCY_RELEASE: "simulated_emergency_mode",
    ACTION_RESET: "simulated_reset_mode",
}


def _call(adapter: ITurnstileControl, action: str, command_id: str = "cmd-1", lane_id: str = "LANE-BD-01") -> CommandOutcome:
    if action == ACTION_OPEN:
        return adapter.openGate(command_id, lane_id, timeout_ms=FLOW_A_TIMEOUT_MS)
    if action == ACTION_CLOSE:
        return adapter.closeGate(command_id, lane_id)
    if action == ACTION_EMERGENCY_RELEASE:
        return adapter.emergencyRelease(command_id, lane_id)
    return adapter.resetEmergency(command_id, lane_id)


def _scenarios() -> list[tuple[str, str, CommandOutcome]]:
    rows: list[tuple[str, str, CommandOutcome]] = []
    for action, (confirmed_raw, confirmed_outcome) in CONFIRMED.items():
        rows.append((action, confirmed_raw, confirmed_outcome))
        rows.append((action, "RECEIPT_ONLY", CommandOutcome.UNDETERMINED))
        rows.append((action, "TIMEOUT", CommandOutcome.UNDETERMINED))
        rows.append((action, "NETWORK_ERROR", CommandOutcome.UNDETERMINED))
        rows.append((action, "ERROR", CommandOutcome.CONTROLLER_ERROR))
    return rows


# --- Mandatory contract (ITurnstileControl), run against every compliant adapter ---


@pytest.mark.parametrize("adapter_name", COMPLIANT_ADAPTERS)
@pytest.mark.parametrize(("action", "mode", "expected"), _scenarios())
def test_contract_maps_controller_reply_to_port_result(
    adapter_name: str, action: str, mode: str, expected: CommandOutcome
) -> None:
    client = ControllerApiClient()
    setattr(client, MODE_ATTR[action], mode)
    adapter = COMPLIANT_ADAPTERS[adapter_name](client)

    outcome = _call(adapter, action)

    assert outcome == expected
    assert outcome in RESULT_SETS[action]
    # Exactly one command reached the controller; the adapter never retries on its own
    assert [c["action"] for c in client.sent_commands()] == [action]


@pytest.mark.parametrize("adapter_name", COMPLIANT_ADAPTERS)
def test_contract_receipt_only_is_never_treated_as_opened(adapter_name: str) -> None:
    client = ControllerApiClient()
    client.simulated_open_mode = "RECEIPT_ONLY"
    adapter = COMPLIANT_ADAPTERS[adapter_name](client)

    assert adapter.openGate("cmd-r", "LANE-BD-01", timeout_ms=FLOW_A_TIMEOUT_MS) == CommandOutcome.UNDETERMINED


@pytest.mark.parametrize("adapter_name", COMPLIANT_ADAPTERS)
def test_contract_timeout_is_never_controller_error(adapter_name: str) -> None:
    client = ControllerApiClient()
    client.simulated_open_mode = "TIMEOUT"
    adapter = COMPLIANT_ADAPTERS[adapter_name](client)

    outcome = adapter.openGate("cmd-t", "LANE-BD-01", timeout_ms=FLOW_A_TIMEOUT_MS)

    assert outcome == CommandOutcome.UNDETERMINED
    assert outcome != CommandOutcome.CONTROLLER_ERROR


@pytest.mark.parametrize("adapter_name", COMPLIANT_ADAPTERS)
@pytest.mark.parametrize(
    ("latency_ms", "timeout_ms", "expected"),
    [
        (700, 700, CommandOutcome.OPEN_CONFIRMED),  # reply exactly at the deadline still counts
        (701, 700, CommandOutcome.UNDETERMINED),  # late reply = no reply within timeout_ms
        (250, 300, CommandOutcome.OPEN_CONFIRMED),  # processor passes min(700, remaining) = 300
        (350, 300, CommandOutcome.UNDETERMINED),
        (2_500, 3_000, CommandOutcome.OPEN_CONFIRMED),  # assisted opening uses 3000 ms
    ],
)
def test_contract_open_gate_respects_timeout_ms(
    adapter_name: str, latency_ms: int, timeout_ms: int, expected: CommandOutcome
) -> None:
    client = ControllerApiClient()
    client.simulated_latency_ms = latency_ms
    adapter = COMPLIANT_ADAPTERS[adapter_name](client)

    assert adapter.openGate("cmd-lat", "LANE-BD-01", timeout_ms=timeout_ms) == expected


@pytest.mark.parametrize("adapter_factory", [SimulatedTurnstileAdapter, FaultyTurnstileAdapter])
def test_contract_other_commands_use_their_own_configurable_timeout(adapter_factory: type) -> None:
    client = ControllerApiClient()
    client.simulated_latency_ms = 1_500
    short = adapter_factory(client, command_timeout_ms=1_000)
    long = adapter_factory(client, command_timeout_ms=2_000)

    assert short.closeGate("cmd-c1", "LANE-BD-01") == CommandOutcome.UNDETERMINED
    assert long.closeGate("cmd-c2", "LANE-BD-01") == CommandOutcome.CLOSED
    assert short.resetEmergency("cmd-r1", "LANE-BD-01") == CommandOutcome.UNDETERMINED
    assert long.emergencyRelease("cmd-e1", "LANE-BD-01") == CommandOutcome.EMERGENCY_RELEASED
    with pytest.raises(ValueError):
        adapter_factory(client, command_timeout_ms=0)


@pytest.mark.parametrize("adapter_name", COMPLIANT_ADAPTERS)
def test_contract_adapter_never_synthesizes_passage(adapter_name: str) -> None:
    client = ControllerApiClient()
    adapter = COMPLIANT_ADAPTERS[adapter_name](client)

    for action in CONFIRMED:
        _call(adapter, action, command_id=f"cmd-{action}")

    assert not isinstance(adapter, IPassageEvents)
    assert not hasattr(adapter, "onPassage") and not hasattr(adapter, "onLaneClear")
    assert {c["action"] for c in client.command_log} == set(CONFIRMED)  # only the commands, no PASSAGE entries


# --- FaultyTurnstileAdapter: injected faults stay inside the contract ---


@pytest.mark.parametrize("action", list(CONFIRMED))
@pytest.mark.parametrize(
    ("fault", "expected"),
    [
        (FAULT_RECEIPT_ONLY, CommandOutcome.UNDETERMINED),
        (FAULT_CONTROLLER_REJECTS, CommandOutcome.CONTROLLER_ERROR),
        (FAULT_TRANSPORT_TIMEOUT, CommandOutcome.UNDETERMINED),
    ],
)
def test_faulty_adapter_injected_faults_return_only_contract_results(
    action: str, fault: str, expected: CommandOutcome
) -> None:
    client = ControllerApiClient()  # controller itself would confirm; the fault overrides the reply
    adapter = FaultyTurnstileAdapter(client, fault=fault)

    outcome = _call(adapter, action)

    assert outcome == expected
    assert outcome in RESULT_SETS[action]
    assert [c["action"] for c in client.sent_commands()] == [action]  # a fault never means "not sent"


def test_faulty_adapter_fault_can_target_one_action() -> None:
    client = ControllerApiClient()
    adapter = FaultyTurnstileAdapter(client, fault=FAULT_RECEIPT_ONLY, fault_actions={ACTION_CLOSE})

    assert adapter.openGate("cmd-o", "LANE-BD-01", timeout_ms=FLOW_A_TIMEOUT_MS) == CommandOutcome.OPEN_CONFIRMED
    assert adapter.closeGate("cmd-c", "LANE-BD-01") == CommandOutcome.UNDETERMINED


def test_faulty_adapter_rejects_unknown_fault() -> None:
    with pytest.raises(ValueError):
        FaultyTurnstileAdapter(ControllerApiClient(), fault="OPEN_ANYWAY")


# --- Optional contract (IControllerStatusQuery) ---


def test_only_simulated_adapter_supports_status_query() -> None:
    client = ControllerApiClient()
    assert isinstance(SimulatedTurnstileAdapter(client), IControllerStatusQuery)
    assert not isinstance(FaultyTurnstileAdapter(client), IControllerStatusQuery)


@pytest.mark.parametrize("action", list(CONFIRMED))
@pytest.mark.parametrize(
    ("query_mode", "confirmed"),
    [("UNDETERMINED", False), ("CONFIRMED", True), ("ERROR", None), ("NETWORK_ERROR", False)],
)
def test_optional_query_status_returns_result_of_the_original_command(
    action: str, query_mode: str, confirmed: bool | None
) -> None:
    client = ControllerApiClient()
    setattr(client, MODE_ATTR[action], "RECEIPT_ONLY")
    adapter = SimulatedTurnstileAdapter(client)
    assert _call(adapter, action, command_id="cmd-q") == CommandOutcome.UNDETERMINED

    client.simulated_query_status = query_mode
    outcome = adapter.queryStatus("cmd-q", "LANE-BD-01")

    if confirmed is True:
        assert outcome == CONFIRMED[action][1]
    elif confirmed is None:
        assert outcome == CommandOutcome.CONTROLLER_ERROR
    else:
        assert outcome == CommandOutcome.UNDETERMINED
    assert outcome in RESULT_SETS[action]
    # Querying never resends the command
    assert len(client.sent_commands()) == 1


def test_optional_query_status_for_unknown_command_is_undetermined() -> None:
    client = ControllerApiClient()
    client.simulated_query_status = "CONFIRMED"
    adapter = SimulatedTurnstileAdapter(client)

    assert adapter.queryStatus("never-sent", "LANE-BD-01") == CommandOutcome.UNDETERMINED


# --- LSP counter-example (illustration only, not part of the mandatory suite) ---


class ReceiptAsOpenedAdapter(SimulatedTurnstileAdapter):
    """Violates LSP: treats a receipt as OPEN_CONFIRMED, so a processor would record OPENED for a closed gate."""

    def openGate(self, commandId: str, laneId: str, timeout_ms: int) -> CommandOutcome:
        if self._client.simulated_open_mode == "RECEIPT_ONLY":
            self._client.send_command(ACTION_OPEN, commandId, laneId)
            return CommandOutcome.OPEN_CONFIRMED
        return super().openGate(commandId, laneId, timeout_ms)


@pytest.mark.xfail(strict=True, reason="LSP counter-example: receipt treated as opened breaks the contract")
def test_lsp_violating_adapter_fails_the_same_contract() -> None:
    client = ControllerApiClient()
    client.simulated_open_mode = "RECEIPT_ONLY"
    adapter = ReceiptAsOpenedAdapter(client)

    assert adapter.openGate("cmd-v", "LANE-BD-01", timeout_ms=FLOW_A_TIMEOUT_MS) == CommandOutcome.UNDETERMINED
