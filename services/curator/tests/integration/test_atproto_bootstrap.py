from __future__ import annotations

import base64
import os

import pytest

from feed_passport.adapters.platforms.live import AtprotoSidecarLiveAdapter
from feed_passport.domain import CapabilityLevel
from feed_passport.runtime import build_service_bundle


def encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def clear_feed_passport_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("FEED_PASSPORT_"):
            monkeypatch.delenv(name, raising=False)


def configure_connection_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEED_PASSPORT_CONNECTION_KEY_B64", encoded(b"c" * 32))
    monkeypatch.setenv("FEED_PASSPORT_CONNECTION_INDEX_KEY_B64", encoded(b"i" * 32))


def configure_atproto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEED_PASSPORT_ATPROTO_SIDECAR_URL", "http://127.0.0.1:4310")
    monkeypatch.setenv(
        "FEED_PASSPORT_ATPROTO_SIDECAR_INTERNAL_SECRET",
        "internal-sidecar-secret-with-at-least-32-characters",
    )
    monkeypatch.setenv(
        "FEED_PASSPORT_ATPROTO_APP_CALLBACK_URI",
        "http://127.0.0.1:5173/oauth/callback",
    )


def test_bootstrap_registers_guided_token_free_sidecar_without_oauth_vault(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_feed_passport_environment(monkeypatch)
    configure_connection_keys(monkeypatch)
    configure_atproto(monkeypatch)

    bundle = build_service_bundle(database_path=tmp_path / "curator.db", seed_demo=False)
    try:
        assert bundle.connection_registry is not None
        assert bundle.oauth_service is None
        assert bundle.atproto_oauth_service is not None
        assert bundle.atproto_sidecar_client is not None
        adapter = bundle.application.adapters["bluesky"]
        assert isinstance(adapter, AtprotoSidecarLiveAdapter)
        assert adapter.capabilities("not-yet-connected").level is CapabilityLevel.GUIDED
        rendered = repr(bundle.atproto_sidecar_client)
        assert "internal-sidecar-secret" not in rendered
        assert "<redacted>" in rendered
    finally:
        bundle.close()


@pytest.mark.parametrize(
    "missing",
    (
        "FEED_PASSPORT_ATPROTO_SIDECAR_URL",
        "FEED_PASSPORT_ATPROTO_SIDECAR_INTERNAL_SECRET",
        "FEED_PASSPORT_ATPROTO_APP_CALLBACK_URI",
    ),
)
def test_bootstrap_rejects_partial_atproto_configuration_before_opening_database(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    clear_feed_passport_environment(monkeypatch)
    configure_connection_keys(monkeypatch)
    configure_atproto(monkeypatch)
    monkeypatch.delenv(missing)

    with pytest.raises(ValueError, match="must be configured together"):
        build_service_bundle(database_path=tmp_path / "partial.db", seed_demo=False)

    assert not (tmp_path / "partial.db").exists()


def test_bootstrap_rejects_atproto_without_connection_encryption_keys(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_feed_passport_environment(monkeypatch)
    configure_atproto(monkeypatch)

    with pytest.raises(ValueError, match="connection encryption keys"):
        build_service_bundle(database_path=tmp_path / "missing-keys.db", seed_demo=False)

    assert not (tmp_path / "missing-keys.db").exists()


def test_bootstrap_rejects_standard_oauth_provider_without_vault_key(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_feed_passport_environment(monkeypatch)
    configure_connection_keys(monkeypatch)
    monkeypatch.setenv("FEED_PASSPORT_YOUTUBE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("FEED_PASSPORT_YOUTUBE_OAUTH_CLIENT_SECRET", "client-secret")

    with pytest.raises(ValueError, match="OAuth vault key"):
        build_service_bundle(database_path=tmp_path / "missing-vault.db", seed_demo=False)

    assert not (tmp_path / "missing-vault.db").exists()
