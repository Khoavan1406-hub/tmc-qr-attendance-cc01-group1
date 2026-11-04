"""[PACKAGE 2] AccessDecision component of M1 Gate Core (Figure 4.2, Table 4.7, §7.1.1 SRP, §7.3.1).

SRP: AccessDecision only evaluates QR token validity, factory scope, employee eligibility,
and operating mode (NORMAL / DEGRADED / CANNOT_PROCESS). It never calls the controller or writes scan states.
"""

from datetime import datetime

from tmc_gate.domain.models import DecisionResult, TokenClaims
from tmc_gate.ports.repositories import ISnapshotRepository


class AccessDecision:
    """Evaluates QR credentials against the factory-local EligibilitySnapshot (§4.1.6, §7.3.1)."""

    def __init__(self, snapshot_repo: ISnapshotRepository) -> None:
        self._snapshot_repo = snapshot_repo

    def evaluate(self, qrToken: str, laneId: str, now: datetime | None = None) -> DecisionResult:
        """Check snapshot usability, operating mode (I-10), Ed25519 signature, 60s expiry,
        factory binding (ADR-11), and employee lane permissions.

        TODO [Package 2]:
        1. Load `snapshot = self._snapshot_repo.get_snapshot()`.
        2. Check `mode = snapshot.getOperatingMode(eval_time)`. If `CANNOT_PROCESS`, return
           `DecisionResult(allowed=False, operatingMode=CANNOT_PROCESS, reason="ELIGIBILITY_DATA_UNUSABLE_OR_EXPIRED", ...)`.
        3. Verify QR token using `_verify_signed_qr_token(...)`. If invalid, return `DecisionResult(allowed=False, ...)`.
        4. Check `snapshot.isEmployeeEligible(claims.employeeId, laneId)`. If not eligible, return `DecisionResult(allowed=False, ...)`.
        5. Return `DecisionResult(allowed=True, operatingMode=mode, snapshotVersion=snapshot.snapshotVersion, reason="ALLOWED", claims=claims, direction=snapshot.lanes.get(laneId))`.
        """
        raise NotImplementedError("TODO [Package 2]: Implement AccessDecision.evaluate")

    @staticmethod
    def _verify_signed_qr_token(
        qrToken: str,
        public_key_pem: bytes,
        expected_factory_id: str,
        now: datetime,
        clock_error_bound_sec: float,
    ) -> tuple[TokenClaims | None, str]:
        """Verify Ed25519 signature, 60s lifetime, and factory binding (§7.3.1, ADR-11).

        TODO [Package 2]:
        - Split `qrToken` by `"."` into `payload_b64` and `sig_b64`; if malformed return `(None, "MALFORMED_QR_TOKEN")`.
        - Verify signature with `Ed25519PublicKey.verify(...)`; on `InvalidSignature` return `(None, "INVALID_QR_SIGNATURE")`.
        - If `aud != expected_factory_id`, return `(None, "WRONG_FACTORY_SCOPE")`.
        - If `(now - expires_at).total_seconds() > clock_error_bound_sec`, return `(None, "QR_TOKEN_EXPIRED")`.
        - Return `(TokenClaims(...), "OK")`.
        """
        raise NotImplementedError("TODO [Package 2]: Implement AccessDecision._verify_signed_qr_token")
