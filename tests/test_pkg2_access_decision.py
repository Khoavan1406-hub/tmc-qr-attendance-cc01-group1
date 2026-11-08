"""[PACKAGE 2] Unit tests for Ed25519 QR credential issuance and AccessDecision evaluation (§7.3.1, ADR-11)."""

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from tmc_gate.adapters.demo_qr_issuer import QrCredentialIssuance
from tmc_gate.application.access_decision import (
    AccessDecision,
    InvalidQrSignature,
    MalformedQrToken,
    QrTokenExpired,
    WrongFactoryScope,
    _b64url_decode,
)
from tmc_gate.domain.enums import Direction, OperatingMode
from tmc_gate.domain.snapshot import EligibilitySnapshot, EmployeeRight
from tmc_gate.ports.repositories import ISnapshotRepository

NOW = datetime(2026, 11, 8, 8, 0, 0, tzinfo=UTC)


class InMemorySnapshotRepo(ISnapshotRepository):
    """In-memory snapshot repository for testing AccessDecision."""

    def __init__(self, snapshot: EligibilitySnapshot) -> None:
        self._snapshot = snapshot

    def get_snapshot(self) -> EligibilitySnapshot:
        return self._snapshot

    def save_snapshot(self, snapshot: EligibilitySnapshot) -> None:
        self._snapshot = snapshot


def _build_test_setup(
    watermark_age_sec: float = 10.0,
    clock_error_bound_sec: float = 0.5,
    factory_id: str = "BD",
    issuer: QrCredentialIssuance | None = None,
    extra_keys: tuple[bytes, ...] = (),
    active: bool = True,
    allowed_lanes: set[str] | None = None,
) -> tuple[QrCredentialIssuance, EligibilitySnapshot, InMemorySnapshotRepo, AccessDecision]:
    qr_issuer = issuer or QrCredentialIssuance()
    verification_keys = (qr_issuer.public_key_pem,) + extra_keys

    snapshot = EligibilitySnapshot(
        factoryId=factory_id,
        snapshotVersion=1,
        confirmedWatermark=NOW - timedelta(seconds=watermark_age_sec),
        clockErrorBoundSec=clock_error_bound_sec,
        employees={
            "EMP-001": EmployeeRight(
                employeeId="EMP-001",
                active=active,
                allowedFactories={factory_id},
                allowedLanes=allowed_lanes if allowed_lanes is not None else {"LANE-BD-01"},
            ),
            "EMP-SUSPENDED": EmployeeRight(
                employeeId="EMP-SUSPENDED",
                active=False,
                allowedFactories={factory_id},
                allowedLanes={"LANE-BD-01"},
            ),
        },
        lanes={
            "LANE-BD-01": Direction.ENTRY,
            "LANE-BD-02": Direction.EXIT,
        },
        verificationKeys=verification_keys,
    )
    repo = InMemorySnapshotRepo(snapshot)
    decision = AccessDecision(repo)
    return qr_issuer, snapshot, repo, decision


# --- Package 2 Requirement Test Cases (1-11) ---


def test_scenario_1_valid_qr_accepted_under_normal_mode() -> None:
    """Scenario 1: Valid QR is accepted under NORMAL mode."""
    qr_issuer, snapshot, _, decision = _build_test_setup(watermark_age_sec=10.0, clock_error_bound_sec=0.5)

    token, claims = qr_issuer.issue_qr_token(employee_id="EMP-001", factory_id="BD", issued_at=NOW)
    result = decision.evaluate(qrToken=token, laneId="LANE-BD-01", now=NOW)

    assert result.allowed is True
    assert result.operatingMode == OperatingMode.NORMAL
    assert result.reason == "ALLOWED"
    assert result.snapshotVersion == snapshot.snapshotVersion
    assert result.claims is not None
    assert result.claims.employeeId == "EMP-001"
    assert result.claims.factoryId == "BD"
    assert result.claims.tokenId == claims["jti"]
    assert result.direction == Direction.ENTRY


def test_scenario_2_valid_qr_accepted_under_degraded_mode() -> None:
    """Scenario 2: Valid QR is accepted under DEGRADED mode (verify operatingMode in result is DEGRADED)."""
    # Watermark age 120s is between 60s and 1800s -> DEGRADED mode
    qr_issuer, snapshot, _, decision = _build_test_setup(watermark_age_sec=120.0, clock_error_bound_sec=0.5)
    assert snapshot.getOperatingMode(NOW) == OperatingMode.DEGRADED

    token, claims = qr_issuer.issue_qr_token(employee_id="EMP-001", factory_id="BD", issued_at=NOW)
    result = decision.evaluate(qrToken=token, laneId="LANE-BD-01", now=NOW)

    assert result.allowed is True
    assert result.operatingMode == OperatingMode.DEGRADED
    assert result.reason == "ALLOWED"
    assert result.claims is not None
    assert result.claims.employeeId == "EMP-001"
    assert result.claims.factoryId == "BD"


def test_scenario_3_expiration_boundaries_before_60s_pass_and_at_60s_fail() -> None:
    """Scenario 3: Expiration boundaries: 1 ms before exp (Pass), exactly at exp = iat + 60 s (Fail)."""
    qr_issuer, _, _, decision = _build_test_setup(watermark_age_sec=10.0, clock_error_bound_sec=0.5)

    token, _ = qr_issuer.issue_qr_token(employee_id="EMP-001", factory_id="BD", issued_at=NOW, lifetime_sec=60)

    # Boundary 1: 1 ms before exp -> Pass
    just_before = NOW + timedelta(seconds=60) - timedelta(milliseconds=1)
    result_before = decision.evaluate(qrToken=token, laneId="LANE-BD-01", now=just_before)
    assert result_before.allowed is True
    assert result_before.reason == "ALLOWED"

    # Boundary 2: exactly at exp (60 s after iat) -> Fail; the token expires at exp
    result_60 = decision.evaluate(qrToken=token, laneId="LANE-BD-01", now=NOW + timedelta(seconds=60))
    assert result_60.allowed is False
    assert result_60.reason == "QR_TOKEN_EXPIRED"

    # Boundary 3: At > 60s past expiry and clock skew tolerance (e.g. 65s) -> Fail
    now_expired = NOW + timedelta(seconds=65)
    result_expired = decision.evaluate(qrToken=token, laneId="LANE-BD-01", now=now_expired)
    assert result_expired.allowed is False
    assert result_expired.reason == "QR_TOKEN_EXPIRED"


def test_scenario_4_wrong_factory_aud_mismatch() -> None:
    """Scenario 4: Wrong factory (aud mismatch)."""
    qr_issuer, _, _, decision = _build_test_setup(factory_id="BD")

    # Issue token for factory "LA" while snapshot is configured for factory "BD"
    token, _ = qr_issuer.issue_qr_token(employee_id="EMP-001", factory_id="LA", issued_at=NOW)
    result = decision.evaluate(qrToken=token, laneId="LANE-BD-01", now=NOW)

    assert result.allowed is False
    assert result.reason == "WRONG_FACTORY_SCOPE"


def test_scenario_5_fake_signature_and_tampered_payload() -> None:
    """Scenario 5: Fake signature (signed with a different Ed25519 key) and tampered payload."""
    qr_issuer, _, _, decision = _build_test_setup(factory_id="BD")

    # 1. Fake signature: signed with an unrecognized Ed25519 private key
    fake_issuer = QrCredentialIssuance()
    fake_token, _ = fake_issuer.issue_qr_token(employee_id="EMP-001", factory_id="BD", issued_at=NOW)
    res_fake = decision.evaluate(qrToken=fake_token, laneId="LANE-BD-01", now=NOW)
    assert res_fake.allowed is False
    assert res_fake.reason == "INVALID_QR_SIGNATURE"

    # 2. Tampered payload: valid signature, but payload modified after signing
    valid_token, _ = qr_issuer.issue_qr_token(employee_id="EMP-001", factory_id="BD", issued_at=NOW)
    payload_b64, sig_b64 = valid_token.split(".")
    payload_data = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    payload_data["sub"] = "EMP-002"  # Tamper subject to another employee
    tampered_bytes = json.dumps(payload_data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    tampered_b64 = base64.urlsafe_b64encode(tampered_bytes).decode("ascii").rstrip("=")
    tampered_token = f"{tampered_b64}.{sig_b64}"

    res_tampered = decision.evaluate(qrToken=tampered_token, laneId="LANE-BD-01", now=NOW)
    assert res_tampered.allowed is False
    assert res_tampered.reason == "INVALID_QR_SIGNATURE"


def test_scenario_6_malformed_token_format() -> None:
    """Scenario 6: Malformed token format (e.g., missing dot, not base64url, invalid json)."""
    _, _, _, decision = _build_test_setup(factory_id="BD")

    # Missing dot
    res_no_dot = decision.evaluate(qrToken="MALFORMED_NO_DOT_TOKEN", laneId="LANE-BD-01", now=NOW)
    assert res_no_dot.allowed is False
    assert res_no_dot.reason == "MALFORMED_QR_TOKEN"

    # Multiple dots
    res_multi_dot = decision.evaluate(qrToken="part1.part2.part3", laneId="LANE-BD-01", now=NOW)
    assert res_multi_dot.allowed is False
    assert res_multi_dot.reason == "MALFORMED_QR_TOKEN"

    # Not base64url
    res_bad_b64 = decision.evaluate(qrToken="???bad_base64???.!!!sig!!!", laneId="LANE-BD-01", now=NOW)
    assert res_bad_b64.allowed is False
    assert res_bad_b64.reason == "MALFORMED_QR_TOKEN"

    # Not valid JSON
    bad_json_b64 = base64.urlsafe_b64encode(b"not json data").decode("ascii").rstrip("=")
    res_bad_json = decision.evaluate(qrToken=f"{bad_json_b64}.c2lnbmF0dXJl", laneId="LANE-BD-01", now=NOW)
    assert res_bad_json.allowed is False
    assert res_bad_json.reason == "MALFORMED_QR_TOKEN"


def test_scenario_7_employee_blocked_or_unauthorized_on_lane() -> None:
    """Scenario 7: Employee is blocked, or Employee unauthorized on the specific laneId."""
    qr_issuer, _, _, decision = _build_test_setup(factory_id="BD")

    # 1. Suspended / blocked employee
    token_suspended, _ = qr_issuer.issue_qr_token(employee_id="EMP-SUSPENDED", factory_id="BD", issued_at=NOW)
    res_suspended = decision.evaluate(qrToken=token_suspended, laneId="LANE-BD-01", now=NOW)
    assert res_suspended.allowed is False
    assert res_suspended.reason == "EMPLOYEE_SUSPENDED_OR_INACTIVE"

    # 2. Unauthorized lane (EMP-001 is only permitted on LANE-BD-01, not LANE-BD-02)
    token_valid, _ = qr_issuer.issue_qr_token(employee_id="EMP-001", factory_id="BD", issued_at=NOW)
    res_wrong_lane = decision.evaluate(qrToken=token_valid, laneId="LANE-BD-02", now=NOW)
    assert res_wrong_lane.allowed is False
    assert res_wrong_lane.reason == "LANE_NOT_PERMITTED"


def test_scenario_8_clock_skew_ge_2000ms_returns_cannot_process() -> None:
    """Scenario 8: Clock skew >= 2000 ms -> returns CANNOT_PROCESS."""
    # Clock error bound >= 2.0 s fails closed immediately without verifying token
    qr_issuer, snapshot, _, decision = _build_test_setup(clock_error_bound_sec=2.0)
    assert snapshot.getOperatingMode(NOW) == OperatingMode.CANNOT_PROCESS

    token, _ = qr_issuer.issue_qr_token(employee_id="EMP-001", factory_id="BD", issued_at=NOW)
    result = decision.evaluate(qrToken=token, laneId="LANE-BD-01", now=NOW)

    assert result.allowed is False
    assert result.operatingMode == OperatingMode.CANNOT_PROCESS
    assert result.reason == "ELIGIBILITY_DATA_UNUSABLE_OR_EXPIRED"

    # Even a malformed or garbage token immediately returns CANNOT_PROCESS without verifying
    res_garbage = decision.evaluate(qrToken="GARBAGE_TOKEN", laneId="LANE-BD-01", now=NOW)
    assert res_garbage.allowed is False
    assert res_garbage.operatingMode == OperatingMode.CANNOT_PROCESS
    assert res_garbage.reason == "ELIGIBILITY_DATA_UNUSABLE_OR_EXPIRED"


def test_scenario_9_snapshot_watermark_age_ge_1800s_returns_cannot_process() -> None:
    """Scenario 9: Snapshot watermark age >= 1800s -> returns CANNOT_PROCESS."""
    # Watermark age >= 1800s (30 minutes) fails closed immediately
    qr_issuer, snapshot, _, decision = _build_test_setup(watermark_age_sec=1800.0)
    assert snapshot.getOperatingMode(NOW) == OperatingMode.CANNOT_PROCESS

    token, _ = qr_issuer.issue_qr_token(employee_id="EMP-001", factory_id="BD", issued_at=NOW)
    result = decision.evaluate(qrToken=token, laneId="LANE-BD-01", now=NOW)

    assert result.allowed is False
    assert result.operatingMode == OperatingMode.CANNOT_PROCESS
    assert result.reason == "ELIGIBILITY_DATA_UNUSABLE_OR_EXPIRED"


def test_scenario_10_key_rotation_validation_succeeds_using_second_public_key() -> None:
    """Scenario 10: Key rotation: Validation succeeds using the second public key in verificationKeys."""
    issuer_old = QrCredentialIssuance()
    issuer_new = QrCredentialIssuance()

    # Snapshot retains both keys during rotation: (old_key, new_key)
    _, snapshot, _, decision = _build_test_setup(
        issuer=issuer_old,
        extra_keys=(issuer_new.public_key_pem,),
    )
    assert len(snapshot.verificationKeys) == 2

    # Token issued using the second / new key succeeds
    token_new, _ = issuer_new.issue_qr_token(employee_id="EMP-001", factory_id="BD", issued_at=NOW)
    result_new = decision.evaluate(qrToken=token_new, laneId="LANE-BD-01", now=NOW)
    assert result_new.allowed is True
    assert result_new.reason == "ALLOWED"

    # Token issued using the first / old key still succeeds as well
    token_old, _ = issuer_old.issue_qr_token(employee_id="EMP-001", factory_id="BD", issued_at=NOW)
    result_old = decision.evaluate(qrToken=token_old, laneId="LANE-BD-01", now=NOW)
    assert result_old.allowed is True
    assert result_old.reason == "ALLOWED"


def test_scenario_11_srp_verification_evaluate_twice_returns_allowed_both_times() -> None:
    """Scenario 11: SRP Verification: Calling evaluate() twice sequentially with the exact same
    valid token returns allowed=True both times (proving AccessDecision does not claim tokens).
    """
    qr_issuer, _, _, decision = _build_test_setup(watermark_age_sec=10.0, clock_error_bound_sec=0.5)
    token, _ = qr_issuer.issue_qr_token(employee_id="EMP-001", factory_id="BD", issued_at=NOW)

    # First evaluation
    result_1 = decision.evaluate(qrToken=token, laneId="LANE-BD-01", now=NOW)
    assert result_1.allowed is True
    assert result_1.reason == "ALLOWED"

    # Second evaluation with the exact same token
    result_2 = decision.evaluate(qrToken=token, laneId="LANE-BD-01", now=NOW)
    assert result_2.allowed is True
    assert result_2.reason == "ALLOWED"

    # Assert outputs are identical
    assert result_1.claims == result_2.claims
    assert result_1.operatingMode == result_2.operatingMode
    assert result_1.direction == result_2.direction


# --- Backward-compatible test names (originally marked with pytest.skip) ---


def test_qr_issuance_and_signature_verification() -> None:
    """Verifies QrCredentialIssuance.issue_qr_token and AccessDecision.evaluate:
    - Valid Ed25519 token within 60s for matching factory returns allowed=True.
    - Tampered signature or malformed token returns allowed=False (INVALID_QR_SIGNATURE / MALFORMED_QR_TOKEN).
    """
    qr_issuer, _, _, decision = _build_test_setup(factory_id="BD")

    token, _ = qr_issuer.issue_qr_token(employee_id="EMP-001", factory_id="BD", issued_at=NOW)
    res_valid = decision.evaluate(qrToken=token, laneId="LANE-BD-01", now=NOW)
    assert res_valid.allowed is True
    assert res_valid.reason == "ALLOWED"

    res_malformed = decision.evaluate(qrToken="malformed-token", laneId="LANE-BD-01", now=NOW)
    assert res_malformed.allowed is False
    assert res_malformed.reason == "MALFORMED_QR_TOKEN"

    fake_issuer = QrCredentialIssuance()
    fake_token, _ = fake_issuer.issue_qr_token(employee_id="EMP-001", factory_id="BD", issued_at=NOW)
    res_fake = decision.evaluate(qrToken=fake_token, laneId="LANE-BD-01", now=NOW)
    assert res_fake.allowed is False
    assert res_fake.reason == "INVALID_QR_SIGNATURE"


def test_qr_expiry_and_factory_scope_rejection() -> None:
    """Verifies 60s expiry boundary and factory binding:
    - Token older than 60s (+ clockErrorBoundSec) is rejected with QR_TOKEN_EXPIRED.
    - Token issued for factory LA presented at factory BD is rejected with WRONG_FACTORY_SCOPE.
    """
    qr_issuer, _, _, decision = _build_test_setup(factory_id="BD")

    # Expiry rejection
    token, _ = qr_issuer.issue_qr_token(employee_id="EMP-001", factory_id="BD", issued_at=NOW, lifetime_sec=60)
    res_expired = decision.evaluate(qrToken=token, laneId="LANE-BD-01", now=NOW + timedelta(seconds=65))
    assert res_expired.allowed is False
    assert res_expired.reason == "QR_TOKEN_EXPIRED"

    # Wrong factory scope
    token_la, _ = qr_issuer.issue_qr_token(employee_id="EMP-001", factory_id="LA", issued_at=NOW)
    res_wrong_factory = decision.evaluate(qrToken=token_la, laneId="LANE-BD-01", now=NOW)
    assert res_wrong_factory.allowed is False
    assert res_wrong_factory.reason == "WRONG_FACTORY_SCOPE"
