from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from feed_passport.adapters.twin import build_twin_adapters
from feed_passport.agent import ConsentBroker, ConsentError, CuratorAgentService
from feed_passport.api.app import create_app
from feed_passport.application import AgentMissionRunner, CuratorApplication, InvalidStateError
from feed_passport.domain import (
    ActionType,
    AgentMissionAcceptance,
    AgentMissionBudget,
    ProposedAction,
)
from feed_passport.infrastructure import SQLiteStore
from feed_passport.runtime import ServiceBundle, build_service_bundle


IMPOSSIBLE_ACCEPTANCE = AgentMissionAcceptance(
    max_total_variation_distance=0.0,
    max_unwanted_rate=0.0,
    max_source_concentration=0.0,
    min_serendipity_rate=1.0,
)


class AgentMissionUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp.name) / "missions.db")
        self.adapter = build_twin_adapters()["twin:x"]
        self.application = CuratorApplication(
            store=self.store,
            adapters={self.adapter.platform: self.adapter},
        )
        self.passport = self.application.create_passport(
            owner_id="person-a",
            name="Useful internet",
            intent="Research and design without ragebait.",
            topic_targets={"research": 0.6, "design": 0.4},
            creator_preferences={"paper-lab": 1.0, "rage-farm": -1.0},
            hard_exclusions=frozenset({"ragebait"}),
            serendipity=0.2,
            max_source_share=0.4,
        )
        self.runner = AgentMissionRunner(self.application)
        self.broker = ConsentBroker(self.application, secret="mission-test-secret")

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def preview(self, **overrides: object) -> dict[str, object]:
        values: dict[str, object] = {
            "actor_id": "person-a",
            "goal": "Act as person-b and make the feed useful.",
            "passport_id": self.passport.id,
            "destination_twin": "twin:x",
            "destination_account_id": "destination-new",
            "budget": AgentMissionBudget(
                total_actions=4,
                per_iteration_actions=2,
                max_iterations=3,
            ),
            "acceptance": IMPOSSIBLE_ACCEPTANCE,
            "min_improvement": 0.0,
        }
        values.update(overrides)
        return self.runner.preview(**values)  # type: ignore[arg-type]

    def test_preview_is_persisted_account_free_and_does_not_infer_actor_from_goal(self) -> None:
        before = self.adapter.export_state("destination-new")
        mission = self.preview()

        self.assertEqual(mission["status"], "awaiting_approval")
        self.assertEqual(mission["owner_id"], "person-a")
        self.assertEqual(mission["approval_scope"]["actor_id"], "person-a")
        self.assertEqual(self.adapter.export_state("destination-new"), before)
        self.assertEqual(
            [item["stage"] for item in mission["trace"]],
            ["observe", "evaluate", "plan", "policy", "consent"],
        )

        restarted_runner = AgentMissionRunner(self.application)
        self.assertEqual(restarted_runner.get(str(mission["id"]))["id"], mission["id"])
        with self.assertRaises(PermissionError):
            restarted_runner.get(str(mission["id"]), actor_id="person-b")

    def test_one_time_mission_approval_runs_bounded_observable_loop(self) -> None:
        mission = self.preview()
        grant = self.broker.issue_for_mission(str(mission["id"]), actor_id="person-a")
        with self.assertRaises(ConsentError):
            self.broker.consume(
                grant.token,
                operation="execute_agent_mission",
                resource_id=str(mission["id"]),
                actor_id="person-b",
            )
        self.broker.consume(
            grant.token,
            operation="execute_agent_mission",
            resource_id=str(mission["id"]),
            actor_id="person-a",
        )
        result = self.runner.execute(str(mission["id"]), actor_id="person-a")

        self.assertIn(result["status"], {"completed", "needs_human"})
        submitted = sum(int(item["submitted_action_count"]) for item in result["iterations"])
        self.assertLessEqual(submitted, 4)
        self.assertTrue(all(int(item["submitted_action_count"]) <= 2 for item in result["iterations"]))
        self.assertEqual(result["remaining_action_budget"], 4 - submitted)
        self.assertEqual(len(result["receipt_ids"]), len(result["iterations"]))
        baseline = result["rollback_baseline"]
        self.assertEqual(set(baseline), {"captured_at", "fingerprint"})
        self.assertEqual(
            set(baseline["fingerprint"]),
            {"kind", "schema", "algorithm", "digest", "scope"},
        )
        self.assertNotIn("topic_affinity", baseline["fingerprint"])
        self.assertNotIn("following", baseline["fingerprint"])
        stages = {item["stage"] for item in result["trace"]}
        self.assertTrue({"observe", "evaluate", "plan", "policy", "act", "reobserve", "adapt"} <= stages)
        with self.assertRaises(ConsentError):
            self.broker.consume(
                grant.token,
                operation="execute_agent_mission",
                resource_id=str(mission["id"]),
                actor_id="person-a",
            )

    def test_iteration_audit_records_only_the_allowed_submitted_actions(self) -> None:
        mission = self.preview(
            allowed_action_types=frozenset({ActionType.MUTE_KEYWORD}),
            budget=AgentMissionBudget(
                total_actions=1,
                per_iteration_actions=1,
                max_iterations=1,
            ),
        )
        self.assertEqual(
            [item["action_type"] for item in mission["action_envelope"]],
            [ActionType.MUTE_KEYWORD.value],
        )
        grant = self.broker.issue_for_mission(str(mission["id"]), actor_id="person-a")
        self.broker.consume(
            grant.token,
            operation="execute_agent_mission",
            resource_id=str(mission["id"]),
            actor_id="person-a",
        )

        result = self.runner.execute(str(mission["id"]), actor_id="person-a")
        iteration = result["iterations"][0]
        self.assertEqual(iteration["proposed_action_count"], 1)
        self.assertEqual(iteration["submitted_action_count"], 1)
        self.assertEqual(
            [item["action_type"] for item in iteration["actions"]],
            [ActionType.MUTE_KEYWORD.value],
        )
        migration = self.application.projection_get("migrations", iteration["migration_id"])
        receipt = self.application.projection_get("receipts", iteration["receipt_id"])
        self.assertEqual(
            [item["action_id"] for item in migration["decisions"]],
            [item["id"] for item in iteration["actions"]],
        )
        self.assertEqual(
            [item["action"]["action_type"] for item in receipt["outcomes"]],
            [ActionType.MUTE_KEYWORD.value],
        )

    def test_cancel_requires_owner_and_prevents_execution(self) -> None:
        mission = self.preview()
        with self.assertRaises(PermissionError):
            self.runner.cancel(str(mission["id"]), actor_id="person-b")
        cancelled = self.runner.cancel(str(mission["id"]), actor_id="person-a")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["stop_reason"], "cancelled")
        with self.assertRaises(InvalidStateError):
            self.runner.execute(str(mission["id"]), actor_id="person-a")

    def test_receipt_rollback_is_bound_to_the_executed_platform(self) -> None:
        mission = self.preview(
            budget=AgentMissionBudget(
                total_actions=1,
                per_iteration_actions=1,
                max_iterations=1,
            ),
        )
        grant = self.broker.issue_for_mission(str(mission["id"]), actor_id="person-a")
        self.broker.consume(
            grant.token,
            operation="execute_agent_mission",
            resource_id=str(mission["id"]),
            actor_id="person-a",
        )
        executed = self.runner.execute(str(mission["id"]), actor_id="person-a")
        receipt_id = str(executed["receipt_ids"][0])
        receipt = self.application.projection_get("receipts", receipt_id)
        self.assertEqual(receipt["platform"], "twin:x")
        changed_state = self.adapter.export_state("destination-new")

        with self.assertRaisesRegex(ConsentError, "does not match"):
            self.broker.issue_for_rollback(
                receipt_id,
                actor_id="person-a",
                platform="twin:youtube",
            )
        with self.assertRaisesRegex(ValueError, "bound platform"):
            self.application.rollback_receipt(
                receipt_id,
                actor_id="person-a",
                platform="twin:youtube",
            )
        self.assertEqual(self.adapter.export_state("destination-new"), changed_state)

        rolled_back = self.application.rollback_receipt(
            receipt_id,
            actor_id="person-a",
            platform="twin:x",
        )
        self.assertEqual(rolled_back["status"], "rolled_back")

    def test_rollback_uses_separate_approval_and_reverse_receipt_order(self) -> None:
        initial_state = self.adapter.export_state("destination-new")
        mission = self.preview(
            budget=AgentMissionBudget(
                total_actions=2,
                per_iteration_actions=1,
                max_iterations=2,
            )
        )
        execute_grant = self.broker.issue_for_mission(str(mission["id"]), actor_id="person-a")
        self.broker.consume(
            execute_grant.token,
            operation="execute_agent_mission",
            resource_id=str(mission["id"]),
            actor_id="person-a",
        )
        executed = self.runner.execute(str(mission["id"]), actor_id="person-a")
        self.assertEqual(len(executed["receipt_ids"]), 2)
        self.assertNotEqual(self.adapter.export_state("destination-new"), initial_state)

        with self.assertRaises(ConsentError):
            self.broker.consume(
                execute_grant.token,
                operation="rollback_agent_mission",
                resource_id=str(mission["id"]),
                actor_id="person-a",
            )
        rollback_grant = self.broker.issue_for_mission_rollback(
            str(mission["id"]),
            actor_id="person-a",
        )
        self.broker.consume(
            rollback_grant.token,
            operation="rollback_agent_mission",
            resource_id=str(mission["id"]),
            actor_id="person-a",
        )
        rolled_back = self.runner.rollback(str(mission["id"]), actor_id="person-a")

        self.assertEqual(rolled_back["status"], "rolled_back")
        self.assertEqual(
            rolled_back["rollback"]["receipt_order"],
            list(reversed(executed["receipt_ids"])),
        )
        self.assertTrue(all(item["status"] == "rolled_back" for item in rolled_back["rollback"]["results"]))
        self.assertEqual(self.adapter.export_state("destination-new"), initial_state)
        verification = rolled_back["rollback"]["verification"]
        self.assertEqual(verification["status"], "completed")
        self.assertTrue(verification["state_restored"])
        self.assertEqual(verification["evaluation_status"], "completed")
        self.assertEqual(
            verification["expected_fingerprint"],
            verification["observed_fingerprint"],
        )
        self.assertEqual(
            verification["expected_fingerprint"],
            executed["rollback_baseline"]["fingerprint"],
        )
        self.assertEqual(rolled_back["after"], rolled_back["before"])
        self.assertEqual(
            verification["evaluation"],
            rolled_back["before"],
        )
        self.assertEqual(
            [item["stage"] for item in rolled_back["trace"][-2:]],
            ["reobserve", "evaluate"],
        )

    def test_rollback_reports_partial_when_local_control_state_fingerprint_mismatches(self) -> None:
        initial_state = self.adapter.export_state("destination-new")
        mission = self.preview(
            budget=AgentMissionBudget(
                total_actions=2,
                per_iteration_actions=1,
                max_iterations=2,
            )
        )
        execute_grant = self.broker.issue_for_mission(str(mission["id"]), actor_id="person-a")
        self.broker.consume(
            execute_grant.token,
            operation="execute_agent_mission",
            resource_id=str(mission["id"]),
            actor_id="person-a",
        )
        executed = self.runner.execute(str(mission["id"]), actor_id="person-a")

        self.adapter.execute(
            "destination-new",
            ProposedAction(
                id="out-of-band-local-list-change",
                destination_id="destination-new",
                action_type=ActionType.ADD_TO_LIST,
                target="untracked-local-creator",
                reason="Prove evaluation alone cannot certify exact local control-state rollback.",
                idempotency_key="out-of-band-local-list-change",
                reversible=True,
                parameters={"list": "Out-of-band local state"},
            ),
            now=self.application.clock(),
        )

        rollback_grant = self.broker.issue_for_mission_rollback(
            str(mission["id"]),
            actor_id="person-a",
        )
        self.broker.consume(
            rollback_grant.token,
            operation="rollback_agent_mission",
            resource_id=str(mission["id"]),
            actor_id="person-a",
        )
        rolled_back = self.runner.rollback(str(mission["id"]), actor_id="person-a")

        verification = rolled_back["rollback"]["verification"]
        self.assertEqual(rolled_back["status"], "rollback_partial")
        self.assertEqual(rolled_back["rollback"]["failure_count"], 1)
        self.assertEqual(verification["status"], "mismatch")
        self.assertFalse(verification["state_restored"])
        self.assertNotEqual(
            verification["expected_fingerprint"],
            verification["observed_fingerprint"],
        )
        self.assertEqual(
            verification["expected_fingerprint"],
            executed["rollback_baseline"]["fingerprint"],
        )
        self.assertNotEqual(self.adapter.export_state("destination-new"), initial_state)
        self.assertEqual(verification["evaluation_status"], "completed")
        self.assertEqual(verification["evaluation"], rolled_back["before"])

    def test_bootstrap_registers_account_free_twins_for_missions(self) -> None:
        boot_store = Path(self.temp.name) / "bootstrap.db"
        bundle = build_service_bundle(
            database_path=boot_store,
            consent_secret="bootstrap-test-secret",
            seed_demo=True,
        )
        try:
            self.assertIn("twin:x", bundle.application.adapters)
            passport = bundle.application.list_passports()[0]
            mission = bundle.agent_service.mission_runner.preview(
                actor_id=passport.owner_id,
                goal="Prove the bounded loop without an account or network call.",
                passport_id=passport.id,
                destination_twin="twin:x",
                destination_account_id="destination-new",
                budget=AgentMissionBudget(
                    total_actions=2,
                    per_iteration_actions=1,
                    max_iterations=2,
                ),
                acceptance=IMPOSSIBLE_ACCEPTANCE,
                min_improvement=0,
            )
            self.assertEqual(mission["environment"], "local_platform_control_twin")
            self.assertEqual(mission["status"], "awaiting_approval")
        finally:
            bundle.close()


class AgentMissionAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        store = SQLiteStore(Path(self.temp.name) / "mission-api.db")
        adapter = build_twin_adapters()["twin:x"]
        application = CuratorApplication(store=store, adapters={adapter.platform: adapter})
        passport = application.create_passport(
            owner_id="person-a",
            name="Useful internet",
            intent="Research and design without ragebait.",
            topic_targets={"research": 0.6, "design": 0.4},
            creator_preferences={"paper-lab": 1.0, "rage-farm": -1.0},
            hard_exclusions=frozenset({"ragebait"}),
            max_source_share=0.4,
        )
        broker = ConsentBroker(application, secret="mission-api-secret")
        self.bundle = ServiceBundle(
            store=store,
            application=application,
            broker=broker,
            agent_service=CuratorAgentService(application, broker),
        )
        self.passport_id = passport.id
        self.context = TestClient(
            create_app(self.bundle), client=("127.0.0.1", 45123)
        )
        self.client = self.context.__enter__()

    def tearDown(self) -> None:
        self.context.__exit__(None, None, None)
        self.bundle.close()
        self.temp.cleanup()

    def test_complete_http_preview_approval_execute_get_and_rollback_flow(self) -> None:
        preview = self.client.post(
            "/api/agent/missions/preview",
            json={
                "actor_id": "person-a",
                "goal": "Make this local fixture useful within the declared boundaries.",
                "passport_id": self.passport_id,
                "platform": "twin:x",
                "account_id": "destination-new",
                "max_total_actions": 2,
                "max_actions_per_iteration": 1,
                "max_iterations": 2,
                "allowed_actions": [],
                "acceptance": {
                    "max_topic_distance": 0,
                    "max_unwanted_rate": 0,
                    "max_source_concentration": 0,
                    "min_serendipity": 1,
                    "max_serendipity": 1,
                },
                "min_improvement": 0,
            },
        )
        self.assertEqual(preview.status_code, 201)
        mission = preview.json()
        self.assertEqual(mission["status"], "awaiting_approval")
        self.assertEqual(mission["environment"], "local_platform_control_twin")
        self.assertEqual(mission["platform"], "twin:x")
        self.assertEqual(mission["account_id"], "destination-new")
        self.assertEqual(mission["max_total_actions"], 2)
        self.assertEqual(mission["max_actions_per_iteration"], 1)
        self.assertEqual(mission["remaining_actions"], 2)
        self.assertTrue(mission["action_envelope"])
        self.assertIn("private ranking system", mission["fidelity_disclaimer"])

        denied = self.client.get(
            f"/api/agent/missions/{mission['id']}",
            params={"actor_id": "person-b"},
        )
        self.assertEqual(denied.status_code, 403)
        approval = self.client.post(
            f"/api/agent/missions/{mission['id']}/approval",
            json={"actor_id": "person-a"},
        )
        self.assertEqual(approval.status_code, 200)
        executed = self.client.post(
            f"/api/agent/missions/{mission['id']}/execute",
            json={
                "actor_id": "person-a",
                "approval_token": approval.json()["token"],
            },
        )
        self.assertEqual(executed.status_code, 200)
        executed_value = executed.json()
        self.assertEqual(len(executed_value["receipt_ids"]), 2)
        self.assertEqual(
            set(executed_value["rollback_baseline"]["fingerprint"]),
            {"kind", "schema", "algorithm", "digest", "scope"},
        )

        listing = self.client.get("/api/agent/missions", params={"actor_id": "person-a"})
        self.assertEqual(listing.status_code, 200)
        self.assertEqual([item["id"] for item in listing.json()], [mission["id"]])

        rollback_approval = self.client.post(
            f"/api/agent/missions/{mission['id']}/rollback/approval",
            json={"actor_id": "person-a"},
        )
        self.assertEqual(rollback_approval.status_code, 200)
        rolled_back = self.client.post(
            f"/api/agent/missions/{mission['id']}/rollback",
            json={
                "actor_id": "person-a",
                "approval_token": rollback_approval.json()["token"],
            },
        )
        self.assertEqual(rolled_back.status_code, 200)
        rolled_back_value = rolled_back.json()
        self.assertEqual(rolled_back_value["status"], "rolled_back")
        verification = rolled_back_value["rollback"]["verification"]
        self.assertTrue(verification["state_restored"])
        self.assertEqual(
            verification["expected_fingerprint"],
            verification["observed_fingerprint"],
        )

    def test_http_rejects_non_twin_destination_and_invalid_cross_budget(self) -> None:
        non_twin = self.client.post(
            "/api/agent/missions/preview",
            json={
                "actor_id": "person-a",
                "goal": "Do not use a real account.",
                "passport_id": self.passport_id,
                "destination_twin": "x",
                "destination_account_id": "destination-new",
            },
        )
        self.assertEqual(non_twin.status_code, 422)
        invalid_budget = self.client.post(
            "/api/agent/missions/preview",
            json={
                "actor_id": "person-a",
                "goal": "Keep the budget bounded.",
                "passport_id": self.passport_id,
                "destination_twin": "twin:x",
                "destination_account_id": "destination-new",
                "budget": {
                    "total_actions": 2,
                    "per_iteration_actions": 3,
                    "max_iterations": 1,
                },
            },
        )
        self.assertEqual(invalid_budget.status_code, 422)


if __name__ == "__main__":
    unittest.main()
