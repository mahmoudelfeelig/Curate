from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from feed_passport.adapters.lab import LabAdapter
from feed_passport.agent import ConsentBroker, CuratorAgentService
from feed_passport.api.app import create_app
from feed_passport.application import CuratorApplication
from feed_passport.infrastructure import SQLiteStore
from feed_passport.runtime import ServiceBundle


class ContinuousCompanionAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        store = SQLiteStore(Path(self.temp.name) / "companion-api.db")
        adapter = LabAdapter()
        application = CuratorApplication(store=store, adapters={adapter.platform: adapter})
        self.first = application.create_passport(
            owner_id="person-a",
            name="Research",
            intent="Research and design.",
            topic_targets={"research": 0.7, "design": 0.3},
        )
        self.second = application.create_passport(
            owner_id="person-b",
            name="Music",
            intent="Music and design.",
            topic_targets={"music": 0.8, "design": 0.2},
        )
        broker = ConsentBroker(application, secret="continuous-api-secret")
        self.bundle = ServiceBundle(
            store=store,
            application=application,
            broker=broker,
            agent_service=CuratorAgentService(application, broker),
        )
        self.client_context = TestClient(create_app(self.bundle))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.bundle.close()
        self.temp.cleanup()

    def create_slice(self, *, passport_id: str, actor_id: str, topics: list[str]) -> dict:
        response = self.client.post(
            "/api/shares",
            json={
                "actor_id": actor_id,
                "passport_id": passport_id,
                "topic_names": topics,
                "creator_ids": [],
                "include_serendipity": False,
                "include_formats": False,
                "include_exclusions": False,
                "expires_at": "2030-08-31T12:00:00Z",
                "scope": "continuous",
                "refresh_on_revision": True,
                "target_passport_ids": [passport_id],
                "pair_id": "pair-person-a-person-b",
                "counterparty_owner_id": "person-b" if actor_id == "person-a" else "person-a",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_continuous_sync_route_recomputes_effective_passports_and_revokes(self) -> None:
        first_slice = self.create_slice(
            passport_id=self.first.id,
            actor_id="person-a",
            topics=["research", "design"],
        )
        second_slice = self.create_slice(
            passport_id=self.second.id,
            actor_id="person-b",
            topics=["music", "design"],
        )
        created = self.client.post(
            "/api/companions",
            json={
                "actor_id": "person-a",
                "name": "Live lane",
                "slice_ids": [first_slice["id"], second_slice["id"]],
                "weights": {"person-a": 1.0, "person-b": 1.0},
                "strategy": "weighted",
                "expires_at": "2030-08-31T12:00:00Z",
                "scope": "continuous",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["sync_revision"], 1)

        first_view = self.client.get(f"/api/passports/{self.first.id}")
        self.assertEqual(first_view.status_code, 200)
        self.assertIn("music", first_view.json()["effective"]["topic_targets"])
        self.assertNotIn("music", first_view.json()["base"]["topic_targets"])

        revised = self.client.patch(
            f"/api/passports/{self.second.id}",
            json={"actor_id": "person-b", "changes": {"topic_targets": {"music": 0.2, "design": 0.8}}},
        )
        self.assertEqual(revised.status_code, 200)
        companions = self.client.get("/api/companions")
        self.assertEqual(companions.json()[0]["sync_revision"], 2)

        revoked = self.client.post(
            f"/api/shares/{second_slice['id']}/revoke",
            json={"actor_id": "person-b"},
        )
        self.assertEqual(revoked.status_code, 200)
        after_revoke = self.client.get(f"/api/passports/{self.first.id}")
        self.assertNotIn("music", after_revoke.json()["effective"]["topic_targets"])

    def test_strict_continuous_consent_validation_fails_closed(self) -> None:
        missing_refresh = self.client.post(
            "/api/shares",
            json={
                "actor_id": "person-a",
                "passport_id": self.first.id,
                "topic_names": ["design"],
                "expires_at": "2030-08-31T12:00:00Z",
                "scope": "continuous",
                "target_passport_ids": [self.first.id],
            },
        )
        self.assertEqual(missing_refresh.status_code, 422)

        unknown_target = self.client.post(
            "/api/shares",
            json={
                "actor_id": "person-a",
                "passport_id": self.first.id,
                "topic_names": ["design"],
                "expires_at": "2030-08-31T12:00:00Z",
                "scope": "continuous",
                "refresh_on_revision": True,
                "target_passport_ids": ["passport-missing"],
                "pair_id": "pair-person-a-person-b",
                "counterparty_owner_id": "person-b",
            },
        )
        self.assertEqual(unknown_target.status_code, 404)

        impersonated = self.client.post(
            "/api/shares",
            json={
                "actor_id": "person-b",
                "passport_id": self.first.id,
                "topic_names": ["design"],
                "expires_at": "2030-08-31T12:00:00Z",
                "scope": "continuous",
                "refresh_on_revision": True,
                "target_passport_ids": [self.first.id],
                "pair_id": "pair-person-a-person-b",
                "counterparty_owner_id": "person-a",
            },
        )
        self.assertEqual(impersonated.status_code, 403)

        extra_field = self.client.post(
            "/api/companions",
            json={
                "actor_id": "person-a",
                "name": "Rejected",
                "slice_ids": ["slice-a", "slice-b"],
                "weights": {"person-a": 1.0},
                "strategy": "weighted",
                "expires_at": "2030-08-31T12:00:00Z",
                "scope": "continuous",
                "approve_everyone": True,
            },
        )
        self.assertEqual(extra_field.status_code, 422)


if __name__ == "__main__":
    unittest.main()
