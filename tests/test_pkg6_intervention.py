"""[PACKAGE 6] Unit & integration tests for M3 Gate Intervention (Flow B: Assisted Access & Emergency Release, ADR-07).

Scope note: M3 Gate Intervention and M1 Gate Core run real logic; HR approval and Resolution Feed are simulated.
"""

import pytest


def test_assisted_access_requires_guard_authorization_and_creates_no_automatic_attendance() -> None:
    """TODO [Package 6]:
    Verify:
    - Unauthorized guard is rejected server-side (`GUARD_UNAUTHORIZED`).
    - Assisted open or emergency release never creates automatic attendance from opening alone (ADR-07, I-04).
    """
    pytest.skip("TODO [Package 6]: Write test for Assisted Access authorization and ADR-07")


def test_emergency_release_hold_blocks_scans_and_unconfirmed_reset_stays_in_hold() -> None:
    """TODO [Package 6]:
    Verify:
    - `emergencyRelease` places lane in `EMERGENCY_HOLD` (blocking new QR scans).
    - If `resetEmergency` is not confirmed (`UNDETERMINED` / `CONTROLLER_ERROR`), lane does NOT exit `EMERGENCY_HOLD`.
    - Once `RESET_CONFIRMED` is returned, normal closing rules resume.
    """
    pytest.skip("TODO [Package 6]: Write test for EMERGENCY_HOLD and unconfirmed reset behavior")
