from __future__ import annotations

import math
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from feed_passport.adapters.lab import LabAdapter
from feed_passport.agent.feed_goal_planner import (
    FeedGoalEvidence,
    FeedGoalProposal,
    FeedGoalResult,
    FeedGoalUsage,
)
from feed_passport.application import CuratorApplication, EvidenceLink, FeedEvidenceService
from feed_passport.infrastructure import SQLiteStore


class IncrementingIds:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"{self.value:04d}"


class StubResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self.text = ""
        self.payload = payload

    def json(self):
        return self.payload


class StubHttp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("resolveHandle"):
            return StubResponse(200, {"did": "did:plc:animal-science"})
        if url.endswith("getPosts"):
            return StubResponse(
                200,
                {
                    "posts": [
                        {
                            "record": {
                                "text": (
                                    "A veterinary study of why cats knead blankets. "
                                    "Pet science is delightful."
                                )
                            },
                            "author": {
                                "did": "did:plc:animal-science",
                                "handle": "pets.example",
                                "displayName": "Pet Lab",
                            },
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected URL: {url}")


class FeedEvidenceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp.name) / "feed-evidence.db")
        self.ids = IncrementingIds()
        self.now = datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc)
        self.application = CuratorApplication(
            store=self.store,
            adapters={"feed_passport_lab": LabAdapter()},
            clock=lambda: self.now,
            id_factory=self.ids,
        )
        self.passport = self.application.create_passport(
            owner_id="person-a",
            name="My useful internet",
            intent="Calm research and design without ragebait.",
            topic_targets={"research": 0.5, "design": 0.5},
            hard_exclusions=frozenset({"ragebait"}),
            max_outrage=0.05,
        )
        self.http = StubHttp()
        self.service = FeedEvidenceService(
            application=self.application,
            http_client=self.http,
            clock=lambda: self.now,
            id_factory=self.ids,
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    @staticmethod
    def _agent_result(*, pet_weight: float = 0.6) -> FeedGoalResult:
        return FeedGoalResult(
            proposal=FeedGoalProposal(
                target_topic_weights={
                    "pet_science": pet_weight,
                    "cute_drawing": 0.2,
                    "exploration": round(0.8 - pet_weight, 6),
                },
                hard_exclusions=["ragebait"],
                rationale="The explicit targets are preserved from the owner request.",
            ),
            evidence=FeedGoalEvidence(
                provider="scripted_local",
                model_id="feed-goal-test",
                endpoint_scope="scripted_no_network",
                external_model_calls=False,
                paid_model_calls=False,
                stop_reason="limit_turns",
                duration_ms=4,
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

    def test_analyze_separates_provider_metadata_from_inference_and_builds_target_mix(self) -> None:
        result = self.service.analyze(
            actor_id="person-a",
            passport_id=self.passport.id,
            goal=(
                "Reduce ragebait. Make my feed 60% pet science and 20% cute drawing; "
                "keep the remainder exploratory."
            ),
            stage="before",
            links=(EvidenceLink("https://bsky.app/profile/pets.example/post/3abc"),),
        )

        item = self.store.get_projection(
            "feed_evidence_snapshots", result["snapshot_id"]
        )[1]["items"][0]
        self.assertEqual(item["metadata_source"], "bluesky_public_appview")
        self.assertTrue(item["metadata_verified"])
        self.assertIn("pet_science", item["inference"]["topics"])
        self.assertEqual(
            result["proposal"]["target_topic_weights"],
            {"cute_drawing": 0.2, "exploration": 0.2, "pet_science": 0.6},
        )
        self.assertEqual(result["proposal"]["passport_changes"]["max_outrage"], 0.03)
        self.assertIn("not the platform's private FYP", self.store.get_projection(
            "feed_evidence_snapshots", result["snapshot_id"]
        )[1]["claim_boundary"])

    def test_analyze_turns_a_vague_request_into_a_measurable_topic_shift(self) -> None:
        result = self.service.analyze(
            actor_id="person-a",
            passport_id=self.passport.id,
            goal="I want less ragebait and more science-based pages.",
            stage="before",
            links=(
                EvidenceLink(
                    "https://www.instagram.com/p/science123/",
                    "A calm explanation of a recent astronomy observation.",
                ),
            ),
        )

        targets = result["proposal"]["target_topic_weights"]
        changes = result["proposal"]["passport_changes"]
        self.assertGreater(targets["science"], 0)
        self.assertTrue(math.isclose(sum(targets.values()), 1.0, abs_tol=0.000001))
        self.assertIn("ragebait", changes["hard_exclusions"])
        self.assertEqual(changes["max_outrage"], 0.03)

    def test_bluesky_link_only_mode_accepts_a_bare_link_without_network_metadata(self) -> None:
        service = FeedEvidenceService(
            application=self.application,
            http_client=self.http,
            public_metadata_enabled=False,
            clock=lambda: self.now,
            id_factory=self.ids,
        )
        result = service.analyze(
            actor_id="person-a",
            passport_id=self.passport.id,
            goal="Show me more science.",
            stage="before",
            links=(EvidenceLink("https://bsky.app/profile/science.example/post/3bare"),),
        )

        item = result["snapshot"]["items"][0]
        self.assertEqual(item["metadata_source"], "user_selected_link_only")
        self.assertEqual(item["user_note"], "")
        self.assertEqual(self.http.calls, [])

    def test_apply_requires_unchanged_version_and_records_versioned_result(self) -> None:
        result = self.service.analyze(
            actor_id="person-a",
            passport_id=self.passport.id,
            goal="Make this 70% pet science and 30% cute drawing.",
            stage="before",
            links=(
                EvidenceLink(
                    "https://www.instagram.com/reel/example123/",
                    "A cute drawing of a cat explained with animal behavior research.",
                ),
            ),
        )
        applied = self.service.apply(
            result["id"],
            actor_id="person-a",
            expected_passport_version=self.passport.version,
        )

        self.assertEqual(applied["status"], "applied_to_passport")
        self.assertEqual(applied["passport"]["version"], self.passport.version + 1)
        self.assertEqual(
            applied["passport"]["topic_targets"],
            {"cute_drawing": 0.3, "pet_science": 0.7},
        )
        with self.assertRaisesRegex(Exception, "no longer awaiting consent"):
            self.service.apply(
                result["id"],
                actor_id="person-a",
                expected_passport_version=self.passport.version,
            )

    def test_after_snapshot_compares_only_against_same_owner_and_passport(self) -> None:
        before = self.service.analyze(
            actor_id="person-a",
            passport_id=self.passport.id,
            goal="Reduce ragebait.",
            stage="before",
            links=(
                EvidenceLink(
                    "https://www.instagram.com/p/before123/",
                    "Shocking outrage ragebait exposed.",
                ),
            ),
        )
        after = self.service.analyze(
            actor_id="person-a",
            passport_id=self.passport.id,
            goal="Reduce ragebait.",
            stage="after",
            baseline_snapshot_id=before["snapshot_id"],
            links=(
                EvidenceLink(
                    "https://www.instagram.com/p/after123/",
                    "A calm drawing and veterinary study.",
                ),
            ),
        )

        self.assertEqual(after["comparison"]["ragebait_rate_delta"], -1.0)
        self.assertEqual(
            after["comparison"]["baseline_snapshot_id"], before["snapshot_id"]
        )
        self.assertEqual(after["status"], "comparison_recorded")
        self.assertNotIn("result_passport_version", after)
        self.assertIn("not the app's whole recommendation feed", after["comparison"]["claim_boundary"])
        with self.assertRaisesRegex(Exception, "no longer awaiting consent"):
            self.service.apply(
                after["id"],
                actor_id="person-a",
                expected_passport_version=self.passport.version,
            )

    def test_evidence_urls_are_canonicalized_and_stage_pairs_fail_closed(self) -> None:
        before = self.service.analyze(
            actor_id="person-a",
            passport_id=self.passport.id,
            goal="Reduce ragebait.",
            stage="before",
            links=(
                EvidenceLink(
                    "https://www.instagram.com/reel/example123/?utm_source=copy_link#fragment",
                    "A veterinary explanation.",
                ),
            ),
        )
        item = before["snapshot"]["items"][0]
        self.assertEqual(item["url"], "https://www.instagram.com/reel/example123/")
        with self.assertRaisesRegex(ValueError, "requires a before baseline"):
            self.service.analyze(
                actor_id="person-a",
                passport_id=self.passport.id,
                goal="Reduce ragebait.",
                stage="after",
                links=(EvidenceLink("https://www.instagram.com/p/after123/", "calm"),),
            )

    def test_agent_receives_sanitized_evidence_and_cannot_change_explicit_percentages(self) -> None:
        result = self.service.analyze(
            actor_id="person-a",
            passport_id=self.passport.id,
            goal=(
                "Reduce ragebait. Make this 60% pet science and 20% cute drawing; "
                "keep the remainder exploratory."
            ),
            stage="before",
            links=(
                EvidenceLink(
                    "https://www.instagram.com/reel/secret123/?utm_source=private",
                    "A private owner note about veterinary drawing.",
                ),
            ),
        )
        passport, goal, evidence = self.service.planner_input(
            result["id"], actor_id="person-a"
        )
        serialized = evidence[0].model_dump(mode="json")
        self.assertEqual(passport.id, self.passport.id)
        self.assertIn("60% pet science", goal)
        self.assertNotIn("url", serialized)
        self.assertNotIn("note", serialized)
        self.assertEqual(serialized["title"], "")
        attached = self.service.attach_agent_plan(
            result["id"],
            actor_id="person-a",
            expected_passport_version=self.passport.version,
            result=self._agent_result(),
        )
        self.assertEqual(attached["proposal"]["interpretation_source"], "strands_model")
        self.assertEqual(attached["agent_evidence"]["authority"], "proposal_only")

        second = self.service.analyze(
            actor_id="person-a",
            passport_id=self.passport.id,
            goal=(
                "Reduce ragebait. Make this 60% pet science and 20% cute drawing; "
                "keep the remainder exploratory."
            ),
            stage="before",
            links=(EvidenceLink("https://www.instagram.com/p/other123/", "calm"),),
        )
        with self.assertRaisesRegex(Exception, "explicit percentage"):
            self.service.attach_agent_plan(
                second["id"],
                actor_id="person-a",
                expected_passport_version=self.passport.version,
                result=self._agent_result(pet_weight=0.5),
            )
        with self.assertRaisesRegex(ValueError, "only once"):
            self.service.analyze(
                actor_id="person-a",
                passport_id=self.passport.id,
                goal="Make this 60% pet science and 20% pet science.",
                stage="before",
                links=(EvidenceLink("https://www.instagram.com/p/after123/", "calm"),),
            )


if __name__ == "__main__":
    unittest.main()
