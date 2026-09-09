from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from feed_passport.adapters.platforms.base import UnsupportedPlatformAction
from feed_passport.adapters.platforms.live import YouTubeLiveAdapter
from feed_passport.adapters.platforms.live.youtube import YOUTUBE_SCOPE
from feed_passport.domain import ActionStatus, ActionType, CapabilityLevel, ProposedAction
from feed_passport.infrastructure.live_certification import (
    CERTIFICATION_FORMAT,
    LiveCertificationVerifier,
)
from feed_passport.ports.live_platform import PreparedRemoteAction
from feed_passport.runtime import build_service_bundle


REVISION = "a" * 40
HMAC_KEY = b"h" * 32


def encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def clear_feed_passport_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("FEED_PASSPORT_"):
            monkeypatch.delenv(name, raising=False)


def test_restart_loads_expired_exact_revision_only_for_observation_recovery(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_feed_passport_environment(monkeypatch)
    current = datetime.now(UTC)
    certified_at = current - timedelta(days=2)
    expires_at = current - timedelta(days=1)
    signer = LiveCertificationVerifier(
        hmac_key=HMAC_KEY,
        expected_revision=REVISION,
        now=certified_at + timedelta(minutes=1),
    )
    signed = signer.sign(
        {
            "format": CERTIFICATION_FORMAT,
            "platform": "youtube",
            "environment": "authorized_live",
            "account_class": "dummy",
            "result": "passed",
            "certified_at": certified_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "code_revision": REVISION,
            "execute": ["subscribe_creator"],
            "observe": ["subscriptions"],
            "verify": ["subscriptions"],
            "rollback": ["subscribe_creator"],
            "receipt_ref": "conformance:youtube:historical-dummy",
            "provider_approval_ref": "",
            "checks": [
                {"name": "prepare_before_write", "result": "passed"},
                {"name": "apply_and_verify", "result": "passed"},
                {"name": "inverse_rollback", "result": "passed"},
            ],
        }
    )
    certification_dir = tmp_path / "certifications"
    certification_dir.mkdir()
    (certification_dir / "youtube.json").write_text(
        json.dumps(signed),
        encoding="utf-8",
    )
    monkeypatch.setenv("FEED_PASSPORT_CONNECTION_KEY_B64", encoded(b"c" * 32))
    monkeypatch.setenv("FEED_PASSPORT_CONNECTION_INDEX_KEY_B64", encoded(b"i" * 32))
    monkeypatch.setenv("FEED_PASSPORT_OAUTH_VAULT_KEY_B64", encoded(b"v" * 32))
    monkeypatch.setenv("FEED_PASSPORT_LIVE_CERTIFICATIONS_DIR", str(certification_dir))
    monkeypatch.setenv("FEED_PASSPORT_LIVE_CERTIFICATION_HMAC_KEY_B64", encoded(HMAC_KEY))
    monkeypatch.setenv("FEED_PASSPORT_CODE_REVISION", REVISION)
    database_path = tmp_path / "restart.db"

    initial = build_service_bundle(database_path=database_path, seed_demo=False)
    try:
        assert initial.connection_registry is not None
        connection = initial.connection_registry.register_connection(
            owner_id="owner-one",
            platform="youtube",
            external_subject="dummy-youtube-subject",
            credential_ref="credential:youtube:dummy",
            metadata={"granted_scopes": [YOUTUBE_SCOPE]},
            now=current,
        )
    finally:
        initial.close()

    restarted = build_service_bundle(database_path=database_path, seed_demo=False)
    try:
        adapter = restarted.application.adapters["youtube"]
        assert isinstance(adapter, YouTubeLiveAdapter)
        assert adapter.validated_live_certification is not None
        assert adapter.validated_live_certification.expires_at <= datetime.now(UTC)
        assert adapter.capabilities(connection.id).level is CapabilityLevel.GUIDED

        action = ProposedAction(
            id="action:historical-subscribe",
            destination_id=connection.id,
            action_type=ActionType.SUBSCRIBE_CREATOR,
            target="UC12345678901234567890",
            reason="Recover the outcome of an already-dispatched dummy-account action.",
            idempotency_key="idempotency:historical-subscribe",
            reversible=True,
        )
        prepared = PreparedRemoteAction(
            platform="youtube",
            connection_id=connection.id,
            action=action,
            before_state={
                "present": False,
                "target_id": action.target,
                "remote_ref": None,
            },
            desired_state={"present": True, "target_id": action.target},
            prepared_at=certified_at + timedelta(minutes=1),
        )
        mutation_called = False

        def read_existing_state(*_args, **_kwargs):
            return {
                "present": True,
                "target_id": action.target,
                "remote_ref": "subscription-existing",
            }

        def reject_mutation(*_args, **_kwargs):
            nonlocal mutation_called
            mutation_called = True
            raise AssertionError("expired-cert reconciliation must never mutate")

        monkeypatch.setattr(adapter, "_read_action_state", read_existing_state)
        monkeypatch.setattr(adapter, "_mutate_to_state", reject_mutation)

        with pytest.raises(
            UnsupportedPlatformAction,
            match="current validated certification",
        ):
            adapter.prepare_remote_action(connection.id, action, now=current)
        with pytest.raises(
            UnsupportedPlatformAction,
            match="current validated certification",
        ):
            adapter.apply_prepared_action(prepared, now=current)

        observed = adapter.reconcile_remote_action(prepared, now=current)

        assert observed.status is ActionStatus.EXECUTED
        assert observed.platform_reference == "subscription-existing"
        assert mutation_called is False
    finally:
        restarted.close()
