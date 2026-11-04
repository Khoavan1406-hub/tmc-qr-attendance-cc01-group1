"""[PACKAGE 2] Unit tests for Ed25519 QR credential issuance and AccessDecision evaluation (§7.3.1, ADR-11)."""

import pytest


def test_qr_issuance_and_signature_verification() -> None:
    """TODO [Package 2]:
    Write unit test verifying `QrCredentialIssuance.issue_qr_token` and `AccessDecision.evaluate`:
    - Valid Ed25519 token within 60s for the matching factory returns `allowed=True`.
    - Tampered signature or malformed token returns `allowed=False` (`INVALID_QR_SIGNATURE` / `MALFORMED_QR_TOKEN`).
    """
    pytest.skip("TODO [Package 2]: Write unit test for Ed25519 QR issuance and signature verification")


def test_qr_expiry_and_factory_scope_rejection() -> None:
    """TODO [Package 2]:
    Write unit test verifying:
    - Token older than 60s (+ clockErrorBoundSec) is rejected with `QR_TOKEN_EXPIRED`.
    - Token issued for factory `LA` presented at factory `BD` is rejected with `WRONG_FACTORY_SCOPE`.
    """
    pytest.skip("TODO [Package 2]: Write unit test for 60s expiry and factory binding")
