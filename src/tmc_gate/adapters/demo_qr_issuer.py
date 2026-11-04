"""[PACKAGE 2] Demo helper representing QR Credential Issuance from M4 Access & Configuration (§4.1.6 Figure 4.5, §7.3.1, ADR-11).

Uses Ed25519 asymmetric signing:
- Central site holds the private key to issue 60-second, factory-bound, single-use QR tokens.
- Factory holds only the public key in EligibilitySnapshot to verify tokens locally.
"""

from datetime import datetime

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
        employee_id: str,
        factory_id: str,
        token_id: str | None = None,
        issued_at: datetime | None = None,
        lifetime_sec: int | None = None,
    ) -> tuple[str, dict[str, str]]:
        """Issue a signed, short-lived, factory-bound QR credential (§7.3.1).

        TODO [Package 2]:
        1. Build claims dict with keys:
           - `"jti"`: unique token_id (or generated `tok-<hex>`)
           - `"sub"`: `employee_id`
           - `"aud"`: `factory_id`
           - `"iat"`: ISO-8601 UTC timestamp of `issued_at`
           - `"exp"`: ISO-8601 UTC timestamp of `issued_at + timedelta(seconds=lifetime_sec or 60)`
        2. Serialize claims to UTF-8 JSON bytes and sign with `self._private_key.sign(payload_bytes)`.
        3. Base64url-encode both payload and signature as `f"{payload_b64}.{sig_b64}"`.
        4. Return `(qr_token_str, claims)`.
        """
        raise NotImplementedError("TODO [Package 2]: Implement QrCredentialIssuance.issue_qr_token")
