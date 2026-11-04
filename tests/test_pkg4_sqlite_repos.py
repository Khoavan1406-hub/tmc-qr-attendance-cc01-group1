"""[PACKAGE 4] Unit tests for SQLite repositories and atomic constraints (§5.6.1, ADR-02, ADR-11)."""

import pytest


def test_atomic_reserve_lane_and_token_claim_prevents_replay_and_preserves_token_on_busy_lane() -> None:
    """TODO [Package 4]:
    Write test verifying `SqliteScanRepository.atomic_reserve_lane_claim_token_and_accept`:
    - First valid scan on a free lane commits `ACCEPTED`, `TokenReservation`, and `GateCycle`.
    - Second scan on the same busy lane returns `(False, "LANE_BUSY")` and does NOT claim the second tokenId.
    - Replaying an already claimed `tokenId` on another free lane returns `(False, "TOKEN_ALREADY_CLAIMED")`.
    """
    pytest.skip("TODO [Package 4]: Write test for atomic lane reservation and single-use token claim")


def test_store_passage_and_attendance_enforces_at_most_one_transaction_per_scan() -> None:
    """TODO [Package 4]:
    Write test verifying `SqliteAttendanceRepository.store_passage_and_attendance_atomic`:
    - Duplicate `passageEventId` is ignored.
    - At most one original `AttendanceTransaction` is stored per `scanId`, along with outbox and audit records.
    """
    pytest.skip("TODO [Package 4]: Write test for passage and attendance atomic persistence")
