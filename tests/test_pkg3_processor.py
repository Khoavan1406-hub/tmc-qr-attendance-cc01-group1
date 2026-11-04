"""[PACKAGE 3] Unit & integration tests for GateTransactionProcessor, GateCoreFacade, and GateCoreController (§4.2.2 Flow A, ADR-02)."""

import pytest


def test_flow_a_scan_open_passage_creates_attendance_and_lane_clear_closes() -> None:
    """TODO [Package 3]:
    Write test verifying Flow A1–A9:
    - Valid scan stores `ACCEPTED` before `openGate`, then returns `OPENED` (no `AttendanceTransaction` created yet).
    - `onPassage` creates exactly 1 `AttendanceTransaction` for the `scanId`.
    - `onLaneClear` sends `closeGate` once and releases the lane on `CLOSED`.
    """
    pytest.skip("TODO [Package 3]: Write test for Flow A happy path and passage-based attendance (I-07)")


def test_duplicate_scan_id_and_undetermined_controller_outcome() -> None:
    """TODO [Package 3]:
    Write test verifying:
    - Resubmitting the same `scanId` returns the stored result without calling `openGate` a second time.
    - When `openGate` returns `UNDETERMINED`, scan state is `UNDETERMINED`, lane stays reserved, and `openGate` is NOT resent (I-03).
    - Missing passage after 15s (`handlePassageTimeout`) marks `NO_PASSAGE` and `PENDING_RECONCILIATION` without closing the gate.
    """
    pytest.skip("TODO [Package 3]: Write test for duplicate scanId, UNDETERMINED outcome, and 15s passage timeout")
