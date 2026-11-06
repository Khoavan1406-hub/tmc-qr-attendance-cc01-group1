"""[PACKAGE 5] Contract tests illustrating LSP with SimulatedTurnstileAdapter and FaultyTurnstileAdapter (§7.1.3 LSP)."""

import pytest


def test_turnstile_contract_illustrates_lsp_across_two_adapters() -> None:
    """TODO [Package 5]:
    Write a shared contract test (separating mandatory `ITurnstileControl` rules and optional `IControllerStatusQuery`)
    and run each scenario against both `SimulatedTurnstileAdapter` and `FaultyTurnstileAdapter` (compliant, fault-injecting);
    both must pass (LSP). Assert results are within OPEN_RESULTS / CLOSE_RESULTS / RELEASE_RESULTS / RESET_RESULTS:
    - receipt-only past `timeout_ms` returns `UNDETERMINED` (never treated as opened).
    - `TIMEOUT` returns `UNDETERMINED` (never treated as `CONTROLLER_ERROR`).
    - Explicit `ERROR` returns `CONTROLLER_ERROR`.
    """
    pytest.skip("TODO [Package 5]: Implement contract test illustrating LSP across both adapters")


def test_demo_controller_endpoints_and_demo_mode_flag() -> None:
    """TODO [Package 5]:
    Verify `/api/demo/qr-tokens`, `/api/demo/watermark-age`, `/api/demo/controller-mode`,
    `/api/demo/attendance-transactions`, and verify that `demo_controller` is only mounted when `demo_mode=True`.
    """
    pytest.skip("TODO [Package 5]: Implement demo_controller and demo_mode flag tests")
