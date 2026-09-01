from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from urllib.parse import parse_qs, urlparse

import pytest
import httpx

from feed_passport.adapters.platforms.live import AtprotoSidecarClient
from feed_passport.application.oauth import (
    AtprotoOAuthConnectionService,
    OAuthConnectionService,
    OAuthFlowError,
    OAuthProviderCatalog,
)
from feed_passport.domain.connections import ConnectionStatus
from feed_passport.infrastructure.connection_registry import (
    EncryptedConnectionRegistry,
    OAuthTransactionError,
)
from feed_passport.infrastructure.crypto import AesGcmKeyring
from feed_passport.infrastructure.oauth_vault import LocalEncryptedOAuthVault
from feed_passport.infrastructure.sqlite_store import SQLiteStore
from feed_passport.ports.oauth import (
    OAuthClientAuthentication,
    OAuthProviderConfig,
)


NOW = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
ATPROTO_SECRET = "internal-sidecar-secret-with-at-least-32-characters"
ATPROTO_REF = "a" * 43
ATPROTO_DID = "did:plc:alice123"


class Response:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self.headers = MappingProxyType({"Content-Type": "application/json"})
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> object:
        return self._payload


class FakeHttpClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.refreshes = 0
        self.revocations = 0
        self.identity_status = 200
        self.revoke_error: Exception | None = None

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, object] | None = None,
        json: object = None,
        data: dict[str, object] | None = None,
    ) -> Response:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "params": dict(params or {}),
                "json": json,
                "data": dict(data or {}),
            }
        )
        if url == "https://provider.test/token":
            if data and data.get("grant_type") == "refresh_token":
                self.refreshes += 1
                return Response(
                    200,
                    {
                        "access_token": "refreshed-secret-access",
                        "expires_in": 3600,
                        "token_type": "Bearer",
                        "scope": "scope.read scope.write",
                    },
                )
            return Response(
                200,
                {
                    "access_token": "initial-secret-access",
                    "refresh_token": "initial-secret-refresh",
                    "expires_in": 180,
                    "token_type": "Bearer",
                    "scope": "scope.read scope.write",
                },
            )
        if url == "https://provider.test/me":
            if self.identity_status != 200:
                return Response(self.identity_status, {"error": "identity unavailable"})
            return Response(200, {"items": [{"id": "external-channel-7"}]})
        if url == "https://provider.test/revoke":
            if self.revoke_error is not None:
                raise self.revoke_error
            self.revocations += 1
            return Response(200, {})
        raise AssertionError(f"unexpected fake HTTP request: {method} {url}")


def provider() -> OAuthProviderConfig:
    return OAuthProviderConfig(
        platform="youtube",
        client_id="local-client-id",
        client_secret="local-client-secret",
        authorize_url="https://provider.test/authorize",
        token_url="https://provider.test/token",
        revoke_url="https://provider.test/revoke",
        identity_url="https://provider.test/me",
        identity_kind="youtube_channel",
        scopes=frozenset({"scope.read", "scope.write"}),
        allowed_redirect_uris=frozenset({"http://127.0.0.1:5173/oauth/callback"}),
        use_pkce=True,
        client_authentication=OAuthClientAuthentication.CLIENT_SECRET_POST,
    )


@pytest.fixture
def oauth_stack(tmp_path):
    store = SQLiteStore(tmp_path / "oauth.db")
    catalog = OAuthProviderCatalog({"youtube": provider()})
    http = FakeHttpClient()
    connection_ids = iter(("connection-test", "connection-test-reconnect"))
    credential_ids = iter(("credential-test", "credential-test-rotated"))
    states = iter(("s" * 48, "t" * 48))
    connections = EncryptedConnectionRegistry(
        store,
        AesGcmKeyring(
            active_key_id="local-v1",
            keys={"local-v1": b"c" * 32},
            index_key=b"i" * 32,
        ),
        id_factory=lambda: next(connection_ids),
        state_factory=lambda: next(states),
    )
    vault = LocalEncryptedOAuthVault(
        store,
        encryption_key=b"v" * 32,
        key_id="vault-v1",
        providers=catalog,
        http_client=http,
        id_factory=lambda: next(credential_ids),
        refresh_skew=timedelta(minutes=2),
    )
    service = OAuthConnectionService(
        connections=connections,
        credentials=vault,
        providers=catalog,
        http_client=http,
    )
    try:
        yield store, catalog, http, connections, vault, service
    finally:
        store.close()


def test_provider_config_never_represents_client_secret() -> None:
    value = provider()
    assert "local-client-secret" not in repr(value)
    assert "<redacted>" in repr(value)


def test_start_builds_owner_bound_pkce_url_without_secret(oauth_stack) -> None:
    store, _catalog, _http, _connections, _vault, service = oauth_stack
    result = service.start(
        owner_id="owner-a",
        platform="youtube",
        redirect_uri="http://127.0.0.1:5173/oauth/callback",
        now=NOW,
    )
    query = parse_qs(urlparse(result["authorization_url"]).query)
    assert query["state"] == ["s" * 48]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) == 43
    assert "local-client-secret" not in result["authorization_url"]
    with store.transaction() as connection:
        row = connection.execute("SELECT * FROM oauth_transactions").fetchone()
    assert row["state_hash"] != "s" * 48
    assert b"code_verifier" not in bytes(row["metadata_ciphertext"])


def test_redirect_outside_exact_allowlist_is_rejected(oauth_stack) -> None:
    *_rest, service = oauth_stack
    with pytest.raises(OAuthFlowError, match="not registered"):
        service.start(
            owner_id="owner-a",
            platform="youtube",
            redirect_uri="https://attacker.example/callback",
            now=NOW,
        )


def test_callback_encrypts_tokens_registers_connection_and_rejects_replay(oauth_stack) -> None:
    store, _catalog, http, connections, vault, service = oauth_stack
    service.start(
        owner_id="owner-a",
        platform="youtube",
        redirect_uri="http://127.0.0.1:5173/oauth/callback",
        now=NOW,
    )
    connected = service.callback(
        owner_id="owner-a",
        platform="youtube",
        state="s" * 48,
        code="one-time-code",
        now=NOW + timedelta(seconds=5),
    )
    assert connected.owner_id == "owner-a"
    assert connected.external_subject == "external-channel-7"
    assert connected.granted_scopes == frozenset({"scope.read", "scope.write"})
    assert connections.list_connections(owner_id="owner-a") == (connected,)
    with store.transaction() as connection:
        row = connection.execute("SELECT ciphertext FROM oauth_credentials").fetchone()
    ciphertext = bytes(row["ciphertext"])
    assert b"initial-secret-access" not in ciphertext
    assert b"initial-secret-refresh" not in ciphertext
    assert "initial-secret" not in repr(connected)
    lease = vault.lease(
        connected,
        required_scopes=frozenset({"scope.write"}),
        now=NOW + timedelta(seconds=70),
    )
    assert "refreshed-secret-access" not in repr(lease)
    assert http.refreshes == 1
    with pytest.raises(OAuthTransactionError, match="already been consumed"):
        service.callback(
            owner_id="owner-a",
            platform="youtube",
            state="s" * 48,
            code="replay-code",
            now=NOW + timedelta(seconds=10),
        )


def test_callback_cannot_be_claimed_by_another_owner(oauth_stack) -> None:
    *_rest, service = oauth_stack
    service.start(
        owner_id="owner-a",
        platform="youtube",
        redirect_uri="http://127.0.0.1:5173/oauth/callback",
        now=NOW,
    )
    with pytest.raises(OAuthTransactionError, match="does not belong"):
        service.callback(
            owner_id="owner-b",
            platform="youtube",
            state="s" * 48,
            code="stolen-code",
            now=NOW + timedelta(seconds=5),
        )


def test_generic_callback_rotates_active_connection_and_destroys_old_credential(
    oauth_stack,
) -> None:
    store, _catalog, http, connections, _vault, service = oauth_stack
    service.start(
        owner_id="owner-a",
        platform="youtube",
        redirect_uri="http://127.0.0.1:5173/oauth/callback",
        now=NOW,
    )
    original = service.callback(
        owner_id="owner-a",
        platform="youtube",
        state="s" * 48,
        code="first-code",
        now=NOW + timedelta(seconds=5),
    )
    service.start(
        owner_id="owner-a",
        platform="youtube",
        redirect_uri="http://127.0.0.1:5173/oauth/callback",
        now=NOW + timedelta(minutes=1),
    )

    rotated = service.callback(
        owner_id="owner-a",
        platform="youtube",
        state="t" * 48,
        code="second-code",
        now=NOW + timedelta(minutes=1, seconds=5),
    )

    assert rotated.id == original.id
    assert rotated.version == original.version + 1
    assert rotated.credential_ref != original.credential_ref
    assert rotated.status is ConnectionStatus.ACTIVE
    assert connections.list_connections(owner_id="owner-a") == (rotated,)
    with store.transaction() as database:
        refs = tuple(
            row[0]
            for row in database.execute(
                "SELECT credential_ref FROM oauth_credentials ORDER BY credential_ref"
            ).fetchall()
        )
    assert refs == (rotated.credential_ref,)
    assert http.revocations == 0


def test_generic_callback_reactivates_reauth_required_connection(oauth_stack) -> None:
    _store, _catalog, _http, connections, _vault, service = oauth_stack
    service.start(
        owner_id="owner-a",
        platform="youtube",
        redirect_uri="http://127.0.0.1:5173/oauth/callback",
        now=NOW,
    )
    original = service.callback(
        owner_id="owner-a",
        platform="youtube",
        state="s" * 48,
        code="first-code",
        now=NOW + timedelta(seconds=5),
    )
    stale = connections.update_connection(
        original.id,
        owner_id="owner-a",
        expected_version=original.version,
        now=NOW + timedelta(seconds=10),
        status=ConnectionStatus.REAUTH_REQUIRED,
    )
    service.start(
        owner_id="owner-a",
        platform="youtube",
        redirect_uri="http://127.0.0.1:5173/oauth/callback",
        now=NOW + timedelta(minutes=1),
    )

    rotated = service.callback(
        owner_id="owner-a",
        platform="youtube",
        state="t" * 48,
        code="second-code",
        now=NOW + timedelta(minutes=1, seconds=5),
    )

    assert rotated.id == original.id
    assert rotated.version == stale.version + 1
    assert rotated.status is ConnectionStatus.ACTIVE
    assert rotated.credential_ref != original.credential_ref


def test_generic_rotation_failure_discards_new_credential_and_preserves_old_binding(
    oauth_stack,
    monkeypatch,
) -> None:
    store, _catalog, http, connections, _vault, service = oauth_stack
    service.start(
        owner_id="owner-a",
        platform="youtube",
        redirect_uri="http://127.0.0.1:5173/oauth/callback",
        now=NOW,
    )
    original = service.callback(
        owner_id="owner-a",
        platform="youtube",
        state="s" * 48,
        code="first-code",
        now=NOW + timedelta(seconds=5),
    )
    service.start(
        owner_id="owner-a",
        platform="youtube",
        redirect_uri="http://127.0.0.1:5173/oauth/callback",
        now=NOW + timedelta(minutes=1),
    )

    def fail_rotation(*_args, **_kwargs):
        raise RuntimeError("simulated registry rotation failure")

    monkeypatch.setattr(connections, "update_connection", fail_rotation)
    with pytest.raises(RuntimeError, match="registry rotation failure"):
        service.callback(
            owner_id="owner-a",
            platform="youtube",
            state="t" * 48,
            code="second-code",
            now=NOW + timedelta(minutes=1, seconds=5),
        )

    assert connections.get_connection(original.id, owner_id="owner-a") == original
    with store.transaction() as database:
        refs = tuple(
            row[0]
            for row in database.execute("SELECT credential_ref FROM oauth_credentials").fetchall()
        )
    assert refs == (original.credential_ref,)
    assert http.revocations == 0


def test_callback_failure_revokes_the_unregistered_provider_grant(oauth_stack) -> None:
    store, _catalog, http, connections, _vault, service = oauth_stack
    http.identity_status = 503
    service.start(
        owner_id="owner-a",
        platform="youtube",
        redirect_uri="http://127.0.0.1:5173/oauth/callback",
        now=NOW,
    )

    with pytest.raises(OAuthFlowError, match="could not be confirmed"):
        service.callback(
            owner_id="owner-a",
            platform="youtube",
            state="s" * 48,
            code="one-time-code",
            now=NOW + timedelta(seconds=5),
        )

    assert http.revocations == 1
    assert connections.list_connections(owner_id="owner-a") == ()
    with store.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM oauth_credentials").fetchone()[0] == 0


def test_revoke_confirms_provider_then_destroys_local_credential(oauth_stack) -> None:
    _store, _catalog, http, connections, vault, service = oauth_stack
    service.start(
        owner_id="owner-a",
        platform="youtube",
        redirect_uri="http://127.0.0.1:5173/oauth/callback",
        now=NOW,
    )
    connected = service.callback(
        owner_id="owner-a",
        platform="youtube",
        state="s" * 48,
        code="one-time-code",
        now=NOW + timedelta(seconds=5),
    )
    revoked = service.revoke(
        connected.id,
        owner_id="owner-a",
        expected_version=connected.version,
        now=NOW + timedelta(minutes=1),
    )
    assert http.revocations == 1
    assert revoked.status is ConnectionStatus.REVOKED
    with pytest.raises(Exception, match="not active"):
        vault.lease(
            revoked,
            required_scopes=frozenset({"scope.read"}),
            now=NOW + timedelta(minutes=2),
        )


def test_generic_oauth_can_create_a_fresh_record_after_confirmed_revoke(oauth_stack) -> None:
    _store, _catalog, _http, connections, _vault, service = oauth_stack
    service.start(
        owner_id="owner-a",
        platform="youtube",
        redirect_uri="http://127.0.0.1:5173/oauth/callback",
        now=NOW,
    )
    original = service.callback(
        owner_id="owner-a",
        platform="youtube",
        state="s" * 48,
        code="first-code",
        now=NOW + timedelta(seconds=5),
    )
    revoked = service.revoke(
        original.id,
        owner_id="owner-a",
        expected_version=original.version,
        now=NOW + timedelta(minutes=1),
    )

    service.start(
        owner_id="owner-a",
        platform="youtube",
        redirect_uri="http://127.0.0.1:5173/oauth/callback",
        now=NOW + timedelta(minutes=2),
    )
    reconnected = service.callback(
        owner_id="owner-a",
        platform="youtube",
        state="t" * 48,
        code="second-code",
        now=NOW + timedelta(minutes=2, seconds=5),
    )

    assert revoked.status is ConnectionStatus.REVOKED
    assert reconnected.id == "connection-test-reconnect"
    assert reconnected.status is ConnectionStatus.ACTIVE
    assert reconnected.external_subject == original.external_subject
    assert connections.list_connections(owner_id="owner-a") == (revoked, reconnected)

def test_revoke_failure_leaves_a_non_executable_recovery_state(oauth_stack) -> None:
    store, _catalog, http, connections, _vault, service = oauth_stack
    service.start(
        owner_id="owner-a",
        platform="youtube",
        redirect_uri="http://127.0.0.1:5173/oauth/callback",
        now=NOW,
    )
    connected = service.callback(
        owner_id="owner-a",
        platform="youtube",
        state="s" * 48,
        code="one-time-code",
        now=NOW + timedelta(seconds=5),
    )
    http.revoke_error = httpx.ReadTimeout("provider did not confirm revocation")

    with pytest.raises(Exception, match="could not be confirmed"):
        service.revoke(
            connected.id,
            owner_id="owner-a",
            expected_version=connected.version,
            now=NOW + timedelta(minutes=1),
        )

    pending = connections.get_connection(connected.id, owner_id="owner-a")
    assert pending.status is ConnectionStatus.REVOKING
    with store.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM oauth_credentials").fetchone()[0] == 1


def test_revoke_recovers_after_receipt_commits_before_registry_finalization(
    oauth_stack,
    monkeypatch,
) -> None:
    store, _catalog, http, connections, _vault, service = oauth_stack
    service.start(
        owner_id="owner-a",
        platform="youtube",
        redirect_uri="http://127.0.0.1:5173/oauth/callback",
        now=NOW,
    )
    connected = service.callback(
        owner_id="owner-a",
        platform="youtube",
        state="s" * 48,
        code="one-time-code",
        now=NOW + timedelta(seconds=5),
    )
    finalize = connections.revoke_connection
    finalize_calls = 0

    def crash_after_vault(*args, **kwargs):
        nonlocal finalize_calls
        finalize_calls += 1
        raise RuntimeError("simulated crash after provider confirmation")

    monkeypatch.setattr(connections, "revoke_connection", crash_after_vault)
    with pytest.raises(RuntimeError, match="simulated crash"):
        service.revoke(
            connected.id,
            owner_id="owner-a",
            expected_version=connected.version,
            now=NOW + timedelta(minutes=1),
        )

    pending = connections.get_connection(connected.id, owner_id="owner-a")
    assert pending.status is ConnectionStatus.REVOKING
    assert http.revocations == 1
    with store.transaction() as database:
        assert database.execute("SELECT COUNT(*) FROM oauth_credentials").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM oauth_revocation_receipts").fetchone()[0] == 1

    monkeypatch.setattr(connections, "revoke_connection", finalize)
    recovered = service.revoke(
        connected.id,
        owner_id="owner-a",
        expected_version=pending.version,
        now=NOW + timedelta(minutes=2),
    )

    assert finalize_calls == 1
    assert http.revocations == 1
    assert recovered.status is ConnectionStatus.REVOKED


def test_reddit_oauth_registration_requires_a_dedicated_user_agent(monkeypatch) -> None:
    prefix = "FEED_PASSPORT_REDDIT_OAUTH_"
    monkeypatch.setenv("FEED_PASSPORT_OAUTH_REDIRECT_URIS", "http://127.0.0.1:5173/oauth/callback")
    monkeypatch.setenv(f"{prefix}CLIENT_ID", "reddit-dummy-client")
    monkeypatch.setenv(f"{prefix}CLIENT_SECRET", "reddit-dummy-secret")
    monkeypatch.delenv(f"{prefix}USER_AGENT", raising=False)

    with pytest.raises(ValueError, match="REDDIT_OAUTH_USER_AGENT"):
        OAuthProviderCatalog.from_env()

    expected = "windows:feed-passport:test (by /u/dummy-operator)"
    monkeypatch.setenv(f"{prefix}USER_AGENT", expected)
    assert OAuthProviderCatalog.from_env().get("reddit").user_agent == expected


class QueueSidecarHttp:
    def __init__(self, responses: list[Response | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def request(self, method: str, url: str, **kwargs: object) -> Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def atproto_stack(tmp_path):
    store = SQLiteStore(tmp_path / "atproto-oauth.db")
    connection_ids = iter(("connection-atproto-test", "connection-atproto-reconnect"))
    connections = EncryptedConnectionRegistry(
        store,
        AesGcmKeyring(
            active_key_id="local-v1",
            keys={"local-v1": b"c" * 32},
            index_key=b"i" * 32,
        ),
        id_factory=lambda: next(connection_ids),
    )
    http = QueueSidecarHttp([])
    sidecar = AtprotoSidecarClient(
        base_url="http://127.0.0.1:4310",
        internal_service_secret=ATPROTO_SECRET,
        http_client=http,
    )
    service = AtprotoOAuthConnectionService(
        connections=connections,
        sidecar=sidecar,
        callback_uri="http://127.0.0.1:5173/oauth/callback",
    )
    try:
        yield store, connections, http, service, tmp_path
    finally:
        store.close()


def test_atproto_start_is_token_free_owner_bound_and_public_status_is_safe(atproto_stack) -> None:
    _store, _connections, http, service, _path = atproto_stack
    http.responses.append(
        Response(
            200,
            {
                "platform": "bluesky",
                "authorization_url": "https://pds.example/oauth/authorize?request_uri=urn%3Arequest",
                "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
            },
        )
    )

    started = service.start(
        owner_id="owner-a",
        platform="bluesky",
        handle="@Alice.BSky.Social",
        now=NOW,
    )

    assert started["redirect_uri"] == "http://127.0.0.1:5173/oauth/callback"
    assert service.public_status() == {
        "platform": "bluesky",
        "configured": True,
        "scopes": ["atproto", "transition:generic"],
        "redirect_uris": ["http://127.0.0.1:5173/oauth/callback"],
        "pkce": True,
        "credential_boundary": "official_atproto_sidecar",
    }
    call = http.calls[0]
    assert call["url"] == "http://127.0.0.1:4310/v1/oauth/atproto/start"
    assert call["json"] == {"handle": "alice.bsky.social"}
    assert call["headers"]["X-Feed-Passport-Owner"] == "owner-a"
    rendered = json.dumps(started) + json.dumps(service.public_status())
    assert ATPROTO_SECRET not in rendered


def test_atproto_callback_registers_only_encrypted_opaque_reference(atproto_stack) -> None:
    store, connections, http, service, path = atproto_stack
    callback_query = "code=one-time-sensitive-code&state=" + "s" * 43
    http.responses.append(
        Response(
            200,
            {
                "platform": "bluesky",
                "connection_ref": ATPROTO_REF,
                "external_subject": ATPROTO_DID,
                "status": "active",
            },
        )
    )

    connected = service.callback(
        owner_id="owner-a",
        platform="bluesky",
        query=callback_query,
        now=NOW,
    )

    assert connected.external_subject == ATPROTO_DID
    assert connected.credential_ref == ATPROTO_REF
    assert connected.metadata["credential_boundary"] == "official_atproto_oauth_client"
    assert connections.list_connections(owner_id="owner-a") == (connected,)
    with store.transaction() as database:
        row = database.execute(
            "SELECT metadata_ciphertext, subject_fingerprint FROM external_connections"
        ).fetchone()
    assert ATPROTO_REF.encode() not in bytes(row["metadata_ciphertext"])
    assert ATPROTO_DID not in row["subject_fingerprint"]
    stored = b"".join(
        candidate.read_bytes()
        for candidate in path.iterdir()
        if candidate.is_file() and candidate.name.startswith("atproto-oauth.db")
    )
    assert ATPROTO_REF.encode() not in stored
    assert ATPROTO_DID.encode() not in stored
    assert b"one-time-sensitive-code" not in stored
    assert ATPROTO_SECRET.encode() not in stored


def test_atproto_callback_rotates_stale_active_sidecar_reference(atproto_stack) -> None:
    _store, connections, http, service, _path = atproto_stack
    replacement_ref = "b" * 43
    http.responses.extend(
        [
            Response(
                200,
                {
                    "platform": "bluesky",
                    "connection_ref": ATPROTO_REF,
                    "external_subject": ATPROTO_DID,
                    "status": "active",
                },
            ),
            Response(
                200,
                {
                    "platform": "bluesky",
                    "connection_ref": replacement_ref,
                    "external_subject": ATPROTO_DID,
                    "status": "active",
                },
            ),
            Response(
                200,
                {
                    "platform": "bluesky",
                    "connection_ref": replacement_ref,
                    "external_subject": ATPROTO_DID,
                    "status": "active",
                },
            ),
        ]
    )
    original = service.callback(
        owner_id="owner-a",
        platform="bluesky",
        query="code=first&state=" + "s" * 43,
        now=NOW,
    )
    rotated = service.callback(
        owner_id="owner-a",
        platform="bluesky",
        query="code=second&state=" + "t" * 43,
        now=NOW + timedelta(minutes=1),
    )
    repeated = service.callback(
        owner_id="owner-a",
        platform="bluesky",
        query="code=third&state=" + "u" * 43,
        now=NOW + timedelta(minutes=2),
    )

    assert rotated.id == original.id
    assert rotated.version == original.version + 1
    assert rotated.credential_ref == replacement_ref
    assert repeated == rotated
    assert connections.list_connections(owner_id="owner-a") == (rotated,)
    assert len(http.calls) == 3


def test_atproto_callback_unknown_outcome_is_sanitized_and_not_registered(atproto_stack) -> None:
    _store, connections, http, service, _path = atproto_stack
    http.responses.append(
        Response(503, {"error": "oauth_callback_failed", "detail": "raw provider secret"})
    )

    with pytest.raises(OAuthFlowError) as caught:
        service.callback(
            owner_id="owner-a",
            platform="bluesky",
            query="code=one-time-code&state=" + "s" * 43,
            now=NOW,
        )

    assert caught.value.code == "atproto_oauth_outcome_unknown"
    assert "raw provider secret" not in str(caught.value)
    assert connections.list_connections(owner_id="owner-a") == ()


def test_atproto_revoke_refuses_to_treat_sidecar_absence_as_terminal_proof(
    atproto_stack,
) -> None:
    _store, connections, http, service, _path = atproto_stack
    http.responses.append(
        Response(
            200,
            {
                "platform": "bluesky",
                "connection_ref": ATPROTO_REF,
                "external_subject": ATPROTO_DID,
                "status": "active",
            },
        )
    )
    connected = service.callback(
        owner_id="owner-a",
        platform="bluesky",
        query="code=one-time-code&state=" + "s" * 43,
        now=NOW,
    )
    http.responses.append(
        Response(
            404,
            {
                "error": "atproto_connection_not_found",
                "detail": "must not be exposed",
            },
        )
    )

    with pytest.raises(OAuthFlowError) as caught:
        service.revoke(
            connected.id,
            owner_id="owner-a",
            expected_version=connected.version,
            now=NOW + timedelta(minutes=1),
        )

    assert caught.value.code == "atproto_connection_unavailable"
    pending = connections.get_connection(connected.id, owner_id="owner-a")
    assert pending.status is ConnectionStatus.REVOKING
    assert http.calls[-1]["json"] == {"connection_ref": ATPROTO_REF}
    assert "must not be exposed" not in str(caught.value)


def test_atproto_unknown_revoke_stays_non_executable_then_recovers_from_terminal_proof(
    atproto_stack,
) -> None:
    _store, connections, http, service, _path = atproto_stack
    http.responses.append(
        Response(
            200,
            {
                "platform": "bluesky",
                "connection_ref": ATPROTO_REF,
                "external_subject": ATPROTO_DID,
                "status": "active",
            },
        )
    )
    connected = service.callback(
        owner_id="owner-a",
        platform="bluesky",
        query="code=one-time-code&state=" + "s" * 43,
        now=NOW,
    )
    http.responses.append(
        Response(503, {"error": "session_revoke_failed", "detail": "private detail"})
    )

    with pytest.raises(OAuthFlowError) as caught:
        service.revoke(
            connected.id,
            owner_id="owner-a",
            expected_version=connected.version,
            now=NOW + timedelta(minutes=1),
        )

    assert caught.value.code == "atproto_oauth_outcome_unknown"
    pending = connections.get_connection(connected.id, owner_id="owner-a")
    assert pending.status is ConnectionStatus.REVOKING
    assert pending.version == connected.version + 1
    assert connections.list_runtime_connections(platform="bluesky") == ()

    http.responses.append(
        Response(
            200,
            {
                "platform": "bluesky",
                "connection_ref": ATPROTO_REF,
                "status": "revoked",
                "revocation_proof": "sidecar_terminal",
            },
        )
    )
    recovered = service.revoke(
        connected.id,
        owner_id="owner-a",
        expected_version=pending.version,
        now=NOW + timedelta(minutes=2),
    )
    assert recovered.status is ConnectionStatus.REVOKED
    assert recovered.version == pending.version + 1


def test_atproto_can_create_a_fresh_record_after_confirmed_revoke(atproto_stack) -> None:
    _store, connections, http, service, _path = atproto_stack
    http.responses.extend(
        [
            Response(
                200,
                {
                    "platform": "bluesky",
                    "connection_ref": ATPROTO_REF,
                    "external_subject": ATPROTO_DID,
                    "status": "active",
                },
            ),
            Response(
                200,
                {
                    "platform": "bluesky",
                    "connection_ref": ATPROTO_REF,
                    "status": "revoked",
                    "revocation_proof": "provider_confirmed",
                },
            ),
            Response(
                200,
                {
                    "platform": "bluesky",
                    "connection_ref": "b" * 43,
                    "external_subject": ATPROTO_DID,
                    "status": "active",
                },
            ),
        ]
    )
    original = service.callback(
        owner_id="owner-a",
        platform="bluesky",
        query="code=first&state=" + "s" * 43,
        now=NOW,
    )
    revoked = service.revoke(
        original.id,
        owner_id="owner-a",
        expected_version=original.version,
        now=NOW + timedelta(minutes=1),
    )
    reconnected = service.callback(
        owner_id="owner-a",
        platform="bluesky",
        query="code=second&state=" + "t" * 43,
        now=NOW + timedelta(minutes=2),
    )

    assert revoked.status is ConnectionStatus.REVOKED
    assert reconnected.id == "connection-atproto-reconnect"
    assert reconnected.status is ConnectionStatus.ACTIVE
    assert reconnected.external_subject == original.external_subject
    assert connections.list_connections(owner_id="owner-a") == (revoked, reconnected)

    http.responses.append(
        Response(
            200,
            {
                "platform": "bluesky",
                "connection_ref": "b" * 43,
                "external_subject": ATPROTO_DID,
                "status": "active",
            },
        )
    )
    repeated = service.callback(
        owner_id="owner-a",
        platform="bluesky",
        query="code=third&state=" + "u" * 43,
        now=NOW + timedelta(minutes=3),
    )
    assert repeated == reconnected
    assert connections.list_connections(owner_id="owner-a") == (revoked, reconnected)


def test_atproto_callback_conflicts_while_prior_connection_is_revoking(atproto_stack) -> None:
    _store, connections, http, service, _path = atproto_stack
    http.responses.append(
        Response(
            200,
            {
                "platform": "bluesky",
                "connection_ref": ATPROTO_REF,
                "external_subject": ATPROTO_DID,
                "status": "active",
            },
        )
    )
    original = service.callback(
        owner_id="owner-a",
        platform="bluesky",
        query="code=first&state=" + "s" * 43,
        now=NOW,
    )
    pending = connections.update_connection(
        original.id,
        owner_id="owner-a",
        expected_version=original.version,
        status=ConnectionStatus.REVOKING,
        now=NOW + timedelta(minutes=1),
    )
    http.responses.extend(
        [
            Response(
                200,
                {
                    "platform": "bluesky",
                    "connection_ref": ATPROTO_REF,
                    "external_subject": ATPROTO_DID,
                    "status": "active",
                },
            ),
            Response(
                200,
                {
                    "platform": "bluesky",
                    "connection_ref": ATPROTO_REF,
                    "status": "revoked",
                    "revocation_proof": "provider_confirmed",
                },
            ),
        ]
    )

    with pytest.raises(OAuthFlowError) as caught:
        service.callback(
            owner_id="owner-a",
            platform="bluesky",
            query="code=second&state=" + "t" * 43,
            now=NOW + timedelta(minutes=2),
        )

    assert caught.value.code == "atproto_connection_conflict"
    assert connections.get_connection(original.id, owner_id="owner-a") == pending
    assert connections.list_runtime_connections(platform="bluesky") == ()
