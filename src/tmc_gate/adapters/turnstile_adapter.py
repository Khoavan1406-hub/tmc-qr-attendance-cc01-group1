"""[PACKAGE 5] M2 Turnstile Integration module (Figure 4.3, Table 4.8, §4.3, §7.1.3 LSP, ADR-04).

Contains TurnstileAdapter and ControllerApiClient (with simulator hooks for testing/demo).
Preserves the behavioral contract of ITurnstileControl:
- A command receipt is NOT OPEN_CONFIRMED.
- A timeout after the command may have been sent stays UNDETERMINED.
- Only explicit non-execution returns CONTROLLER_ERROR.
"""

from tmc_gate.domain.enums import CommandOutcome
from tmc_gate.ports.turnstile import ITurnstileControl


class ControllerApiClient:
    """Low-level client speaking the vendor Turnstile Controller API (Figure 4.3, Table 4.8).

    Supports configurable simulated responses per lane for demo and automated testing.
    """

    def __init__(self) -> None:
        self.simulated_open_mode: str = "OPEN_CONFIRMED"  # OPEN_CONFIRMED | RECEIPT_ONLY | TIMEOUT | ERROR
        self.simulated_close_mode: str = "CLOSED"  # CLOSED | TIMEOUT | ERROR
        self.command_log: list[dict[str, str]] = []

    def send_command(self, action: str, command_id: str, lane_id: str) -> str:
        """Send a raw vendor command and return the raw vendor status string.

        TODO [Package 5]:
        1. Append `{"action": action, "commandId": command_id, "laneId": lane_id}` to `self.command_log`.
        2. Return `self.simulated_open_mode` for `"OPEN"`, `self.simulated_close_mode` for `"CLOSE"`,
           `"EMERGENCY_RELEASED"` for `"EMERGENCY_RELEASE"`, and `"RESET_CONFIRMED"` for `"RESET"`.
        """
        raise NotImplementedError("TODO [Package 5]: Implement ControllerApiClient.send_command")


class TurnstileAdapter(ITurnstileControl):
    """Implements ITurnstileControl port defined by M1 Gate Core (Figure 4.3, §4.3, §7.1.3 LSP)."""

    def __init__(self, api_client: ControllerApiClient) -> None:
        self._client = api_client

    def openGate(self, commandId: str, laneId: str) -> CommandOutcome:
        """TODO [Package 5]: Call `self._client.send_command("OPEN", commandId, laneId)` and map response per LSP (§7.1.3):
        - `"OPEN_CONFIRMED"` -> `CommandOutcome.OPEN_CONFIRMED`
        - `"ERROR"` -> `CommandOutcome.CONTROLLER_ERROR`
        - `"RECEIPT_ONLY"` or `"TIMEOUT"` -> `CommandOutcome.UNDETERMINED` (never treat receipt alone as opened!).
        """
        raise NotImplementedError("TODO [Package 5]: Implement TurnstileAdapter.openGate")

    def closeGate(self, commandId: str, laneId: str) -> CommandOutcome:
        """TODO [Package 5]: Call `self._client.send_command("CLOSE", commandId, laneId)` and map response."""
        raise NotImplementedError("TODO [Package 5]: Implement TurnstileAdapter.closeGate")

    def emergencyRelease(self, commandId: str, laneId: str) -> CommandOutcome:
        """TODO [Package 5]: Call `self._client.send_command("EMERGENCY_RELEASE", commandId, laneId)` and map response."""
        raise NotImplementedError("TODO [Package 5]: Implement TurnstileAdapter.emergencyRelease")

    def resetEmergency(self, commandId: str, laneId: str) -> CommandOutcome:
        """TODO [Package 5]: Call `self._client.send_command("RESET", commandId, laneId)` and map response."""
        raise NotImplementedError("TODO [Package 5]: Implement TurnstileAdapter.resetEmergency")
