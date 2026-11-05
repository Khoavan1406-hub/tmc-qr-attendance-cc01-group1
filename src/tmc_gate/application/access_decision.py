"""[PACKAGE 2] AccessDecision component of M1 Gate Core (Figure 4.2, Table 4.7, §7.1.1 SRP, §7.3.1).

SRP: AccessDecision evaluates QR Ed25519 signature (using `snapshot.verificationKeys`),
60-second validity window with `snapshot.clockErrorBoundMs < 2_000`, factory scope,
and employee lane eligibility. It never claims tokens, never calls the controller, and never writes to the database.
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
        2. Check `mode = snapshot.getOperatingMode(eval_time)` (which already enforces continuity and `clockErrorBoundMs < 2_000`).
           If `CANNOT_PROCESS`, return `DecisionResult(allowed=False, operatingMode=CANNOT_PROCESS, reason="ELIGIBILITY_DATA_UNUSABLE_OR_EXPIRED", ...)`.
        3. Verify QR token using `_verify_signed_qr_token(...)` against `snapshot.verificationKeys` (or `(snapshot.publicKeyPem,)`).
           If invalid, return `DecisionResult(allowed=False, ...)`.
        4. Check `snapshot.isEmployeeEligible(claims.employeeId, laneId)`. If not eligible, return `DecisionResult(allowed=False, ...)`.
        5. Return `DecisionResult(allowed=True, operatingMode=mode, snapshotVersion=snapshot.snapshotVersion, reason="ALLOWED", claims=claims, direction=snapshot.lanes.get(laneId))`.
        """
        raise NotImplementedError("TODO [Package 2]: Implement AccessDecision.evaluate")

    @staticmethod
    def _verify_signed_qr_token(
        qrToken: str,
        verification_keys: tuple[bytes, ...],
        expected_factory_id: str,
        now: datetime,
        clock_error_bound_ms: int,
    ) -> tuple[TokenClaims | None, str]:
        """Verify Ed25519 signature against `verification_keys`, 60s lifetime (with `clock_error_bound_ms < 2_000`),
        and factory binding (§7.3.1, ADR-11).
        """
        raise NotImplementedError("TODO [Package 2]: Implement AccessDecision._verify_signed_qr_token")
