from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError
from strands.models import Model

from feed_passport.adapters.lab import LabAdapter
from feed_passport.agent import (
    AgentCommand,
    ConsentBroker,
    ConsentError,
    CuratorAgentService,
    build_strands_agent,
)
from feed_passport.agent.hooks import CapabilityConsentHooks
from feed_passport.agent.tools import build_curator_tools
from feed_passport.application import CuratorApplication
from feed_passport.infrastructure import SQLiteStore
from feed_passport.runtime.agentcore_app import create_agentcore_app


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class OfflineModel(Model):
    """No-network model used only to prove real Strands construction."""

    def update_config(self, **model_config: object) -> None:
        return None

    def get_config(self) -> dict[str, object]:
        return {"context_window_limit": 1024}

    async def structured_output(self, *args: object, **kwargs: object):
        if False:
            yield {}

    async def stream(self, *args: object, **kwargs: object):
        if False:
            yield {}


class ConsentAndStrandsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.clock = MutableClock(datetime(2026, 8, 29, 20, 0, tzinfo=timezone.utc))
        self.store = SQLiteStore(Path(self.temp.name) / "agent.db")
        self.adapter = LabAdapter()
        self.application = CuratorApplication(
            store=self.store,
            adapters={self.adapter.platform: self.adapter},
            clock=self.clock,
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
        grant_ids = iter((f"grant-{index}" for index in range(1, 100)))
        self.broker = ConsentBroker(
            self.application,
            secret="deterministic-test-secret",
            clock=self.clock,
            id_factory=lambda: next(grant_ids),
        )
        self.migration = self.application.prepare_migration(
            passport_id=self.passport.id,
            platform=self.adapter.platform,
            destination_account_id="destination-new",
            actor_id="person-a",
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def invoke_tool(self, name: str, values: dict[str, object]):
        tool = next(
            item
            for item in build_curator_tools(self.application, self.broker)
            if item.tool_name == name
        )

        async def invoke():
            events = [
                event
                async for event in tool.stream(
                    {"toolUseId": f"test-{name}", "name": name, "input": values},
                    {},
                )
            ]
            return events[-1]

        return asyncio.run(invoke())

    def test_grant_is_scoped_signed_short_lived_and_one_time(self) -> None:
        grant = self.broker.issue_for_migration(
            self.migration["id"],
            actor_id="person-a",
            max_total_actions=2,
            ttl=timedelta(minutes=5),
        )
        with self.assertRaises(ConsentError):
            self.broker.inspect(
                grant.token[:-1] + ("A" if grant.token[-1] != "A" else "B"),
                operation="execute_migration",
                resource_id=self.migration["id"],
                actor_id="person-a",
            )
        with self.assertRaises(ConsentError):
            self.broker.inspect(
                grant.token,
                operation="execute_migration",
                resource_id=self.migration["id"],
                actor_id="person-b",
            )

        payload = self.broker.consume(
            grant.token,
            operation="execute_migration",
            resource_id=self.migration["id"],
            actor_id="person-a",
        )
        self.assertEqual(payload["max_total_actions"], 2)
        with self.assertRaises(ConsentError):
            self.broker.consume(
                grant.token,
                operation="execute_migration",
                resource_id=self.migration["id"],
                actor_id="person-a",
            )

    def test_expired_grant_fails_closed(self) -> None:
        grant = self.broker.issue_for_migration(
            self.migration["id"],
            actor_id="person-a",
            ttl=timedelta(seconds=10),
        )
        self.clock.value += timedelta(seconds=10)
        with self.assertRaises(ConsentError):
            self.broker.consume(
                grant.token,
                operation="execute_migration",
                resource_id=self.migration["id"],
                actor_id="person-a",
            )

    def test_agent_command_runs_preview_confirmation_execution_and_rollback(self) -> None:
        service = CuratorAgentService(self.application, self.broker)
        approval = service.handle(
            AgentCommand(
                command="approve_migration",
                actor_id="person-a",
                arguments={"migration_id": self.migration["id"], "max_total_actions": 3},
            )
        )
        executed = service.handle(
            AgentCommand(
                command="execute_migration",
                actor_id="person-a",
                arguments={
                    "migration_id": self.migration["id"],
                    "approval_token": approval.data["approval_token"],
                },
            )
        )
        self.assertEqual(executed.status, "ok")
        self.assertIn(executed.data["status"], {"issued", "issued_with_drift"})
        receipt_id = executed.data["receipt_id"]

        rollback_approval = service.handle(
            AgentCommand(
                command="approve_rollback",
                actor_id="person-a",
                arguments={"receipt_id": receipt_id, "platform": self.adapter.platform},
            )
        )
        rolled_back = service.handle(
            AgentCommand(
                command="rollback_receipt",
                actor_id="person-a",
                arguments={
                    "receipt_id": receipt_id,
                    "platform": self.adapter.platform,
                    "approval_token": rollback_approval.data["approval_token"],
                },
            )
        )
        self.assertEqual(rolled_back.data["status"], "rolled_back")

    def test_agent_command_captures_an_authorized_source_observation(self) -> None:
        service = CuratorAgentService(self.application, self.broker)
        captured = service.handle(
            AgentCommand(
                command="capture_passport",
                actor_id="person-a",
                arguments={
                    "platform": self.adapter.platform,
                    "account_id": "source-main",
                    "name": "Agent-captured source",
                    "intent": "Create a portable policy from the authorized Lab observation.",
                },
            )
        )
        self.assertEqual(captured.status, "ok")
        self.assertNotEqual(captured.data["id"], self.passport.id)
        self.assertIn("research", captured.data["topic_targets"])
        self.assertEqual(captured.trace[-1]["event"], "passport.captured")

    def test_strands_agent_registers_only_capability_safe_tools_and_hooks(self) -> None:
        agent, hooks = build_strands_agent(
            self.application,
            self.broker,
            model=OfflineModel(),
        )
        self.assertEqual(agent.name, "feed-passport-curator")
        self.assertEqual(
            set(agent.tool_names),
            {
                "inspect_passport",
                "inspect_platform_capabilities",
                "capture_source_passport",
                "preview_translation",
                "apply_approved_migration",
                "rollback_approved_receipt",
                "preview_local_agent_mission",
                "inspect_local_agent_mission",
                "execute_approved_agent_mission",
                "cancel_local_agent_mission",
                "rollback_approved_agent_mission",
                "watch_feed_drift",
                "find_creator_continuity",
                "list_temporary_visa_templates",
                "create_temporary_visa",
                "revoke_temporary_visa",
                "create_companion_slice",
                "create_companion_passport",
                "revoke_companion_slice",
                "schedule_drift_monitor",
                "stop_drift_monitor",
                "preserve_creator_continuity",
                "create_passport_checkpoint",
                "restore_passport_checkpoint",
                "export_portable_passport",
                "import_portable_passport",
            },
        )
        self.assertTrue(agent.hooks.has_callbacks())
        self.assertIsInstance(hooks, CapabilityConsentHooks)

        event = SimpleNamespace(
            tool_use={
                "name": "apply_approved_migration",
                "input": {"migration_id": self.migration["id"], "actor_id": "person-a"},
            },
            cancel_tool=False,
        )
        hooks.before_tool_call(event)
        self.assertTrue(event.cancel_tool)

        for tool_name in (
            "execute_approved_agent_mission",
            "rollback_approved_agent_mission",
        ):
            event = SimpleNamespace(
                tool_use={
                    "name": tool_name,
                    "input": {"mission_id": "mission-without-consent", "actor_id": "person-a"},
                },
                cancel_tool=False,
            )
            hooks.before_tool_call(event)
            self.assertTrue(event.cancel_tool)

    def test_deterministic_agent_command_can_schedule_alerts_but_not_future_mutation(self) -> None:
        service = CuratorAgentService(self.application, self.broker)
        common = {
            "passport_id": self.passport.id,
            "platform": self.adapter.platform,
            "account_id": "destination-new",
            "interval_minutes": 15,
            "expires_at": (self.clock.value + timedelta(hours=1)).isoformat(),
        }

        for arguments in (
            {**common, "mode": "bounded_auto", "allowed_actions": ["hide_topic"]},
            {**common, "mode": "alert_only", "allowed_actions": ["hide_topic"]},
        ):
            with self.assertRaisesRegex(PermissionError, "explicit local UI/API approval surface"):
                service.handle(
                    AgentCommand(
                        command="create_drift_monitor",
                        actor_id="person-a",
                        arguments=arguments,
                    )
                )

        alert = service.handle(
            AgentCommand(
                command="create_drift_monitor",
                actor_id="person-a",
                arguments={**common, "mode": "alert_only", "allowed_actions": []},
            )
        )
        self.assertEqual(alert.data["mode"], "alert_only")
        self.assertEqual(alert.data["allowed_actions"], [])
        self.assertEqual(alert.data["status"], "active")

    def test_strands_monitor_tool_can_schedule_alerts_but_not_future_mutation(self) -> None:
        common = {
            "passport_id": self.passport.id,
            "platform": self.adapter.platform,
            "account_id": "destination-new",
            "actor_id": "person-a",
            "interval_minutes": 15,
            "expires_at": (self.clock.value + timedelta(hours=1)).isoformat(),
        }

        for values in (
            {**common, "mode": "bounded_auto", "allowed_actions": ["hide_topic"]},
            {**common, "mode": "alert_only", "allowed_actions": ["hide_topic"]},
        ):
            result = self.invoke_tool("schedule_drift_monitor", values)
            self.assertIsInstance(result.exception, PermissionError)
            self.assertIn(
                "explicit local UI/API approval surface",
                str(result.exception),
            )
            self.assertEqual(result.tool_result["status"], "error")

        alert = self.invoke_tool(
            "schedule_drift_monitor",
            {**common, "mode": "alert_only", "allowed_actions": []},
        )
        self.assertIsNone(alert.exception)
        self.assertEqual(alert.tool_result["status"], "success")
        value = json.loads(alert.tool_result["content"][0]["text"])
        self.assertEqual(value["mode"], "alert_only")
        self.assertEqual(value["allowed_actions"], [])
        self.assertEqual(value["status"], "active")

    def test_agentcore_runtime_exposes_only_the_proposal_boundary(self) -> None:
        runtime, handler = create_agentcore_app()
        self.assertIs(runtime.handlers["main"], handler)
        health = asyncio.run(handler({"kind": "health"}))
        self.assertEqual(health["operations"], ["health", "plan_feature"])
        self.assertFalse(health["mutation_tools_exposed"])

        for payload in (
            {"command": "approve_migration", "actor_id": "person-a", "arguments": {}},
            {"command": "execute_migration", "actor_id": "person-a", "arguments": {}},
            {"message": "Choose a provider and execute it", "actor_id": "person-a"},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    asyncio.run(handler(payload))


if __name__ == "__main__":
    unittest.main()
