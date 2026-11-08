"""[PACKAGE 2] Regressions from the PR #13 review (probes by Khoa Văn, 08/11).

1. A clock error bound of 0 must never be widened to 2000 ms.
2. The factory enforces the signed validity window itself: 0 < exp - iat <= 60 s, iat not in the future.
3. The token expires at exp: valid while now < exp, rejected from now == exp; the clock bound is not added
   to exp (team decision 08/11).
"""

from datetime import UTC, datetime, timedelta

import pytest

from tmc_gate.adapters.demo_qr_issuer import QrCredentialIssuance
from tmc_gate.adapters.sqlite_repos import SqliteSnapshotRepository
from tmc_gate.application.access_decision import AccessDecision
from tmc_gate.domain.enums import Direction
from tmc_gate.domain.snapshot import EligibilitySnapshot, EmployeeRight

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
LANE = "LANE-BD-01"


def qr_setup(bound: float = 0.0) -> tuple[QrCredentialIssuance, AccessDecision]:
    issuer = QrCredentialIssuance()
    snapshot = EligibilitySnapshot(
        factoryId="BD",
        snapshotVersion=1,
        confirmedWatermark=NOW - timedelta(seconds=10),
        clockErrorBoundSec=bound,
        verificationKeys=(issuer.public_key_pem,),
        employees={"EMP-001": EmployeeRight("EMP-001", True, {"BD"}, {LANE})},
        lanes={LANE: Direction.ENTRY},
    )
    return issuer, AccessDecision(SqliteSnapshotRepository(snapshot))


def _token(issuer: QrCredentialIssuance, issued_at: datetime, lifetime: int = 60) -> str:
    token, _ = issuer.issue_qr_token(employee_id="EMP-001", factory_id="BD", issued_at=issued_at, lifetime_sec=lifetime)
    return token


@pytest.mark.parametrize("expired_ms", [1, 1999])
def test_zero_clock_bound_does_not_extend_expiry(expired_ms: int) -> None:
    issuer, decision = qr_setup(0.0)
    token = _token(issuer, NOW - timedelta(seconds=60))
    result = decision.evaluate(token, LANE, now=NOW + timedelta(milliseconds=expired_ms))
    assert not result.allowed, f"Expired by {expired_ms} ms with bound=0: {result}"
    assert result.reason == "QR_TOKEN_EXPIRED"


@pytest.mark.parametrize("bound", [0.0, 0.5, 1.9])
def test_expiry_is_strict_for_every_clock_bound(bound: float) -> None:
    issuer, decision = qr_setup(bound)
    token = _token(issuer, NOW - timedelta(seconds=60))
    assert decision.evaluate(token, LANE, now=NOW - timedelta(milliseconds=1)).allowed is True  # 1 ms before exp
    at_exp = decision.evaluate(token, LANE, now=NOW)  # the token expires exactly at exp
    assert at_exp.allowed is False and at_exp.reason == "QR_TOKEN_EXPIRED"


@pytest.mark.parametrize(
    ("issued_at", "lifetime", "reason"),
    [
        (NOW - timedelta(hours=5), 86400, "QR_VALIDITY_PERIOD_INVALID"),
        (NOW - timedelta(seconds=10), 61, "QR_VALIDITY_PERIOD_INVALID"),
        (NOW + timedelta(minutes=10), 60, "QR_TOKEN_NOT_YET_VALID"),
        (NOW + timedelta(minutes=10), -60, "QR_VALIDITY_PERIOD_INVALID"),
        (NOW - timedelta(seconds=10), 0, "QR_VALIDITY_PERIOD_INVALID"),
    ],
    ids=["24-hour-token-used-after-5-hours", "61s-lifetime", "future-iat", "exp-before-iat", "zero-lifetime"],
)
def test_reject_invalid_signed_validity_period(issued_at: datetime, lifetime: int, reason: str) -> None:
    issuer, decision = qr_setup(0.5)
    result = decision.evaluate(_token(issuer, issued_at, lifetime), LANE, now=NOW)
    assert not result.allowed, f"Invalid validity period was allowed: {result}"
    assert result.reason == reason


def test_exactly_60s_lifetime_is_accepted() -> None:
    issuer, decision = qr_setup(0.5)
    assert decision.evaluate(_token(issuer, NOW - timedelta(seconds=30), 60), LANE, now=NOW).allowed is True


@pytest.mark.parametrize(("ahead_ms", "allowed"), [(500, True), (501, False)])
def test_iat_may_lead_only_by_the_clock_bound(ahead_ms: int, allowed: bool) -> None:
    issuer, decision = qr_setup(0.5)
    issued_at = NOW + timedelta(milliseconds=ahead_ms)
    issuer_seconds = int(issued_at.timestamp())  # claims are whole epoch seconds
    token = _token(issuer, datetime.fromtimestamp(issuer_seconds, tz=UTC))
    now = datetime.fromtimestamp(issuer_seconds, tz=UTC) - timedelta(milliseconds=ahead_ms)
    assert decision.evaluate(token, LANE, now=now).allowed is allowed
