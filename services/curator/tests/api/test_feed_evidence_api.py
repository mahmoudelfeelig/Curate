from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from feed_passport.adapters.lab import LabAdapter
from feed_passport.agent import ConsentBroker, CuratorAgentService
from feed_passport.agent.feed_goal_planner import (
    FeedGoalEvidence,
    FeedGoalProposal,
    FeedGoalResult,
    FeedGoalUsage,
)
from feed_passport.api.app import create_app
from feed_passport.application import CuratorApplication, FeedEvidenceService
from feed_passport.infrastructure import SQLiteStore
from feed_passport.runtime import ServiceBundle


class NoNetworkHttp:
    def request(self, *_args, **_kwargs):
        raise AssertionError("the Instagram owner-note flow must not make a network request")


class ScriptedFeedPlanner:
    def __init__(self) -> None:
        self.calls = []

    async def propose(self, **kwargs) -> FeedGoalResult:
        self.calls.append(kwargs)
        return FeedGoalResult(
            proposal=FeedGoalProposal(
                target_topic_weights={
                    "pet_science": 0.6,
                    "cute_drawing": 0.2,
                    "exploration": 0.2,
                },
                hard_exclusions=["ragebait"],
                rationale="The owner percentages remain exact and the evidence is a small sample.",
            ),
            evidence=FeedGoalEvidence(
                provider="scripted_local",
                model_id="feed-goal-api-test",
                endpoint_scope="scripted_no_network",
                external_model_calls=False,
                paid_model_calls=False,
                stop_reason="limit_turns",
                duration_ms=3,
                cycles=3,
                usage=FeedGoalUsage(input_tokens=30, output_tokens=20, total_tokens=50),
                tools=[
                    {"name": "inspect_selected_passport", "status": "completed"},
                    {"name": "inspect_sanitized_evidence", "status": "completed"},
                    {"name": "submit_feed_goal_proposal", "status": "accepted"},
                ],
                locked_by_server=(
                    "actor_and_passport_version",
                    "consent_and_application",
                    "connections_and_credentials",
                    "execution_and_rollback",
                ),
            ),
        )


class FeedEvidenceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        store = SQLiteStore(Path(self.temp.name) / "feed-evidence-api.db")
        lab = LabAdapter()
        application = CuratorApplication(store=store, adapters={lab.platform: lab})
        passport = application.create_passport(
            owner_id="person-a",
            name="Useful internet",
            intent="Calm research without ragebait.",
            topic_targets={"research": 1.0},
            hard_exclusions=frozenset({"ragebait"}),
        )
        broker = ConsentBroker(application, secret="feed-evidence-api-secret")
        self.planner = ScriptedFeedPlanner()
        self.bundle = ServiceBundle(
            store=store,
            application=application,
            broker=broker,
            agent_service=CuratorAgentService(application, broker),
            feed_goal_planner=self.planner,  # type: ignore[arg-type]
            feed_evidence_service=FeedEvidenceService(
                application=application,
                http_client=NoNetworkHttp(),  # type: ignore[arg-type]
            ),
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

    def test_capture_model_plan_apply_and_comparison_are_separate_boundaries(self) -> None:
        before = self.client.post(
            "/api/agent/feed-evidence/analyze",
            json={
                "actor_id": "person-a",
                "passport_id": self.passport_id,
                "goal": (
                    "Reduce ragebait. Make this 60% pet science and 20% cute drawing; "
                    "keep the remainder exploratory."
                ),
                "stage": "before",
                "links": [
                    {
                        "url": "https://www.instagram.com/reel/evidence123/?utm_source=private",
                        "note": "A veterinary explanation with a cute drawing.",
                    }
                ],
            },
        )
        self.assertEqual(before.status_code, 201)
        captured = before.json()
        self.assertEqual(captured["status"], "awaiting_owner_consent")

        planned = self.client.post(
            f"/api/agent/feed-evidence/{captured['id']}/model-plan",
            json={"actor_id": "person-a", "expected_passport_version": 1},
        )
        self.assertEqual(planned.status_code, 200)
        self.assertEqual(planned.json()["agent_evidence"]["authority"], "proposal_only")
        model_item = self.planner.calls[0]["evidence"][0].model_dump(mode="json")
        self.assertNotIn("url", model_item)
        self.assertNotIn("note", model_item)
        self.assertNotIn("evidence123", str(model_item))

        applied = self.client.post(
            f"/api/agent/feed-evidence/{captured['id']}/apply",
            json={"actor_id": "person-a", "expected_passport_version": 1},
        )
        self.assertEqual(applied.status_code, 200)
        self.assertEqual(applied.json()["result_passport_version"], 2)

        after = self.client.post(
            "/api/agent/feed-evidence/analyze",
            json={
                "actor_id": "person-a",
                "passport_id": self.passport_id,
                "goal": "Reduce ragebait.",
                "stage": "after",
                "baseline_snapshot_id": captured["snapshot_id"],
                "links": [
                    {
                        "url": "https://www.instagram.com/p/after123/",
                        "note": "A calm veterinary study.",
                    }
                ],
            },
        )
        self.assertEqual(after.status_code, 201)
        self.assertEqual(after.json()["status"], "comparison_recorded")
        comparison_apply = self.client.post(
            f"/api/agent/feed-evidence/{after.json()['id']}/apply",
            json={"actor_id": "person-a", "expected_passport_version": 2},
        )
        self.assertEqual(comparison_apply.status_code, 409)

    def test_analyze_accepts_a_link_without_a_description(self) -> None:
        response = self.client.post(
            "/api/agent/feed-evidence/analyze",
            json={
                "actor_id": "person-a",
                "passport_id": self.passport_id,
                "goal": "Show me more thoughtful drawing.",
                "stage": "before",
                "links": [
                    {"url": "https://www.instagram.com/p/linkonly123/"},
                ],
            },
        )

        self.assertEqual(response.status_code, 201)
        item = response.json()["snapshot"]["items"][0]
        self.assertEqual(item["user_note"], "")
        self.assertEqual(item["metadata_source"], "user_selected_link_only")


if __name__ == "__main__":
    unittest.main()
