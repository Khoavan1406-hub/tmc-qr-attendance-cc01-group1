"""[PACKAGE 5] Unit tests for TurnstileAdapter LSP behavioral contract and Demo endpoints (§7.1.3 LSP)."""

import pytest


def test_liskov_substitution_principle_on_turnstile_adapter() -> None:
    """TODO [Package 5]:
    Write unit test verifying `TurnstileAdapter` behavioral contract (§7.1.3 LSP):
    - `"OPEN_CONFIRMED"` -> `CommandOutcome.OPEN_CONFIRMED`
    - `"RECEIPT_ONLY"` or `"TIMEOUT"` -> `CommandOutcome.UNDETERMINED` (never treated as opened!)
    - `"ERROR"` -> `CommandOutcome.CONTROLLER_ERROR`
    """
    pytest.skip("TODO [Package 5]: Write unit test for TurnstileAdapter LSP contract")


def test_demo_controller_endpoints() -> None:
    """TODO [Package 5]:
    Write test verifying `/api/demo/qr-tokens`, `/api/demo/watermark-age`, `/api/demo/controller-mode`,
    and `/api/demo/attendance-transactions`.
    """
    pytest.skip("TODO [Package 5]: Write test for demo controller endpoints")
