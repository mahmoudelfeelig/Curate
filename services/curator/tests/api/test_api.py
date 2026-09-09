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


class APITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        store = SQLiteStore(Path(self.temp.name) / "api.db")
        adapter = LabAdapter()
        application = CuratorApplication(store=store, adapters={adapter.platform: adapter})
        passport = application.create_passport(
            owner_id="person-a",
            name="Useful internet",
            intent="Research, design, and local culture without ragebait.",
            topic_targets={"research": 0.5, "design": 0.3, "local": 0.2},
            creator_preferences={"paper-lab": 1.0, "rage-farm": -1.0},
            hard_exclusions=frozenset({"ragebait"}),
            serendipity=0.2,
            max_source_share=0.4,
        )
        broker = ConsentBroker(application, secret="api-test-secret")
        self.bundle = ServiceBundle(
            store=store,
            application=application,
            broker=broker,
            agent_service=CuratorAgentService(application, broker),
        )
        self.passport_id = passport.id
        self.client_context = TestClient(
            create_app(self.bundle), client=("127.0.0.1", 45123)
        )
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.bundle.close()
        self.temp.cleanup()

    def test_default_local_origins_include_loopback_preview(self) -> None:
        response = self.client.options(
            "/api/demo",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://127.0.0.1:5173")

    def test_complete_migration_and_rollback_http_flow(self) -> None:
        demo = self.client.get("/api/demo")
        self.assertEqual(demo.status_code, 200)
        self.assertEqual(len(demo.json()["passports"]), 1)

        preview = self.client.post(
            "/api/migrations/preview",
            json={
                "actor_id": "person-a",
                "passport_id": self.passport_id,
                "platform": "feed_passport_lab",
                "destination_account_id": "destination-new",
            },
        )
        self.assertEqual(preview.status_code, 201)
        migration = preview.json()
        self.assertEqual(migration["status"], "awaiting_approval")

        approval = self.client.post(
            f"/api/migrations/{migration['id']}/approval",
            json={"actor_id": "person-a", "max_total_actions": 3},
        )
        self.assertEqual(approval.status_code, 200)
        executed = self.client.post(
            f"/api/migrations/{migration['id']}/execute",
            json={"actor_id": "person-a", "approval_token": approval.json()["token"]},
        )
        self.assertEqual(executed.status_code, 200)
        receipt_id = executed.json()["receipt_id"]

        rollback_approval = self.client.post(
            f"/api/receipts/{receipt_id}/rollback-approval",
            json={"actor_id": "person-a", "platform": "feed_passport_lab"},
        )
        self.assertEqual(rollback_approval.status_code, 200)
        rollback = self.client.post(
            f"/api/receipts/{receipt_id}/rollback",
            json={
                "actor_id": "person-a",
                "platform": "feed_passport_lab",
                "approval_token": rollback_approval.json()["token"],
            },
        )
        self.assertEqual(rollback.status_code, 200)
        self.assertEqual(rollback.json()["status"], "rolled_back")

    def test_agent_preview_requires_confirmation_and_llm_route_is_off_by_default(self) -> None:
        reply = self.client.post(
            "/api/agent/command",
            json={
                "command": "preview_migration",
                "actor_id": "person-a",
                "arguments": {
                    "passport_id": self.passport_id,
                    "platform": "feed_passport_lab",
                    "destination_account_id": "destination-new",
                },
            },
        )
        self.assertEqual(reply.status_code, 200)
        self.assertTrue(reply.json()["requires_confirmation"])
        self.assertEqual(reply.json()["data"]["status"], "awaiting_approval")

        disabled = self.client.post(
            "/api/agent/strands",
            json={"actor_id": "person-a", "message": "Inspect my Passport"},
        )
        self.assertEqual(disabled.status_code, 503)

        model_status = self.client.get("/api/agent/model/status")
        self.assertEqual(model_status.status_code, 200)
        self.assertFalse(model_status.json()["configured"])
        self.assertFalse(model_status.json()["online"])
        self.assertFalse(model_status.json()["external_model_calls"])
        self.assertFalse(model_status.json()["paid_model_calls"])

        model_preview = self.client.post(
            "/api/agent/missions/model-preview",
            json={
                "actor_id": "person-a",
                "passport_id": self.passport_id,
                "platform": "twin:youtube",
                "account_id": "destination-new",
                "goal": "Use a real local model, never a fixture pretending to be one.",
            },
        )
        self.assertEqual(model_preview.status_code, 503)
        self.assertIn("no external or paid provider fallback", model_preview.json()["detail"])

    def test_seeded_lab_account_can_be_captured_as_a_new_passport(self) -> None:
        captured = self.client.post(
            "/api/passports/capture",
            json={
                "platform": "feed_passport_lab",
                "account_id": "source-main",
                "owner_id": "person-a",
                "name": "Captured Lab source",
                "intent": "Turn an authorized source observation into a portable policy.",
            },
        )
        self.assertEqual(captured.status_code, 201)
        value = captured.json()
        self.assertNotEqual(value["id"], self.passport_id)
        self.assertEqual(value["owner_id"], "person-a")
        self.assertIn("research", value["topic_targets"])
        self.assertIn("paper-lab", value["creator_preferences"])

    def test_owner_boundary_and_request_validation_fail_closed(self) -> None:
        forbidden = self.client.patch(
            f"/api/passports/{self.passport_id}",
            json={"actor_id": "person-b", "changes": {"serendipity": 0.5}},
        )
        self.assertEqual(forbidden.status_code, 403)
        invalid = self.client.post(
            "/api/migrations/preview",
            json={"actor_id": "person-a", "passport_id": self.passport_id},
        )
        self.assertEqual(invalid.status_code, 422)
        drift = self.client.post(
            "/api/drift",
            json={
                "actor_id": "person-b",
                "passport_id": self.passport_id,
                "platform": "feed_passport_lab",
                "account_id": "destination-new",
            },
        )
        self.assertEqual(drift.status_code, 403)

    def test_portability_import_visa_revoke_and_creator_confirmation_routes(self) -> None:
        exported = self.client.get(f"/api/passports/{self.passport_id}/export")
        self.assertEqual(exported.status_code, 200)
        imported = self.client.post(
            "/api/passports/import",
            json={
                "actor_id": "person-a",
                "format": exported.json()["format"],
                "passport": exported.json()["passport"],
            },
        )
        self.assertEqual(imported.status_code, 201)
        self.assertNotEqual(imported.json()["passport"]["id"], self.passport_id)

        unsafe = dict(exported.json()["passport"])
        unsafe["raw_history"] = ["private-event"]
        rejected = self.client.post(
            "/api/passports/import",
            json={"actor_id": "person-a", "format": "feed-passport/v1", "passport": unsafe},
        )
        self.assertEqual(rejected.status_code, 422)

        visa = self.client.post(
            "/api/visas",
            json={
                "actor_id": "person-a",
                "passport_id": self.passport_id,
                "name": "Research afternoon",
                "topic_adjustments": {"research": 0.2},
                "add_exclusions": ["short_form"],
                "starts_at": "2026-08-29T16:00:00Z",
                "expires_at": "2030-08-29T18:00:00Z",
                "mode": "isolated",
                "serendipity": 0.1,
            },
        )
        self.assertEqual(visa.status_code, 201)
        revoked = self.client.post(
            f"/api/visas/{visa.json()['id']}/revoke",
            json={"actor_id": "person-a"},
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(revoked.json()["status"], "revoked")

        continuity = self.client.post(
            "/api/creator-continuity",
            json={
                "actor_id": "person-a",
                "passport_id": self.passport_id,
                "creator_id": "creator-studio-a",
                "destination_platform": "youtube",
            },
        )
        self.assertEqual(continuity.status_code, 201)
        self.assertEqual(continuity.json()["destination_identity"], "@studio-a")


if __name__ == "__main__":
    unittest.main()
