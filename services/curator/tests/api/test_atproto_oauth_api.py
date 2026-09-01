from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import MappingProxyType

import pytest
from fastapi.testclient import TestClient

from feed_passport.adapters.lab import LabAdapter
from feed_passport.adapters.platforms.live import AtprotoSidecarClient, AtprotoSidecarLiveAdapter
from feed_passport.agent import ConsentBroker, CuratorAgentService
from feed_passport.api.app import create_app
from feed_passport.application import AtprotoOAuthConnectionService, CuratorApplication
from feed_passport.application.oauth import OAuthProviderCatalog
from feed_passport.infrastructure.connection_registry import EncryptedConnectionRegistry
from feed_passport.infrastructure.crypto import AesGcmKeyring
from feed_passport.infrastructure.sqlite_store import SQLiteStore
from feed_passport.ports.identity import AuthenticatedPrincipal
from feed_passport.ports.live_platform import LiveAuthenticationError
from feed_passport.runtime import ServiceBundle


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
SECRET = "internal-sidecar-secret-with-at-least-32-characters"
CONNECTION_REF = "c" * 43
DID = "did:plc:alice123"
AUTH = {"Authorization": "Bearer owner-a-token"}


class Response:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self.headers = MappingProxyType({"Content-Type": "application/json"})
        self.text = json.dumps(payload)
        self._payload = payload

    def json(self) -> object:
        return self._payload


class QueueHttp:
    def __init__(self) -> None:
        self.responses: list[Response] = []
        self.calls: list[dict[str, object]] = []

    def request(self, method: str, url: str, **kwargs: object) -> Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected sidecar call: {method} {url}")
        return self.responses.pop(0)


class StaticBearerVerifier:
    def verify(self, token: str, *, now: datetime) -> AuthenticatedPrincipal:
        if token != "owner-a-token":
            raise RuntimeError("invalid test token")
        return AuthenticatedPrincipal(
            subject="owner-a",
            issuer="https://issuer.example.test",
            authenticated_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(minutes=10),
        )


def build_stack(tmp_path):
    store = SQLiteStore(tmp_path / "atproto-api.db")
    connections = EncryptedConnectionRegistry(
        store,
        AesGcmKeyring(
            active_key_id="local-v1",
            keys={"local-v1": b"c" * 32},
            index_key=b"i" * 32,
        ),
        id_factory=lambda: "connection-atproto-api",
    )
    http = QueueHttp()
    sidecar = AtprotoSidecarClient(
        base_url="http://127.0.0.1:4310",
        internal_service_secret=SECRET,
        http_client=http,
    )
    oauth = AtprotoOAuthConnectionService(
        connections=connections,
        sidecar=sidecar,
        callback_uri="http://127.0.0.1:5173/oauth/callback",
    )
    lab = LabAdapter()
    bluesky = AtprotoSidecarLiveAdapter(connections={}, sidecar_client=sidecar)
    application = CuratorApplication(
        store=store,
        adapters={lab.platform: lab, bluesky.platform: bluesky},
        connections=connections,
        action_journal=store,
    )
    broker = ConsentBroker(application, secret="atproto-api-consent-secret")
    bundle = ServiceBundle(
        store=store,
        application=application,
        broker=broker,
        agent_service=CuratorAgentService(application, broker),
        oauth_providers=OAuthProviderCatalog({}),
        connection_registry=connections,
        atproto_oauth_service=oauth,
        atproto_sidecar_client=sidecar,
    )
    return bundle, http


def test_atproto_api_start_callback_list_and_revoke_never_expose_sidecar_secrets(tmp_path) -> None:
    bundle, http = build_stack(tmp_path)
    app = create_app(
        bundle,
        auth_mode="oidc",
        bearer_verifier=StaticBearerVerifier(),
    )
    with TestClient(app) as client:
        provider_response = client.get("/api/oauth/providers", headers=AUTH)
        health = client.get("/health")
        http.responses.append(
            Response(
                200,
                {
                    "platform": "bluesky",
                    "authorization_url": "https://pds.example/oauth/authorize?request_uri=urn%3Arequest",
                    "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
                },
            )
        )
        started = client.post(
            "/api/connections/bluesky/oauth/start",
            headers=AUTH,
            json={"actor_id": "owner-a", "handle": "@Alice.BSky.Social"},
        )
        http.responses.append(
            Response(
                200,
                {
                    "platform": "bluesky",
                    "connection_ref": CONNECTION_REF,
                    "external_subject": DID,
                    "status": "active",
                },
            )
        )
        connected = client.post(
            "/api/connections/bluesky/oauth/callback",
            headers=AUTH,
            json={
                "actor_id": "owner-a",
                "query": "code=private-one-time-code&state=" + "s" * 43,
            },
        )
        listed = client.get("/api/connections?actor_id=owner-a", headers=AUTH)
        version = connected.json()["version"]
        http.responses.append(
            Response(
                200,
                {
                    "platform": "bluesky",
                    "connection_ref": CONNECTION_REF,
                    "status": "revoked",
                    "revocation_proof": "provider_confirmed",
                },
            )
        )
        revoked = client.post(
            "/api/connections/connection-atproto-api/revoke",
            headers=AUTH,
            json={"actor_id": "owner-a", "expected_version": version},
        )

    try:
        assert health.json()["connections"] == "configured"
        assert provider_response.json() == [
            {
                "platform": "bluesky",
                "configured": True,
                "scopes": ["atproto", "transition:generic"],
                "redirect_uris": ["http://127.0.0.1:5173/oauth/callback"],
                "pkce": True,
                "credential_boundary": "official_atproto_sidecar",
            }
        ]
        assert started.status_code == 200
        assert connected.status_code == 201
        assert listed.status_code == 200
        assert revoked.status_code == 200
        assert connected.json()["external_subject"] == DID
        assert revoked.json()["status"] == "revoked"
        assert listed.json() == [connected.json()]
        serialized = "\n".join(
            json.dumps(response.json())
            for response in (provider_response, health, started, connected, listed, revoked)
        )
        for private_value in (
            SECRET,
            CONNECTION_REF,
            "private-one-time-code",
            "X-Feed-Passport-Owner",
            "http://127.0.0.1:4310",
        ):
            assert private_value not in serialized
        assert http.calls[0]["headers"]["X-Feed-Passport-Owner"] == "owner-a"
        assert http.calls[1]["headers"]["X-Feed-Passport-Owner"] == "owner-a"
        assert http.calls[2]["headers"]["X-Feed-Passport-Owner"] == "owner-a"
    finally:
        bundle.close()


def test_atproto_api_rejects_standard_oauth_shapes_and_unknown_callback_outcomes(tmp_path) -> None:
    bundle, http = build_stack(tmp_path)
    app = create_app(
        bundle,
        auth_mode="oidc",
        bearer_verifier=StaticBearerVerifier(),
    )
    with TestClient(app) as client:
        wrong_start = client.post(
            "/api/connections/bluesky/oauth/start",
            headers=AUTH,
            json={
                "actor_id": "owner-a",
                "redirect_uri": "http://127.0.0.1:5173/oauth/callback",
            },
        )
        wrong_callback = client.post(
            "/api/connections/bluesky/oauth/callback",
            headers=AUTH,
            json={"actor_id": "owner-a", "state": "s" * 43, "code": "code"},
        )
        http.responses.append(
            Response(503, {"error": "oauth_callback_failed", "detail": "private detail"})
        )
        unknown = client.post(
            "/api/connections/bluesky/oauth/callback",
            headers=AUTH,
            json={"actor_id": "owner-a", "query": "code=code&state=" + "s" * 43},
        )
        http.responses.append(
            Response(409, {"error": "oauth_callback_rejected", "detail": "provider secret"})
        )
        rejected = client.post(
            "/api/connections/bluesky/oauth/callback",
            headers=AUTH,
            json={"actor_id": "owner-a", "query": "code=code&state=" + "t" * 43},
        )
        http.responses.append(
            Response(409, {"error": "oauth_state_expired", "detail": "state detail"})
        )
        expired = client.post(
            "/api/connections/bluesky/oauth/callback",
            headers=AUTH,
            json={"actor_id": "owner-a", "query": "code=code&state=" + "u" * 43},
        )

    try:
        assert wrong_start.status_code == 400
        assert wrong_start.json()["error"] == "atproto_handle_required"
        assert wrong_callback.status_code == 400
        assert wrong_callback.json()["error"] == "atproto_callback_required"
        assert unknown.status_code == 409
        assert unknown.json()["error"] == "atproto_oauth_outcome_unknown"
        assert "private detail" not in unknown.text
        for response in (rejected, expired):
            assert response.status_code == 409
            assert response.json()["error"] == "atproto_oauth_callback_rejected"
        assert "provider secret" not in rejected.text
        assert "state detail" not in expired.text
    finally:
        bundle.close()


def test_atproto_api_unbinds_before_an_unknown_remote_revoke(tmp_path) -> None:
    bundle, http = build_stack(tmp_path)
    app = create_app(
        bundle,
        auth_mode="oidc",
        bearer_verifier=StaticBearerVerifier(),
    )
    http.responses.append(
        Response(
            200,
            {
                "platform": "bluesky",
                "connection_ref": CONNECTION_REF,
                "external_subject": DID,
                "status": "active",
            },
        )
    )
    with TestClient(app) as client:
        connected = client.post(
            "/api/connections/bluesky/oauth/callback",
            headers=AUTH,
            json={"actor_id": "owner-a", "query": "code=code&state=" + "s" * 43},
        ).json()
        http.responses.append(
            Response(503, {"error": "session_revoke_failed", "detail": "private detail"})
        )
        unknown = client.post(
            f"/api/connections/{connected['id']}/revoke",
            headers=AUTH,
            json={"actor_id": "owner-a", "expected_version": connected["version"]},
        )
        pending = client.get("/api/connections?actor_id=owner-a", headers=AUTH).json()[0]

    try:
        assert unknown.status_code == 409
        assert unknown.json()["error"] == "atproto_oauth_outcome_unknown"
        assert pending["status"] == "revoking"
        adapter = bundle.application.adapters["bluesky"]
        with pytest.raises(LiveAuthenticationError):
            adapter._connection(connected["id"])
    finally:
        bundle.close()
