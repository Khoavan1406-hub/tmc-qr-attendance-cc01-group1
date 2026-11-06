"""[PACKAGE 5] M2 Turnstile Integration module (Figure 4.3, Table 4.8, §4.3, §7.1.3 LSP, ADR-04).

Contains:
- ControllerApiClient: low-level simulator client with configurable scenarios (OPEN_CONFIRMED, RECEIPT_ONLY, TIMEOUT, ERROR).
- SimulatedTurnstileAdapter: compliant adapter implementing ITurnstileControl (and optional IControllerStatusQuery).
- FaultyTurnstileAdapter: compliant fault-injecting adapter; passes the same contract tests (§7.1.3 LSP).
"""

from tmc_gate.domain.enums import CommandOutcome
from tmc_gate.ports.turnstile import IControllerStatusQuery, ITurnstileControl


class ControllerApiClient:
    """Low-level client speaking the vendor Turnstile Controller API (Figure 4.3, Table 4.8)."""

    def __init__(self) -> None:
        self.simulated_open_mode: str = "OPEN_CONFIRMED"  # OPEN_CONFIRMED | RECEIPT_ONLY | TIMEOUT | ERROR
        self.simulated_close_mode: str = "CLOSED"  # CLOSED | RECEIPT_ONLY | TIMEOUT | ERROR
        self.simulated_reset_mode: str = "RESET_CONFIRMED"  # RESET_CONFIRMED | RECEIPT_ONLY | TIMEOUT | ERROR
        self.simulated_query_status: str = "UNDETERMINED"
        self.command_log: list[dict[str, str]] = []

    def send_command(self, action: str, command_id: str, lane_id: str) -> str:
        """TODO [Package 5]: Log command and return simulated raw response string."""
        raise NotImplementedError("TODO [Package 5]: Implement ControllerApiClient.send_command")


class SimulatedTurnstileAdapter(ITurnstileControl, IControllerStatusQuery):
    """Compliant implementation of ITurnstileControl (and optional IControllerStatusQuery) (§7.1.3 LSP).

    TODO [Package 5]:
    - `RECEIPT_ONLY` and `TIMEOUT` must map to `CommandOutcome.UNDETERMINED` (never treat receipt alone as opened,
      and never treat timeout as `CONTROLLER_ERROR`).
    - Explicit `"ERROR"` maps to `CommandOutcome.CONTROLLER_ERROR`.
    - Never synthesize `PASSAGE` events inside the adapter.
    """

    def __init__(self, api_client: ControllerApiClient) -> None:
        self._client = api_client

    def openGate(self, commandId: str, laneId: str, timeout_ms: int) -> CommandOutcome:
        raise NotImplementedError("TODO [Package 5]: Implement SimulatedTurnstileAdapter.openGate")

    def closeGate(self, commandId: str, laneId: str) -> CommandOutcome:
        raise NotImplementedError("TODO [Package 5]: Implement SimulatedTurnstileAdapter.closeGate")

    def emergencyRelease(self, commandId: str, laneId: str) -> CommandOutcome:
        raise NotImplementedError("TODO [Package 5]: Implement SimulatedTurnstileAdapter.emergencyRelease")

    def resetEmergency(self, commandId: str, laneId: str) -> CommandOutcome:
        raise NotImplementedError("TODO [Package 5]: Implement SimulatedTurnstileAdapter.resetEmergency")

    def queryStatus(self, commandId: str, laneId: str) -> CommandOutcome:
        raise NotImplementedError("TODO [Package 5]: Implement SimulatedTurnstileAdapter.queryStatus")


class FaultyTurnstileAdapter(ITurnstileControl):
    """Compliant fault-injecting adapter (§7.1.3 LSP). It must pass the same contract tests as SimulatedTurnstileAdapter.

    TODO [Package 5]:
    Configurable scenarios: receipt-only (-> UNDETERMINED after timeout_ms), controller confirms non-execution
    (-> CONTROLLER_ERROR), transport timeout (-> UNDETERMINED). Does not implement IControllerStatusQuery.
    An LSP-violating example, if wanted, belongs in tests/ as a separate class marked xfail.
    """

    def __init__(self, api_client: ControllerApiClient) -> None:
        self._client = api_client

    def openGate(self, commandId: str, laneId: str, timeout_ms: int) -> CommandOutcome:
        raise NotImplementedError("TODO [Package 5]: Implement FaultyTurnstileAdapter.openGate")

    def closeGate(self, commandId: str, laneId: str) -> CommandOutcome:
        raise NotImplementedError("TODO [Package 5]: Implement FaultyTurnstileAdapter.closeGate")

    def emergencyRelease(self, commandId: str, laneId: str) -> CommandOutcome:
        raise NotImplementedError("TODO [Package 5]: Implement FaultyTurnstileAdapter.emergencyRelease")

    def resetEmergency(self, commandId: str, laneId: str) -> CommandOutcome:
        raise NotImplementedError("TODO [Package 5]: Implement FaultyTurnstileAdapter.resetEmergency")


TurnstileAdapter = SimulatedTurnstileAdapter
