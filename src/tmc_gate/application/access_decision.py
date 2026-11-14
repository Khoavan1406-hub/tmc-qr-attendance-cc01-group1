"""[PACKAGE 2] AccessDecision component of M1 Gate Core (Figure 4.2, Table 4.7, §7.1.1 SRP, §7.3.1).

SRP: AccessDecision evaluates QR Ed25519 signature (using `snapshot.verificationKeys`),
60-second validity window with `snapshot.clockErrorBoundMs < 2_000`, factory scope,
and employee lane eligibility. It never claims tokens, never calls the controller, and never writes to the database.
"""

import base64
import json
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from tmc_gate.domain.enums import Direction, OperatingMode
from tmc_gate.domain.models import DecisionResult, TokenClaims
from tmc_gate.domain.snapshot import EligibilitySnapshot
from tmc_gate.ports.repositories import ISnapshotRepository


class QrVerificationError(Exception):
    """Base exception for QR token verification failures."""


class MalformedQrToken(QrVerificationError):
    """Raised when QR token structure, encoding, or JSON payload is invalid."""


class InvalidQrSignature(QrVerificationError):
    """Raised when Ed25519 signature fails verification."""


class QrTokenExpired(QrVerificationError):
    """Raised when QR token expiration time is exceeded."""


class WrongFactoryScope(QrVerificationError):
    """Raised when token audience does not match factory scope."""


class InvalidValidityPeriod(QrVerificationError):
    """Raised when the signed validity window is not 0 < exp - iat <= 60 s."""


class QrTokenNotYetValid(QrVerificationError):
    """Raised when iat lies in the future beyond the known clock error bound."""


MAX_TOKEN_LIFETIME_MS = 60_000  # ADR-11: QR credentials are valid for at most 60 seconds


def _bound_or_zero(bound_ms: int | None) -> int:
    """Clock error bound in ms. 0 stays 0 (never widened); unknown is treated as 0 here because
    evaluate() already returns CANNOT_PROCESS for an unknown or >= 2000 ms bound."""
    if bound_ms is None or bound_ms < 0:
        return 0
    return min(int(bound_ms), 1_999)


# Aliases for common exception names
InvalidSignature = InvalidQrSignature
TokenExpired = QrTokenExpired
InvalidAudience = WrongFactoryScope


def _b64url_decode(s: str) -> bytes:
    """Decode a base64url-encoded string, tolerating omitted padding."""
    clean = s.strip()
    rem = len(clean) % 4
    if rem > 0:
        clean += "=" * (4 - rem)
    return base64.urlsafe_b64decode(clean.encode("ascii"))


def _load_public_key(key_data: bytes | Ed25519PublicKey) -> Ed25519PublicKey:
    if isinstance(key_data, Ed25519PublicKey):
        return key_data
    if isinstance(key_data, (bytes, str)):
        raw = key_data.encode("utf-8") if isinstance(key_data, str) else key_data
        if b"BEGIN PUBLIC KEY" in raw:
            return load_pem_public_key(raw)
        if len(raw) == 32:
            return Ed25519PublicKey.from_public_bytes(raw)
        return load_pem_public_key(raw)
    raise ValueError(f"Unsupported key format: {type(key_data)}")


def _parse_time_claim(value: object) -> tuple[datetime, int]:
    """Return (aware datetime, epoch ms) for an iat/exp claim; anything else is malformed."""
    if isinstance(value, bool):
        raise MalformedQrToken("MALFORMED_QR_TOKEN")
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:  # already epoch milliseconds
            return datetime.fromtimestamp(value / 1000.0, tz=UTC), int(value)
        return datetime.fromtimestamp(value, tz=UTC), int(value * 1000)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise MalformedQrToken("MALFORMED_QR_TOKEN") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed, int(parsed.timestamp() * 1000)
    raise MalformedQrToken("MALFORMED_QR_TOKEN")


class AccessDecision:
    """Evaluates QR credentials against the factory-local EligibilitySnapshot (§4.1.6, §7.3.1)."""

    def __init__(self, snapshot_repo: ISnapshotRepository) -> None:
        self._snapshot_repo = snapshot_repo

    def evaluate(self, qrToken: str, laneId: str, now: datetime | None = None) -> DecisionResult:
        """Check snapshot usability, operating mode (I-10), Ed25519 signature, 60s expiry,
        factory binding (ADR-11), and employee lane permissions.

        Execution order (ADR-11 & SRP):
        1. Fetch snapshot and call snapshot.getOperatingMode(now).
        2. If CANNOT_PROCESS, immediately return allowed=False with
           operatingMode=CANNOT_PROCESS and reason="ELIGIBILITY_DATA_UNUSABLE_OR_EXPIRED".
        3. Call _verify_signed_qr_token() to validate the token.
        4. Call snapshot.isEmployeeEligible(employeeId, laneId) to check access rights.
        5. Return DecisionResult(allowed=True, ...) with direction from snapshot.lanes.
        """
        eval_time = now or datetime.now(UTC)
        if eval_time.tzinfo is None:
            eval_time = eval_time.replace(tzinfo=UTC)

        # 1. Fetch snapshot and evaluate operating mode
        snapshot = self._snapshot_repo.get_snapshot()
        mode = snapshot.getOperatingMode(eval_time)

        # 2. If CANNOT_PROCESS, immediately fail closed without verifying token
        if mode == OperatingMode.CANNOT_PROCESS:
            return DecisionResult(
                allowed=False,
                operatingMode=OperatingMode.CANNOT_PROCESS,
                snapshotVersion=snapshot.snapshotVersion,
                reason="ELIGIBILITY_DATA_UNUSABLE_OR_EXPIRED",
            )

        # 3. Verify signed QR token
        try:
            claims = self._verify_signed_qr_token(
                qrToken,
                snapshot=snapshot,
                now=eval_time,
            )
        except MalformedQrToken:
            return DecisionResult(
                allowed=False,
                operatingMode=mode,
                snapshotVersion=snapshot.snapshotVersion,
                reason="MALFORMED_QR_TOKEN",
            )
        except InvalidQrSignature:
            return DecisionResult(
                allowed=False,
                operatingMode=mode,
                snapshotVersion=snapshot.snapshotVersion,
                reason="INVALID_QR_SIGNATURE",
            )
        except QrTokenExpired:
            return DecisionResult(
                allowed=False,
                operatingMode=mode,
                snapshotVersion=snapshot.snapshotVersion,
                reason="QR_TOKEN_EXPIRED",
            )
        except InvalidValidityPeriod:
            return DecisionResult(
                allowed=False,
                operatingMode=mode,
                snapshotVersion=snapshot.snapshotVersion,
                reason="QR_VALIDITY_PERIOD_INVALID",
            )
        except QrTokenNotYetValid:
            return DecisionResult(
                allowed=False,
                operatingMode=mode,
                snapshotVersion=snapshot.snapshotVersion,
                reason="QR_TOKEN_NOT_YET_VALID",
            )
        except WrongFactoryScope:
            return DecisionResult(
                allowed=False,
                operatingMode=mode,
                snapshotVersion=snapshot.snapshotVersion,
                reason="WRONG_FACTORY_SCOPE",
            )
        except Exception:
            return DecisionResult(
                allowed=False,
                operatingMode=mode,
                snapshotVersion=snapshot.snapshotVersion,
                reason="MALFORMED_QR_TOKEN",
            )

        # 4. Check employee rights for this lane
        eligible, reason = snapshot.isEmployeeEligible(claims.employeeId, laneId)
        if not eligible:
            return DecisionResult(
                allowed=False,
                operatingMode=mode,
                snapshotVersion=snapshot.snapshotVersion,
                reason=reason,
                claims=claims,
                direction=snapshot.lanes.get(laneId),
            )

        # 5. Access allowed
        return DecisionResult(
            allowed=True,
            operatingMode=mode,
            snapshotVersion=snapshot.snapshotVersion,
            reason="ALLOWED",
            claims=claims,
            direction=snapshot.lanes.get(laneId),
        )

    @staticmethod
    def _verify_signed_qr_token(
        qrToken: str,
        *args: object,
        snapshot: EligibilitySnapshot | None = None,
        verification_keys: tuple[bytes, ...] | None = None,
        expected_factory_id: str | None = None,
        now: datetime | None = None,
        clock_error_bound_ms: int | None = None,
    ) -> TokenClaims:
        """Verify Ed25519 signature against `snapshot.verificationKeys`, signed window 0 < exp - iat <= 60 s, iat not in the future
        (beyond the clock bound), and expiry at exp (valid while now < exp; rejected from now == exp),
        and factory binding (§7.3.1, ADR-11).
        """
        # Resolve parameters flexibly
        if snapshot is not None:
            keys = snapshot.verificationKeys
            fac_id = snapshot.factoryId
            bound_ms = _bound_or_zero(snapshot.clockErrorBoundMs)
            eval_now = now or (args[0] if len(args) > 0 and isinstance(args[0], datetime) else datetime.now(UTC))
        elif len(args) > 0 and isinstance(args[0], EligibilitySnapshot):
            snap = args[0]
            keys = snap.verificationKeys
            fac_id = snap.factoryId
            bound_ms = _bound_or_zero(snap.clockErrorBoundMs)
            eval_now = args[1] if len(args) > 1 and isinstance(args[1], datetime) else (now or datetime.now(UTC))
        elif len(args) > 0 and isinstance(args[0], (tuple, list)):
            keys = tuple(args[0])
            fac_id = str(args[1]) if len(args) > 1 else (expected_factory_id or "")
            eval_now = args[2] if len(args) > 2 and isinstance(args[2], datetime) else (now or datetime.now(UTC))
            bound_ms = int(args[3]) if len(args) > 3 and isinstance(args[3], (int, float)) else _bound_or_zero(clock_error_bound_ms)
        else:
            keys = verification_keys or ()
            fac_id = expected_factory_id or ""
            bound_ms = _bound_or_zero(clock_error_bound_ms)
            eval_now = now or datetime.now(UTC)

        if eval_now.tzinfo is None:
            eval_now = eval_now.replace(tzinfo=UTC)

        # 1. Split token by '.'
        if not qrToken or not isinstance(qrToken, str) or "." not in qrToken:
            raise MalformedQrToken("MALFORMED_QR_TOKEN")

        parts = qrToken.split(".")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise MalformedQrToken("MALFORMED_QR_TOKEN")

        payload_b64, sig_b64 = parts[0], parts[1]

        # 2. Base64url decode payload and signature
        try:
            payload_bytes = _b64url_decode(payload_b64)
            sig_bytes = _b64url_decode(sig_b64)
        except Exception as exc:
            raise MalformedQrToken("MALFORMED_QR_TOKEN") from exc

        # Parse JSON payload
        try:
            payload_dict = json.loads(payload_bytes.decode("utf-8"))
        except Exception as exc:
            raise MalformedQrToken("MALFORMED_QR_TOKEN") from exc

        if not isinstance(payload_dict, dict):
            raise MalformedQrToken("MALFORMED_QR_TOKEN")

        jti = payload_dict.get("jti")
        sub = payload_dict.get("sub")
        aud = payload_dict.get("aud")
        iat = payload_dict.get("iat")
        exp = payload_dict.get("exp")

        if not jti or not sub or not aud or exp is None or iat is None:
            raise MalformedQrToken("MALFORMED_QR_TOKEN")

        # 3. Verify signature iteratively against verificationKeys
        if not keys:
            raise InvalidQrSignature("INVALID_QR_SIGNATURE")

        verified = False
        for key_item in keys:
            try:
                pub_key = _load_public_key(key_item)
            except Exception:
                continue

            try:
                pub_key.verify(sig_bytes, payload_b64.encode("utf-8"))
                verified = True
                break
            except Exception:
                pass

            try:
                pub_key.verify(sig_bytes, payload_bytes)
                verified = True
                break
            except Exception:
                pass

        if not verified:
            raise InvalidQrSignature("INVALID_QR_SIGNATURE")

        # 4. Check audience
        if aud != fac_id:
            raise WrongFactoryScope("WRONG_FACTORY_SCOPE")

        # 5. Parse exp and iat (epoch seconds, epoch ms, or ISO-8601)
        exp_dt, exp_ms = _parse_time_claim(exp)
        iat_dt, iat_ms = _parse_time_claim(iat)

        # 6. Signed validity window must be 0 < exp - iat <= 60 s (ADR-11), whoever issued the token
        lifetime_ms = exp_ms - iat_ms
        if lifetime_ms <= 0 or lifetime_ms > MAX_TOKEN_LIFETIME_MS:
            raise InvalidValidityPeriod("QR_VALIDITY_PERIOD_INVALID")

        # 7. Not issued in the future (beyond the known clock error bound, always < 2000 ms)
        eval_now_ms = int(eval_now.timestamp() * 1000)
        if iat_ms > eval_now_ms + bound_ms:
            raise QrTokenNotYetValid("QR_TOKEN_NOT_YET_VALID")

        # 8. The token expires at exp: valid while now < exp, rejected from now == exp. The clock bound is never
        #    added to exp (team decision 08/11).
        if eval_now_ms >= exp_ms:
            raise QrTokenExpired("QR_TOKEN_EXPIRED")

        return TokenClaims(
            tokenId=str(jti),
            employeeId=str(sub),
            factoryId=str(aud),
            issuedAt=iat_dt,
            expiresAt=exp_dt,
        )
