"""[PACKAGE 2] Demo helper representing QR Credential Issuance from M4 Access & Configuration (§4.1.6 Figure 4.5, §7.3.1, ADR-11).

Uses Ed25519 asymmetric signing:
- Central site holds the private key to issue 60-second, factory-bound, single-use QR tokens.
- Factory holds only the public key in EligibilitySnapshot to verify tokens locally.
"""

import base64
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


class QrCredentialIssuance:
    """Issues signed Ed25519 QR credentials valid for 60 seconds and bound to one factory (ADR-11)."""

    TOKEN_LIFETIME_SEC: int = 60

    def __init__(self, private_key: Ed25519PrivateKey | None = None) -> None:
        self._private_key = private_key or Ed25519PrivateKey.generate()

    @property
    def public_key_pem(self) -> bytes:
        """Export the Ed25519 public verification key in PEM format for factory snapshots."""
        return self._private_key.public_key().public_bytes(
            encoding=Encoding.PEM,
            format=PublicFormat.SubjectPublicKeyInfo,
        )

    @property
    def private_key_pem(self) -> bytes:
        return self._private_key.private_bytes(
            encoding=Encoding.PEM,
            format=PrivateFormat.PKCS8,
            encryption_algorithm=NoEncryption(),
        )

    def issue_qr_token(
        self,
        employee_id: str | None = None,
        factory_id: str | None = None,
        token_id: str | None = None,
        issued_at: datetime | None = None,
        lifetime_sec: int | None = None,
        *,
        employeeId: str | None = None,
        factoryId: str | None = None,
        jti: str | None = None,
        **kwargs: object,
    ) -> tuple[str, dict[str, Any]]:
        """Issue a signed, short-lived, factory-bound QR credential (§7.3.1).

        Claims dict includes:
        - "jti": unique token_id or generated tok-<hex>
        - "sub": employee_id
        - "aud": factory_id
        - "iat": current UTC epoch (int seconds)
        - "exp": iat + validity window (int seconds)
        No other PII is included (ADR-11).
        """
        emp_id = employee_id if employee_id is not None else employeeId
        if emp_id is None:
            raise ValueError("employeeId / employee_id is required")

        fac_id = factory_id if factory_id is not None else factoryId
        if fac_id is None:
            raise ValueError("factoryId / factory_id is required")

        token_jti = token_id if token_id is not None else jti
        if not token_jti:
            token_jti = f"tok-{uuid.uuid4().hex}"

        now_utc = issued_at or datetime.now(UTC)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=UTC)

        iat_val = int(now_utc.timestamp())
        validity = lifetime_sec if lifetime_sec is not None else self.TOKEN_LIFETIME_SEC
        exp_val = iat_val + validity

        claims: dict[str, Any] = {
            "jti": str(token_jti),
            "sub": str(emp_id),
            "aud": str(fac_id),
            "iat": iat_val,
            "exp": exp_val,
        }

        payload_bytes = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
        signature_bytes = self._private_key.sign(payload_b64.encode("utf-8"))
        sig_b64 = base64.urlsafe_b64encode(signature_bytes).decode("ascii").rstrip("=")

        token = f"{payload_b64}.{sig_b64}"
        return token, claims
