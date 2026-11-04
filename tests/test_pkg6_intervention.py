"""[PACKAGE 6] Unit & integration tests for M3 Gate Intervention (Flow B: Assisted Access & Emergency Release, ADR-07)."""

import pytest


def test_assisted_access_requires_guard_authorization_and_creates_no_automatic_attendance() -> None:
    """TODO [Package 6]:
    Write test verifying Flow B Assisted Access:
    - Unauthorized guard is rejected (`GUARD_UNAUTHORIZED`).
    - Authorized guard records `SupportCase` and opens gate (`OPENED`), and subsequent `PASSAGE` does NOT create automatic attendance (ADR-07).
    """
    pytest.skip("TODO [Package 6]: Write test for Assisted Access flow and ADR-07")


def test_emergency_release_places_lane_in_hold_until_authorized_reset() -> None:
    """TODO [Package 6]:
    Write test verifying Flow B Emergency Opening:
    - `emergencyRelease` places lane in `EMERGENCY_HOLD` (blocking normal closing and new scans).
    - `resetEmergency` restores normal closing so `LANE_CLEAR` can close and free the lane.
    """
    pytest.skip("TODO [Package 6]: Write test for Emergency Release and Reset lifecycle")
