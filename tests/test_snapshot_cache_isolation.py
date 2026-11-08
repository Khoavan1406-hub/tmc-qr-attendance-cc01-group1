"""[PACKAGE 4] PR #15 review round 3 (Khoa): a snapshot returned to a caller never aliases the committed cache."""

from dataclasses import replace
import json
import sqlite3

import pytest

from tmc_gate.api.app import create_app
from tmc_gate.application.access_decision import AccessDecision
from tmc_gate.domain.enums import Direction


LANE = "LANE-BD-01"
EMPLOYEE = "EMP-001"


@pytest.fixture
def gate(tmp_path):
    path = tmp_path / "isolation.sqlite3"
    app = create_app(db_path=str(path), passage_timeout_interval_sec=None)
    yield app, path
    assert not app.state.uow.connection.in_transaction
    app.state.uow.connection.close()


def stored(path):
    with sqlite3.connect(path) as conn:
        raw = conn.execute("SELECT snapshot_json FROM eligibility_snapshots WHERE factory_id='BD'").fetchone()[0]
    return json.loads(raw)


def signed_token(app):
    return app.state.qr_issuer.issue_qr_token(employee_id=EMPLOYEE, factory_id="BD")[0]


@pytest.mark.parametrize("rollback", [False, True], ids=["never_saved", "rolled_back"])
def test_grant_on_snapshot_copy_is_not_visible_before_commit(gate, rollback):
    app, path = gate
    repo, uow = app.state.snapshot_repo, app.state.uow
    original = repo.get_snapshot()
    rights = dict(original.employees)
    rights[EMPLOYEE] = replace(rights[EMPLOYEE], allowedLanes={"LANE-BD-02"})
    with uow:
        repo.save_snapshot(replace(original, snapshotVersion=2, employees=rights))
        uow.commit()
    qr = signed_token(app)
    decision = AccessDecision(repo)
    assert decision.evaluate(qr, LANE).allowed is False

    working = repo.get_snapshot()
    working.employees[EMPLOYEE].allowedLanes.add(LANE)
    if rollback:
        with uow:
            repo.save_snapshot(replace(working, snapshotVersion=3))
            uow.rollback()

    # The authoritative committed row still denies this lane.
    assert stored(path)["employees"][EMPLOYEE]["allowedLanes"] == ["LANE-BD-02"]
    result = decision.evaluate(qr, LANE)
    assert result.allowed is False, (
        f"rollback={rollback}; persisted rights deny {LANE}, but AccessDecision={result.reason}; "
        f"cached rights={repo.get_snapshot().employees[EMPLOYEE].allowedLanes}"
    )


def test_lane_map_in_a_returned_snapshot_does_not_alias_committed_cache(gate):
    app, path = gate
    repo = app.state.snapshot_repo
    working = repo.get_snapshot()
    working.lanes[LANE] = Direction.EXIT
    assert stored(path)["lanes"][LANE] == "ENTRY"
    assert repo.get_snapshot().lanes[LANE] == Direction.ENTRY, "unsaved copy changed the committed lane map"


def test_committed_grant_is_published(gate):
    app, path = gate
    repo, uow = app.state.snapshot_repo, app.state.uow
    before = repo.get_snapshot()
    employees = dict(before.employees)
    employees[EMPLOYEE] = replace(employees[EMPLOYEE], allowedLanes={"LANE-BD-02"})
    with uow:
        repo.save_snapshot(replace(before, snapshotVersion=2, employees=employees))
        uow.commit()
    qr = signed_token(app)
    decision = AccessDecision(repo)
    assert decision.evaluate(qr, LANE).allowed is False
    current = repo.get_snapshot()
    employees = dict(current.employees)
    employees[EMPLOYEE] = replace(employees[EMPLOYEE], allowedLanes={LANE, "LANE-BD-02"})
    with uow:
        repo.save_snapshot(replace(current, snapshotVersion=3, employees=employees))
        uow.commit()
    assert decision.evaluate(qr, LANE).allowed is True
    assert LANE in stored(path)["employees"][EMPLOYEE]["allowedLanes"]
