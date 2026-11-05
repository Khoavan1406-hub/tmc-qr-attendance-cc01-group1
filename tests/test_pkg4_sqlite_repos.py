"""[PACKAGE 4] Unit & concurrency tests for SqliteUnitOfWork and SQLite repositories (§5.6.1, ADR-02, ADR-05, ADR-11)."""

import pytest


def test_concurrent_claim_same_token() -> None:
    """TODO [Package 4]:
    Two separate SQLite connections to the same DB file on disk, two free lanes, synchronized via `threading.Barrier(2)`:
    expect 1 `ACCEPTED` and 1 rejected (`TOKEN_ALREADY_CLAIMED`), with zero `'database is locked'` errors.
    """
    pytest.skip("TODO [Package 4]: Implement test_concurrent_claim_same_token with 2 connections and Barrier")


def test_unit_of_work_rollback_and_event_id_dedup_and_conflict() -> None:
    """TODO [Package 4]:
    1. Test rollback: raising an error mid-transaction leaves zero partial records in SQLite.
    2. Test `store_passage_and_attendance_atomic`: 1 GateCycle can hold multiple PassageEvents, each passageEventId
       accepted once, and at most 1 original AttendanceTransaction per scanId.
    3. Test simulated consumer deduplication keyed by `(consumer, eventId)`:
       - Same `eventId` + same payload -> `DUPLICATE_IGNORED`
       - Same `eventId` + different payload -> `CONFLICT_FLAGGED`
    """
    pytest.skip("TODO [Package 4]: Implement rollback, passage/attendance atomic write, and (consumer, eventId) tests")
