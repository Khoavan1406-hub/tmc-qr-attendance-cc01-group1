"""[PACKAGE 5] M2 Turnstile Integration module (Figure 4.3, Table 4.8, §4.3, §7.1.3 LSP, ADR-04).

Contains:
- ControllerApiClient: low-level simulator of the vendor controller API with configurable scenarios
  (confirmed, RECEIPT_ONLY, TIMEOUT, ERROR, NETWORK_ERROR) and a simulated response latency.
- SimulatedTurnstileAdapter: compliant adapter implementing ITurnstileControl and the optional IControllerStatusQuery.
- FaultyTurnstileAdapter: compliant fault-injecting adapter; passes the same contract tests (§7.1.3 LSP)
  and does not implement IControllerStatusQuery.

Result mapping shared by both adapters (the LSP contract in ports/turnstile.py):
- controller confirms execution          -> OPEN_CONFIRMED | CLOSED | EMERGENCY_RELEASED | RESET_CONFIRMED
- controller confirms NON-execution      -> CONTROLLER_ERROR
- receipt only, timeout, transport error,
  unknown reply, or reply later than the
  command timeout                        -> UNDETERMINED
RECEIPT_ONLY is a raw controller reply inside the adapter; it is never returned by a port.
The adapters never synthesize PASSAGE or LANE_CLEAR: those signals reach Gate Core through IPassageEvents.

This is a simulator: latency is compared with the timeout instead of sleeping, so tests stay fast and deterministic.
"""

import threading

from tmc_gate.domain.enums import CommandOutcome
from tmc_gate.ports.turnstile import IControllerStatusQuery, ITurnstileControl

# Controller actions (one per port operation)
ACTION_OPEN = "OPEN"
ACTION_CLOSE = "CLOSE"
ACTION_EMERGENCY_RELEASE = "EMERGENCY_RELEASE"
ACTION_RESET = "RESET"

# Raw replies of the simulated vendor API
RAW_RECEIPT_ONLY = "RECEIPT_ONLY"
RAW_TIMEOUT = "TIMEOUT"
RAW_ERROR = "ERROR"  # controller explicitly confirms the command was NOT executed
RAW_NETWORK_ERROR = "NETWORK_ERROR"  # transport failure: the client raises ConnectionError
RAW_UNDETERMINED = "UNDETERMINED"  # status query has no definite answer

# Raw reply meaning "executed" for each action, and the port result it maps to
_CONFIRMED_RAW: dict[str, str] = {
    ACTION_OPEN: "OPEN_CONFIRMED",
    ACTION_CLOSE: "CLOSED",
    ACTION_EMERGENCY_RELEASE: "EMERGENCY_RELEASED",
    ACTION_RESET: "RESET_CONFIRMED",
}
_CONFIRMED_OUTCOME: dict[str, CommandOutcome] = {
    ACTION_OPEN: CommandOutcome.OPEN_CONFIRMED,
    ACTION_CLOSE: CommandOutcome.CLOSED,
    ACTION_EMERGENCY_RELEASE: CommandOutcome.EMERGENCY_RELEASED,
    ACTION_RESET: CommandOutcome.RESET_CONFIRMED,
}

# Values accepted by the simulator per action (also used by the demo API)
COMMAND_MODES: dict[str, tuple[str, ...]] = {
    action: (confirmed, RAW_RECEIPT_ONLY, RAW_TIMEOUT, RAW_ERROR, RAW_NETWORK_ERROR)
    for action, confirmed in _CONFIRMED_RAW.items()
}
QUERY_MODES: tuple[str, ...] = (RAW_UNDETERMINED, "CONFIRMED", RAW_ERROR, RAW_NETWORK_ERROR)

DEFAULT_COMMAND_TIMEOUT_MS = 2_000  # closeGate / emergencyRelease / resetEmergency, configurable per adapter


def map_controller_reply(action: str, raw: str) -> CommandOutcome:
    """Map a raw controller reply to the only results the port may return for this action."""
    if raw == _CONFIRMED_RAW.get(action):
        return _CONFIRMED_OUTCOME[action]
    if raw == RAW_ERROR:
        return CommandOutcome.CONTROLLER_ERROR
    # RECEIPT_ONLY, TIMEOUT, UNDETERMINED, a reply for another action, or anything unknown
    return CommandOutcome.UNDETERMINED


class ControllerApiClient:
    """Low-level client speaking the (simulated) vendor Turnstile Controller API (Figure 4.3, Table 4.8)."""

    def __init__(self) -> None:
        self.simulated_open_mode: str = "OPEN_CONFIRMED"
        self.simulated_close_mode: str = "CLOSED"
        self.simulated_emergency_mode: str = "EMERGENCY_RELEASED"
        self.simulated_reset_mode: str = "RESET_CONFIRMED"
        # Status query reply: UNDETERMINED | CONFIRMED (the original command was executed) | ERROR | NETWORK_ERROR
        self.simulated_query_status: str = RAW_UNDETERMINED
        # Time the controller takes to reply; a reply later than the command timeout counts as no reply
        self.simulated_latency_ms: int = 0
        self.command_log: list[dict[str, str]] = []
        self._lock = threading.Lock()

    def mode_for(self, action: str) -> str:
        return {
            ACTION_OPEN: self.simulated_open_mode,
            ACTION_CLOSE: self.simulated_close_mode,
            ACTION_EMERGENCY_RELEASE: self.simulated_emergency_mode,
            ACTION_RESET: self.simulated_reset_mode,
        }[action]

    def send_command(self, action: str, command_id: str, lane_id: str) -> str:
        """Log the command and return the simulated raw reply. NETWORK_ERROR raises ConnectionError."""
        if action not in _CONFIRMED_RAW:
            raise ValueError(f"Unknown controller action: {action}")
        with self._lock:
            mode = self.mode_for(action)
            self.command_log.append({"kind": "COMMAND", "action": action, "commandId": command_id, "laneId": lane_id})
        if mode == RAW_NETWORK_ERROR:
            raise ConnectionError("simulated transport failure")
        return mode

    def query_command(self, command_id: str, lane_id: str) -> str:
        """Ask the controller for the status of an earlier command and return the raw reply."""
        with self._lock:
            sent = [
                e
                for e in self.command_log
                if e["kind"] == "COMMAND" and e["commandId"] == command_id and e["laneId"] == lane_id
            ]
            self.command_log.append({"kind": "QUERY", "action": "QUERY", "commandId": command_id, "laneId": lane_id})
            status = self.simulated_query_status
        if status == RAW_NETWORK_ERROR:
            raise ConnectionError("simulated transport failure")
        if not sent:
            return RAW_UNDETERMINED  # the controller never saw this (commandId, laneId) pair
        if status == "CONFIRMED":
            return _CONFIRMED_RAW[sent[-1]["action"]]
        return status

    def action_of(self, command_id: str, lane_id: str) -> str | None:
        with self._lock:
            for entry in reversed(self.command_log):
                if entry["kind"] == "COMMAND" and entry["commandId"] == command_id and entry["laneId"] == lane_id:
                    return entry["action"]
        return None

    def sent_commands(self) -> list[dict[str, str]]:
        with self._lock:
            return [dict(e) for e in self.command_log if e["kind"] == "COMMAND"]


class _ContractAdapterBase(ITurnstileControl):
    """Shared, contract-compliant command handling: send once, map the reply, never raise."""

    def __init__(self, api_client: ControllerApiClient, command_timeout_ms: int = DEFAULT_COMMAND_TIMEOUT_MS) -> None:
        if command_timeout_ms <= 0:
            raise ValueError("command_timeout_ms must be positive")
        self._client = api_client
        self._command_timeout_ms = command_timeout_ms

    def _raw_reply(self, action: str, commandId: str, laneId: str) -> str:
        return self._client.send_command(action, commandId, laneId)

    def _execute(self, action: str, commandId: str, laneId: str, timeout_ms: int) -> CommandOutcome:
        try:
            raw = self._raw_reply(action, commandId, laneId)
        except (ConnectionError, TimeoutError, OSError):
            return CommandOutcome.UNDETERMINED  # transport failure: the command may or may not have run
        if self._client.simulated_latency_ms > timeout_ms:
            return CommandOutcome.UNDETERMINED  # no reply within timeout_ms
        return map_controller_reply(action, raw)

    def openGate(self, commandId: str, laneId: str, timeout_ms: int) -> CommandOutcome:
        return self._execute(ACTION_OPEN, commandId, laneId, timeout_ms)

    def closeGate(self, commandId: str, laneId: str) -> CommandOutcome:
        return self._execute(ACTION_CLOSE, commandId, laneId, self._command_timeout_ms)

    def emergencyRelease(self, commandId: str, laneId: str) -> CommandOutcome:
        return self._execute(ACTION_EMERGENCY_RELEASE, commandId, laneId, self._command_timeout_ms)

    def resetEmergency(self, commandId: str, laneId: str) -> CommandOutcome:
        return self._execute(ACTION_RESET, commandId, laneId, self._command_timeout_ms)


class SimulatedTurnstileAdapter(_ContractAdapterBase, IControllerStatusQuery):
    """Compliant implementation of ITurnstileControl and the optional IControllerStatusQuery (§7.1.3 LSP).

    - RECEIPT_ONLY and TIMEOUT map to UNDETERMINED (a receipt is never treated as opened,
      and a timeout is never treated as CONTROLLER_ERROR).
    - Explicit ERROR maps to CONTROLLER_ERROR.
    - Never synthesizes PASSAGE events.
    """

    def queryStatus(self, commandId: str, laneId: str) -> CommandOutcome:
        action = self._client.action_of(commandId, laneId)  # a query must match both commandId and laneId
        try:
            raw = self._client.query_command(commandId, laneId)
        except (ConnectionError, TimeoutError, OSError):
            return CommandOutcome.UNDETERMINED
        if action is None:
            return CommandOutcome.UNDETERMINED
        return map_controller_reply(action, raw)


# Faults the FaultyTurnstileAdapter can inject
FAULT_RECEIPT_ONLY = "RECEIPT_ONLY"  # controller acknowledges receipt but never confirms -> UNDETERMINED
FAULT_CONTROLLER_REJECTS = "CONTROLLER_REJECTS"  # controller confirms non-execution -> CONTROLLER_ERROR
FAULT_TRANSPORT_TIMEOUT = "TRANSPORT_TIMEOUT"  # transport times out after sending -> UNDETERMINED
FAULTS: tuple[str, ...] = (FAULT_RECEIPT_ONLY, FAULT_CONTROLLER_REJECTS, FAULT_TRANSPORT_TIMEOUT)


class FaultyTurnstileAdapter(_ContractAdapterBase):
    """Compliant fault-injecting adapter (§7.1.3 LSP). It passes the same contract tests as SimulatedTurnstileAdapter.

    Configurable faults (per action, or all actions when `fault_actions` is None):
    receipt-only (-> UNDETERMINED), controller confirms non-execution (-> CONTROLLER_ERROR),
    transport timeout (-> UNDETERMINED). Without a fault it behaves like the simulated adapter.
    Does not implement IControllerStatusQuery, so the processor goes straight to PENDING_RECONCILIATION.
    An LSP-violating example lives in tests/ as a separate class marked xfail.
    """

    def __init__(
        self,
        api_client: ControllerApiClient,
        fault: str | None = None,
        fault_actions: set[str] | None = None,
        command_timeout_ms: int = DEFAULT_COMMAND_TIMEOUT_MS,
    ) -> None:
        super().__init__(api_client, command_timeout_ms)
        if fault is not None and fault not in FAULTS:
            raise ValueError(f"Unknown fault: {fault}")
        self.fault = fault
        self.fault_actions = fault_actions

    def _fault_applies(self, action: str) -> bool:
        return self.fault is not None and (self.fault_actions is None or action in self.fault_actions)

    def _raw_reply(self, action: str, commandId: str, laneId: str) -> str:
        if not self._fault_applies(action):
            return super()._raw_reply(action, commandId, laneId)
        # The command still reaches the controller. A real transport failure is not swallowed: it propagates
        # to _execute and becomes UNDETERMINED, so an injected fault can never turn it into CONTROLLER_ERROR.
        super()._raw_reply(action, commandId, laneId)
        if self.fault == FAULT_TRANSPORT_TIMEOUT:
            raise TimeoutError("injected transport timeout")
        if self.fault == FAULT_CONTROLLER_REJECTS:
            return RAW_ERROR
        return RAW_RECEIPT_ONLY


TurnstileAdapter = SimulatedTurnstileAdapter
