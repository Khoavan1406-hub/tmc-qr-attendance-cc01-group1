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
        """Verify Ed25519 signature against `snapshot.verificationKeys`, 60s lifetime (with clock skew),
        and factory binding (§7.3.1, ADR-11).
        """
        # Resolve parameters flexibly
        if snapshot is not None:
            keys = snapshot.verificationKeys
            fac_id = snapshot.factoryId
            bound_ms = snapshot.clockErrorBoundMs or 2000
            eval_now = now or (args[0] if len(args) > 0 and isinstance(args[0], datetime) else datetime.now(UTC))
        elif len(args) > 0 and isinstance(args[0], EligibilitySnapshot):
            snap = args[0]
            keys = snap.verificationKeys
            fac_id = snap.factoryId
            bound_ms = snap.clockErrorBoundMs or 2000
            eval_now = args[1] if len(args) > 1 and isinstance(args[1], datetime) else (now or datetime.now(UTC))
        elif len(args) > 0 and isinstance(args[0], (tuple, list)):
            keys = tuple(args[0])
            fac_id = str(args[1]) if len(args) > 1 else (expected_factory_id or "")
            eval_now = args[2] if len(args) > 2 and isinstance(args[2], datetime) else (now or datetime.now(UTC))
            bound_ms = int(args[3]) if len(args) > 3 and isinstance(args[3], (int, float)) else (clock_error_bound_ms or 2000)
        else:
            keys = verification_keys or ()
            fac_id = expected_factory_id or ""
            bound_ms = clock_error_bound_ms or 2000
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

        # 5. Check expiration: Token is valid if now <= exp + 2000 (allowing up to 2000ms clock skew)
        if isinstance(exp, (int, float)):
            if exp > 10_000_000_000:
                exp_ms = int(exp)
                exp_dt = datetime.fromtimestamp(exp / 1000.0, tz=UTC)
            else:
                exp_ms = int(exp * 1000)
                exp_dt = datetime.fromtimestamp(exp, tz=UTC)
        elif isinstance(exp, str):
            exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=UTC)
            exp_ms = int(exp_dt.timestamp() * 1000)
        else:
            raise MalformedQrToken("MALFORMED_QR_TOKEN")

        eval_now_ms = int(eval_now.timestamp() * 1000)
        allowed_skew_ms = min(bound_ms, 2000) if bound_ms is not None else 2000
        if allowed_skew_ms < 0:
            allowed_skew_ms = 0

        if eval_now_ms > exp_ms + allowed_skew_ms:
            raise QrTokenExpired("QR_TOKEN_EXPIRED")

        # Parse iat
        if isinstance(iat, (int, float)):
            iat_dt = (
                datetime.fromtimestamp(iat / 1000.0, tz=UTC)
                if iat > 10_000_000_000
                else datetime.fromtimestamp(iat, tz=UTC)
            )
        elif isinstance(iat, str):
            iat_dt = datetime.fromisoformat(iat.replace("Z", "+00:00"))
            if iat_dt.tzinfo is None:
                iat_dt = iat_dt.replace(tzinfo=UTC)
        else:
            raise MalformedQrToken("MALFORMED_QR_TOKEN")

        return TokenClaims(
            tokenId=str(jti),
            employeeId=str(sub),
            factoryId=str(aud),
            issuedAt=iat_dt,
            expiresAt=exp_dt,
        )
