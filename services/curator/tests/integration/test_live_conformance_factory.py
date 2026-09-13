from __future__ import annotations

import base64
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from feed_passport.application.oauth import OAuthProviderCatalog
from feed_passport.domain.connections import ConnectionStatus
from feed_passport.domain.models import ActionType, CapabilityLevel, ProposedAction
from feed_passport.infrastructure.connection_registry import EncryptedConnectionRegistry
from feed_passport.infrastructure.crypto import AesGcmKeyring
from feed_passport.infrastructure.oauth_vault import LocalEncryptedOAuthVault
from feed_passport.infrastructure.sqlite_store import SQLiteStore
from feed_passport.runtime.live_conformance import (
    ConformanceAction,
    LiveConformanceGateError,
    LiveConformanceRequest,
)
from feed_passport.runtime.live_conformance_factory import BuiltInLiveConformanceFactory


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
CONNECTION_KEY = b"c" * 32
INDEX_KEY = b"i" * 32
VAULT_KEY = b"v" * 32
HMAC_KEY = b"h" * 32
KEY_ID = "factory-test-v1"


class NoNetworkHttpClient:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.closed = False

    def request(self, *args: object, **kwargs: object) -> object:
        self.calls.append((*args, kwargs))
        raise AssertionError("factory construction must not make a network request")

    def close(self) -> None:
        self.closed = True


class JsonResponse:
    status_code = 200
    headers: dict[str, str] = {}
    text = "json"

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def json(self) -> dict[str, object]:
        return self.payload


class ReadOnlyHttpClient(NoNetworkHttpClient):
    def request(self, *args: object, **kwargs: object) -> JsonResponse:
        self.calls.append((*args, kwargs))
        return JsonResponse({"items": []})


def encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def request(
    tmp_path: Path,
    *,
    platform: str,
    connection_id: str,
    action: ActionType,
    acknowledged: bool = True,
    provider_approval_ref: str = "",
) -> LiveConformanceRequest:
    return LiveConformanceRequest(
        acknowledge_authorized_dummy_account_mutations=acknowledged,
        owner_id="owner-dummy",
        connection_id=connection_id,
        platform=platform,
        actions=(ConformanceAction(action, "target-one"),),
        code_revision="a" * 40,
        provider_approval_ref=provider_approval_ref,
        output_path=tmp_path / f"{platform}-certification.json",
        hmac_key=HMAC_KEY,
    )


def seed_standard_database(
    path: Path,
    *,
    platform: str = "youtube",
    connection_id: str = "connection-youtube-dummy",
    status: ConnectionStatus = ConnectionStatus.ACTIVE,
    scopes: frozenset[str] = frozenset({"https://www.googleapis.com/auth/youtube"}),
) -> None:
    store = SQLiteStore(path)
    vault = LocalEncryptedOAuthVault(
        store,
        encryption_key=VAULT_KEY,
        key_id=KEY_ID,
        providers=OAuthProviderCatalog({}),
        http_client=NoNetworkHttpClient(),
        id_factory=lambda: f"credential-{platform}-dummy",
    )
    credential_ref = vault.put(
        owner_id="owner-dummy",
        platform=platform,
        token_response={
            "access_token": "encrypted-dummy-access-token",
            "refresh_token": "encrypted-dummy-refresh-token",
            "expires_in": 3600,
        },
        granted_scopes=scopes,
        now=NOW,
    )
    registry = EncryptedConnectionRegistry(
        store,
        AesGcmKeyring(
            active_key_id=KEY_ID,
            keys={KEY_ID: CONNECTION_KEY},
            index_key=INDEX_KEY,
        ),
        id_factory=lambda: connection_id,
    )
    registry.register_connection(
        owner_id="owner-dummy",
        platform=platform,
        external_subject=f"dummy-{platform}-subject",
        credential_ref=credential_ref,
        metadata={"granted_scopes": sorted(scopes)},
        now=NOW,
        status=status,
    )
    store.close()


def seed_bluesky_database(path: Path, *, connection_id: str) -> None:
    store = SQLiteStore(path)
    registry = EncryptedConnectionRegistry(
        store,
        AesGcmKeyring(
            active_key_id=KEY_ID,
            keys={KEY_ID: CONNECTION_KEY},
            index_key=INDEX_KEY,
        ),
        id_factory=lambda: connection_id,
    )
    registry.register_connection(
        owner_id="owner-dummy",
        platform="bluesky",
        external_subject="did:plc:dummy123",
        credential_ref="b" * 43,
        metadata={"granted_scopes": ["atproto", "transition:generic"]},
        now=NOW,
    )
    store.close()


def base_environment(database_path: Path) -> dict[str, str]:
    return {
        "FEED_PASSPORT_DB_PATH": str(database_path),
        "FEED_PASSPORT_CONNECTION_KEY_B64": encoded(CONNECTION_KEY),
        "FEED_PASSPORT_CONNECTION_INDEX_KEY_B64": encoded(INDEX_KEY),
        "FEED_PASSPORT_CONNECTION_KEY_ID": KEY_ID,
        "FEED_PASSPORT_PLATFORM_HTTP_TIMEOUT_SECONDS": "5",
    }


def standard_environment(database_path: Path, *, platform: str = "youtube") -> dict[str, str]:
    prefix = f"FEED_PASSPORT_{platform.upper()}_OAUTH_"
    return {
        **base_environment(database_path),
        "FEED_PASSPORT_OAUTH_VAULT_KEY_B64": encoded(VAULT_KEY),
        "FEED_PASSPORT_OAUTH_VAULT_KEY_ID": KEY_ID,
        "FEED_PASSPORT_OAUTH_REDIRECT_URIS": "http://127.0.0.1:5173/oauth/callback",
        f"{prefix}CLIENT_ID": "dummy-client-id",
        f"{prefix}CLIENT_SECRET": "dummy-client-secret",
        f"{prefix}CLIENT_AUTH": (
            "client_secret_post" if platform == "youtube" else "client_secret_basic"
        ),
        **(
            {f"{prefix}USER_AGENT": "windows:feed-passport:test (by /u/dummy-operator)"}
            if platform == "reddit"
            else {}
        ),
    }


@pytest.mark.parametrize(
    ("platform", "action", "scopes", "approval"),
    (
        (
            "youtube",
            ActionType.SUBSCRIBE_CREATOR,
            frozenset({"https://www.googleapis.com/auth/youtube"}),
            "",
        ),
        (
            "x",
            ActionType.FOLLOW_CREATOR,
            frozenset({"users.read", "follows.read", "follows.write"}),
            "",
        ),
        (
            "reddit",
            ActionType.SUBSCRIBE_CREATOR,
            frozenset({"mysubreddits", "subscribe"}),
            "provider-approval-dummy",
        ),
    ),
)
def test_builtin_factory_restores_standard_vault_and_exact_action_without_network(
    tmp_path: Path,
    platform: str,
    action: ActionType,
    scopes: frozenset[str],
    approval: str,
) -> None:
    database = tmp_path / f"{platform}.db"
    connection_id = f"connection-{platform}-dummy"
    seed_standard_database(
        database,
        platform=platform,
        connection_id=connection_id,
        scopes=scopes,
    )
    http = NoNetworkHttpClient()
    factory = BuiltInLiveConformanceFactory(
        http_client_factory=lambda _timeout: http,
        clock=lambda: NOW,
        checkout_verifier=lambda _request: None,
    )
    gated = request(
        tmp_path,
        platform=platform,
        connection_id=connection_id,
        action=action,
        provider_approval_ref=approval,
    )

    with patch.dict(os.environ, standard_environment(database, platform=platform), clear=True):
        runtime = factory(gated)
    try:
        manifest = runtime.adapter.capabilities(gated.connection_id)
        assert manifest.level is CapabilityLevel.EXECUTABLE
        assert manifest.execute == frozenset({action})
        assert manifest.rollback == manifest.execute
        assert manifest.certified_at is None
        assert manifest.evidence_url is None
        assert not http.calls
    finally:
        runtime.close()
    assert http.closed is True


def test_builtin_factory_exact_gate_can_prepare_its_own_uncertified_candidate(
    tmp_path: Path,
) -> None:
    database = tmp_path / "youtube-prepare.db"
    connection_id = "connection-youtube-dummy"
    seed_standard_database(database, connection_id=connection_id)
    http = ReadOnlyHttpClient()
    factory = BuiltInLiveConformanceFactory(
        http_client_factory=lambda _timeout: http,
        clock=lambda: NOW,
        checkout_verifier=lambda _request: None,
    )
    gated = request(
        tmp_path,
        platform="youtube",
        connection_id=connection_id,
        action=ActionType.SUBSCRIBE_CREATOR,
    )
    action = ProposedAction(
        id="conformance-action",
        destination_id=connection_id,
        action_type=ActionType.SUBSCRIBE_CREATOR,
        target="UCkRfArvrzheW2E7b6SVT7vQ",
        reason="authorized dummy-account live conformance",
        idempotency_key="conformance-action",
        reversible=True,
    )

    with patch.dict(os.environ, standard_environment(database), clear=True):
        runtime = factory(gated)
        try:
            prepared = runtime.adapter.prepare_remote_action(
                connection_id,
                action,
                now=NOW,
            )
        finally:
            runtime.close()

    assert prepared.before_state["present"] is False
    assert prepared.desired_state["present"] is True
    assert len(http.calls) == 1


def test_builtin_factory_uses_token_isolated_atproto_sidecar_without_network(
    tmp_path: Path,
) -> None:
    database = tmp_path / "bluesky.db"
    connection_id = "connection-bluesky-dummy"
    seed_bluesky_database(database, connection_id=connection_id)
    http = NoNetworkHttpClient()
    environment = {
        **base_environment(database),
        "FEED_PASSPORT_ATPROTO_SIDECAR_URL": "http://127.0.0.1:43127",
        "FEED_PASSPORT_ATPROTO_SIDECAR_INTERNAL_SECRET": "s" * 48,
    }
    factory = BuiltInLiveConformanceFactory(
        http_client_factory=lambda _timeout: http,
        clock=lambda: NOW,
        checkout_verifier=lambda _request: None,
    )
    gated = request(
        tmp_path,
        platform="bluesky",
        connection_id=connection_id,
        action=ActionType.FOLLOW_CREATOR,
    )

    with patch.dict(os.environ, environment, clear=True):
        runtime = factory(gated)
    try:
        manifest = runtime.adapter.capabilities(gated.connection_id)
        assert manifest.execute == frozenset({ActionType.FOLLOW_CREATOR})
        assert "Sidecar" in type(runtime.adapter).__mro__[2].__name__
        assert not http.calls
        rendered = repr(runtime.adapter)
        assert environment["FEED_PASSPORT_ATPROTO_SIDECAR_INTERNAL_SECRET"] not in rendered
        assert "b" * 43 not in rendered
    finally:
        runtime.close()


def test_builtin_factory_refuses_before_resources_without_mutation_acknowledgement(
    tmp_path: Path,
) -> None:
    http_factory_calls: list[float] = []
    factory = BuiltInLiveConformanceFactory(
        http_client_factory=lambda timeout: http_factory_calls.append(timeout),  # type: ignore[arg-type]
        clock=lambda: NOW,
        checkout_verifier=lambda _request: None,
    )
    gated = request(
        tmp_path,
        platform="youtube",
        connection_id="connection-youtube-dummy",
        action=ActionType.SUBSCRIBE_CREATOR,
        acknowledged=False,
    )

    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(LiveConformanceGateError, match="acknowledgement"):
            factory(gated)
    assert http_factory_calls == []


@pytest.mark.parametrize(
    ("status", "scopes", "message"),
    (
        (ConnectionStatus.REAUTH_REQUIRED, frozenset({"https://www.googleapis.com/auth/youtube"}), "active"),
        (ConnectionStatus.ACTIVE, frozenset({"youtube.readonly"}), "scopes"),
    ),
)
def test_builtin_factory_rejects_inactive_or_under_scoped_owner_binding(
    tmp_path: Path,
    status: ConnectionStatus,
    scopes: frozenset[str],
    message: str,
) -> None:
    database = tmp_path / f"rejected-{status.value}.db"
    seed_standard_database(database, status=status, scopes=scopes)
    http = NoNetworkHttpClient()
    factory = BuiltInLiveConformanceFactory(
        http_client_factory=lambda _timeout: http,
        clock=lambda: NOW,
        checkout_verifier=lambda _request: None,
    )
    gated = request(
        tmp_path,
        platform="youtube",
        connection_id="connection-youtube-dummy",
        action=ActionType.SUBSCRIBE_CREATOR,
    )

    with patch.dict(os.environ, standard_environment(database), clear=True):
        with pytest.raises(LiveConformanceGateError, match=message):
            factory(gated)
    assert not http.calls


def test_builtin_factory_requires_external_vault_key_and_does_not_echo_identifiers(
    tmp_path: Path,
) -> None:
    database = tmp_path / "missing-vault-key.db"
    seed_standard_database(database)
    environment = standard_environment(database)
    del environment["FEED_PASSPORT_OAUTH_VAULT_KEY_B64"]
    http = NoNetworkHttpClient()
    factory = BuiltInLiveConformanceFactory(
        http_client_factory=lambda _timeout: http,
        clock=lambda: NOW,
        checkout_verifier=lambda _request: None,
    )
    gated = request(
        tmp_path,
        platform="youtube",
        connection_id="connection-youtube-dummy",
        action=ActionType.SUBSCRIBE_CREATOR,
    )

    with patch.dict(os.environ, environment, clear=True):
        with pytest.raises(LiveConformanceGateError) as raised:
            factory(gated)
    rendered = str(raised.value)
    assert "owner-dummy" not in rendered
    assert "connection-youtube-dummy" not in rendered
    assert "encrypted-dummy" not in rendered
    assert not http.calls
