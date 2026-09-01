from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from feed_passport.adapters.lab import LabAdapter
from feed_passport.agent import ConsentBroker, CuratorAgentService
from feed_passport.api.app import create_app
from feed_passport.api.auth import AuthenticationError, OIDCBearerTokenVerifier
from feed_passport.application import CuratorApplication
from feed_passport.infrastructure import SQLiteStore
from feed_passport.ports.identity import AuthenticatedPrincipal
from feed_passport.runtime import ServiceBundle


class StaticBearerVerifier:
    def verify(self, token: str, *, now: datetime) -> AuthenticatedPrincipal:
        subjects = {"token-a": "person-a", "token-b": "person-b", "token-c": "person-c"}
        try:
            subject = subjects[token]
        except KeyError as exc:
            raise RuntimeError("invalid test bearer token") from exc
        return AuthenticatedPrincipal(
            subject=subject,
            issuer="https://issuer.example.test",
            authenticated_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(minutes=10),
        )


class StaticJwksClient:
    def get_signing_key_from_jwt(self, _token: str) -> SimpleNamespace:
        return SimpleNamespace(key="test-public-key")


class OIDCBearerVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        self.verifier = OIDCBearerTokenVerifier(
            issuer="https://issuer.example.test",
            audience="public-spa-client",
            jwks_url="https://issuer.example.test/.well-known/jwks.json",
            _jwks_client=StaticJwksClient(),
        )

    def claims(self, **changes: object) -> dict[str, object]:
        return {
            "sub": "owner-a",
            "iss": "https://issuer.example.test",
            "client_id": "public-spa-client",
            "token_use": "access",
            "scope": "openid feed-passport/invoke",
            "iat": self.now.timestamp() - 60,
            "exp": self.now.timestamp() + 600,
            **changes,
        }

    def verify_claims(self, claims: dict[str, object]) -> AuthenticatedPrincipal:
        with patch("feed_passport.api.auth.jwt.decode", return_value=claims):
            return self.verifier.verify("opaque-test-token-value", now=self.now)

    def test_accepts_only_the_scoped_access_token_for_the_exact_client(self) -> None:
        principal = self.verify_claims(self.claims())

        self.assertEqual(principal.actor_id, "owner-a")
        self.assertEqual(principal.claims["token_use"], "access")

    def test_rejects_id_tokens_and_access_tokens_without_the_invoke_scope(self) -> None:
        with self.assertRaisesRegex(AuthenticationError, "access token"):
            self.verify_claims(self.claims(token_use="id", aud="public-spa-client"))
        with self.assertRaisesRegex(AuthenticationError, "required application scope"):
            self.verify_claims(self.claims(scope="openid"))

    def test_rejects_tokens_issued_for_another_client(self) -> None:
        with self.assertRaisesRegex(AuthenticationError, "different client"):
            self.verify_claims(self.claims(client_id="another-client"))


class AuthenticatedAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        store = SQLiteStore(Path(self.temp.name) / "authenticated-api.db")
        adapter = LabAdapter()
        application = CuratorApplication(store=store, adapters={adapter.platform: adapter})
        self.passport_a = application.create_passport(
            owner_id="person-a",
            name="A's useful internet",
            intent="Research without outrage.",
            topic_targets={"research": 1.0},
        )
        self.passport_b = application.create_passport(
            owner_id="person-b",
            name="B's useful internet",
            intent="Design without outrage.",
            topic_targets={"design": 1.0},
        )
        application.create_checkpoint(
            self.passport_a.id,
            actor_id="person-a",
            label="A private checkpoint",
        )
        broker = ConsentBroker(application, secret="authenticated-api-test-secret")
        self.bundle = ServiceBundle(
            store=store,
            application=application,
            broker=broker,
            agent_service=CuratorAgentService(application, broker),
        )
        app = create_app(
            self.bundle,
            auth_mode="oidc",
            bearer_verifier=StaticBearerVerifier(),
        )
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.bundle.close()
        self.temp.cleanup()

    @staticmethod
    def auth(subject: str) -> dict[str, str]:
        return {"Authorization": f"Bearer token-{subject[-1]}"}

    def test_health_is_public_but_every_api_route_requires_a_bearer_token(self) -> None:
        self.assertEqual(self.client.get("/health").status_code, 200)

        missing = self.client.get("/api/passports")

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(missing.json()["error"], "unauthenticated")
        self.assertEqual(missing.headers["www-authenticate"], "Bearer")

    def test_verified_subject_cannot_be_replaced_by_a_body_or_query_actor(self) -> None:
        spoofed_body = self.client.patch(
            f"/api/passports/{self.passport_a.id}",
            headers=self.auth("person-b"),
            json={"actor_id": "person-a", "changes": {"serendipity": 0.4}},
        )
        spoofed_query = self.client.get(
            "/api/agent/missions?actor_id=person-a",
            headers=self.auth("person-b"),
        )

        self.assertEqual(spoofed_body.status_code, 403)
        self.assertEqual(spoofed_query.status_code, 403)
        self.assertEqual(
            self.bundle.application.get_passport(self.passport_a.id).serendipity,
            0.2,
        )

    def test_owner_scoped_lists_and_direct_reads_hide_other_principals(self) -> None:
        list_response = self.client.get("/api/passports", headers=self.auth("person-b"))
        demo_response = self.client.get("/api/demo", headers=self.auth("person-b"))
        hidden_get = self.client.get(
            f"/api/passports/{self.passport_a.id}",
            headers=self.auth("person-b"),
        )
        hidden_export = self.client.get(
            f"/api/passports/{self.passport_a.id}/export",
            headers=self.auth("person-b"),
        )
        hidden_events = self.client.get(
            f"/api/passports/{self.passport_a.id}/events",
            headers=self.auth("person-b"),
        )

        self.assertEqual([item["id"] for item in list_response.json()], [self.passport_b.id])
        self.assertEqual([item["id"] for item in demo_response.json()["passports"]], [self.passport_b.id])
        self.assertEqual(demo_response.json()["checkpoints"], [])
        self.assertEqual(hidden_get.status_code, 404)
        self.assertEqual(hidden_export.status_code, 404)
        self.assertEqual(hidden_events.status_code, 404)

    def test_end_users_cannot_invoke_the_scheduler_processing_route(self) -> None:
        response = self.client.post(
            "/api/visas/process-due",
            headers=self.auth("person-a"),
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("scheduled work", response.json()["detail"])

    def test_authorization_header_is_allowed_by_cors_preflight(self) -> None:
        response = self.client.options(
            "/api/passports",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Authorization", response.headers["access-control-allow-headers"])

    def test_authenticated_first_run_creates_one_owner_bound_passport(self) -> None:
        first = self.client.post("/api/onboarding", headers=self.auth("person-c"), json={})
        second = self.client.post("/api/onboarding", headers=self.auth("person-c"), json={})
        snapshot = self.client.get("/api/demo", headers=self.auth("person-c"))

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["created"])
        self.assertFalse(second.json()["created"])
        self.assertEqual(first.json()["passport"]["owner_id"], "person-c")
        self.assertEqual(second.json()["passport"]["id"], first.json()["passport"]["id"])
        self.assertEqual(
            [passport["id"] for passport in snapshot.json()["passports"]],
            [first.json()["passport"]["id"]],
        )

    def test_credentialed_surfaces_fail_closed_without_oidc(self) -> None:
        self.bundle.connection_registry = object()  # type: ignore[assignment]
        try:
            with patch.dict(
                os.environ,
                {"FEED_PASSPORT_ALLOW_INSECURE_LOOPBACK_OAUTH": "0"},
                clear=False,
            ):
                with self.assertRaisesRegex(ValueError, "require FEED_PASSPORT_AUTH_MODE=oidc"):
                    create_app(self.bundle, auth_mode="demo")
        finally:
            self.bundle.connection_registry = None


if __name__ == "__main__":
    unittest.main()
