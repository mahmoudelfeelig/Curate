from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from feed_passport.infrastructure.live_certification import (
    CERTIFICATION_FORMAT,
    LiveCertificationError,
    LiveCertificationVerifier,
)


NOW = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
REVISION = "a" * 40


def payload(*, platform: str = "youtube") -> dict[str, object]:
    return {
        "format": CERTIFICATION_FORMAT,
        "platform": platform,
        "environment": "authorized_live",
        "account_class": "dummy",
        "result": "passed",
        "certified_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(days=14)).isoformat(),
        "code_revision": REVISION,
        "execute": ["subscribe_creator", "unsubscribe_creator"],
        "observe": ["subscriptions"],
        "verify": ["subscriptions"],
        "rollback": ["subscribe_creator", "unsubscribe_creator"],
        "receipt_ref": "conformance-local-001",
        "provider_approval_ref": "reddit-approval-001" if platform == "reddit" else "",
        "checks": [
            {"name": "prepare_before_write", "result": "passed"},
            {"name": "apply_and_verify", "result": "passed"},
            {"name": "inverse_rollback", "result": "passed"},
        ],
    }


def test_signed_dummy_account_receipt_promotes_only_covered_subset() -> None:
    verifier = LiveCertificationVerifier(
        hmac_key=b"h" * 32,
        expected_revision=REVISION,
        now=NOW,
    )
    signed = verifier.sign(payload())

    certification = verifier.verify(signed)

    assert certification.platform == "youtube"
    assert {value.value for value in certification.execute} == {
        "subscribe_creator",
        "unsubscribe_creator",
    }
    assert certification.evidence_sha256 == signed["evidence_sha256"]
    assert certification.expires_at == NOW + timedelta(days=14)
    assert certification.code_revision == REVISION


def test_tampered_or_expired_receipt_is_rejected() -> None:
    verifier = LiveCertificationVerifier(
        hmac_key=b"h" * 32,
        expected_revision=REVISION,
        now=NOW,
    )
    signed = verifier.sign(payload())
    signed["execute"] = ["subscribe_creator"]
    with pytest.raises(LiveCertificationError, match="digest"):
        verifier.verify(signed)

    late_verifier = LiveCertificationVerifier(
        hmac_key=b"h" * 32,
        expected_revision=REVISION,
        now=NOW + timedelta(days=15),
    )
    with pytest.raises(LiveCertificationError, match="expired"):
        late_verifier.verify(verifier.sign(payload()))


def test_expired_signed_receipt_is_accepted_only_for_observation_recovery() -> None:
    signer = LiveCertificationVerifier(
        hmac_key=b"h" * 32,
        expected_revision=REVISION,
        now=NOW,
    )
    signed = signer.sign(payload())
    late_verifier = LiveCertificationVerifier(
        hmac_key=b"h" * 32,
        expected_revision=REVISION,
        now=NOW + timedelta(days=15),
    )

    recovered = late_verifier.verify_for_reconciliation(signed)

    assert recovered.expires_at == NOW + timedelta(days=14)
    with pytest.raises(LiveCertificationError, match="expired"):
        late_verifier.verify(signed)

    tampered = dict(signed)
    tampered["execute"] = ["subscribe_creator"]
    with pytest.raises(LiveCertificationError, match="digest"):
        late_verifier.verify_for_reconciliation(tampered)


def test_future_dated_receipt_cannot_promote_before_its_certification_time() -> None:
    future_payload = payload()
    future_payload["certified_at"] = (NOW + timedelta(minutes=5)).isoformat()
    future_payload["expires_at"] = (NOW + timedelta(days=14)).isoformat()
    future_signer = LiveCertificationVerifier(
        hmac_key=b"h" * 32,
        expected_revision=REVISION,
        now=NOW + timedelta(minutes=5),
    )
    signed = future_signer.sign(future_payload)
    current_verifier = LiveCertificationVerifier(
        hmac_key=b"h" * 32,
        expected_revision=REVISION,
        now=NOW,
    )

    with pytest.raises(LiveCertificationError, match="not yet active"):
        current_verifier.verify(signed)
    with pytest.raises(LiveCertificationError, match="not yet active"):
        current_verifier.verify_for_reconciliation(signed)


def test_signed_receipt_for_another_revision_cannot_promote_live_capability() -> None:
    signer = LiveCertificationVerifier(
        hmac_key=b"h" * 32,
        expected_revision=REVISION,
        now=NOW,
    )
    deployed_verifier = LiveCertificationVerifier(
        hmac_key=b"h" * 32,
        expected_revision="b" * 40,
        now=NOW,
    )

    with pytest.raises(LiveCertificationError, match="different deployed Git revision"):
        deployed_verifier.verify(signer.sign(payload()))


def test_reddit_requires_provider_approval_reference() -> None:
    verifier = LiveCertificationVerifier(
        hmac_key=b"h" * 32,
        expected_revision=REVISION,
        now=NOW,
    )
    value = payload(platform="reddit")
    value["provider_approval_ref"] = ""
    with pytest.raises(LiveCertificationError, match="provider approval"):
        verifier.sign(value)


def test_receipt_cannot_contain_credential_fields() -> None:
    verifier = LiveCertificationVerifier(
        hmac_key=b"h" * 32,
        expected_revision=REVISION,
        now=NOW,
    )
    value = payload()
    value["checks"] = [
        {"name": "bad", "result": "passed", "access_token": "must-not-be-stored"}
    ]
    with pytest.raises(ValueError, match="credential material"):
        verifier.sign(value)
