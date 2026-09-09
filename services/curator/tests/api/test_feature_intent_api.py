from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from feed_passport.adapters.lab import LabAdapter
from feed_passport.adapters.platforms import build_platform_adapters
from feed_passport.adapters.twin import build_twin_adapters
from feed_passport.agent import (
    ConsentBroker,
    CuratorAgentService,
    FeatureIntentPlannerError,
    FeatureIntentResult,
    SafeFeatureCatalog,
)
from feed_passport.api.app import create_app
from feed_passport.application import CuratorApplication
from feed_passport.domain import FeedPassport
from feed_passport.infrastructure import SQLiteStore
from feed_passport.runtime import ServiceBundle


class InjectedFeaturePlanner:
    """Proposal-only API boundary double; planner protocol has its own Strands tests."""

    def __init__(self) -> None:
        self.kind = "migration"
        self.calls: list[dict[str, Any]] = []
        self.failure: FeatureIntentPlannerError | None = None

    async def propose(
        self,
        *,
        actor_id: str,
        passport: FeedPassport,
        request: str,
        catalog: SafeFeatureCatalog,
    ) -> FeatureIntentResult:
        self.calls.append(
            {
                "actor_id": actor_id,
                "passport": passport,
                "request": request,
                "catalog": catalog,
            }
        )
        if self.failure is not None:
            raise self.failure
        if self.kind == "migration":
            capability = next(
                item
                for item in catalog.migration_destinations
                if item.destination_id == "youtube"
            )
            proposal = {
                "kind": "migration",
                "destination": "youtube",
                "goal": "Carry the selected Feed Passport preferences to YouTube.",
                "rationale": (
                    "YouTube is a guided handoff that requires manual review in official platform "
                    "controls. This proposal cannot change a live account."
                ),
                "capability": capability.model_dump(mode="json"),
            }
        elif self.kind == "temporary_visa":
            proposal = {
                "kind": "temporary_visa",
                "purpose": "Keep a focused feed for a seven-day expedition.",
                "duration_minutes": 10_080,
                "mode": "isolated",
                "rationale": "The proposal stays within the temporary visa review window.",
            }
        else:
            proposal = {
                "kind": "companion_sync",
                "field_categories": ["topics", "formats", "serendipity"],
                "strategy": "bridge",
                "companion_input_percent": 30,
                "duration_minutes": 43_200,
                "rationale": "The proposal requests bounded companion input without choosing a person.",
            }
        rationale = str(proposal["rationale"])
        submission_tool = {
            "migration": "submit_migration_proposal",
            "temporary_visa": "submit_temporary_visa_proposal",
            "companion_sync": "submit_companion_sync_proposal",
        }[self.kind]
        catalog_payload = json.dumps(
            catalog.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return FeatureIntentResult.model_validate(
            {
                "proposal": proposal,
                "evidence": {
                    "runtime": "strands",
                    "provider": "scripted_local",
                    "model_id": "injected-feature-api-planner",
                    "endpoint_scope": "scripted_no_network",
                    "external_model_calls": False,
                    "paid_model_calls": False,
                    "authority": "proposal_only",
                    "mutation_tools_exposed": False,
                    "proposal_text_source": "deterministic_server_templates",
                    "stop_reason": "end_turn",
                    "duration_ms": 1,
                    "cycles": 3,
                    "usage": {
                        "input_tokens": 12,
                        "output_tokens": 8,
                        "total_tokens": 20,
                    },
                    "tools": [
                        {"name": "inspect_selected_passport", "status": "completed"},
                        {"name": "inspect_safe_feature_catalog", "status": "completed"},
                        {"name": submission_tool, "status": "accepted"},
                    ],
                    "proposal_kind": proposal["kind"],
                    "proposal_text_digests": [
                        {
                            "field": "rationale",
                            "characters": len(rationale),
                            "sha256": hashlib.sha256(rationale.encode("utf-8")).hexdigest(),
                        }
                    ],
                    "catalog_sha256": hashlib.sha256(catalog_payload).hexdigest(),
                    "locked_by_server": [
                        "actor",
                        "passport_id_and_version",
                        "destination_and_account_resources",
                        "companion_participants_and_slices",
                        "consent",
                        "approval",
                        "execution",
                        "rollback",
                    ],
                },
            }
        )


class FeatureIntentAPITests(unittest.TestCase):
    PROJECTION_KINDS = (
        "passports",
        "overlays",
        "shares",
        "companions",
        "checkpoints",
        "migrations",
        "receipts",
        "drift_alerts",
        "drift_monitors",
        "creator_links",
        "agent_missions",
        "adapter_accounts",
    )

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        store = SQLiteStore(Path(self.temp.name) / "feature-api.db")
        lab = LabAdapter()
        youtube = build_platform_adapters()["youtube"]
        twin = build_twin_adapters()["twin:x"]
        application = CuratorApplication(
            store=store,
            adapters={
                lab.platform: lab,
                youtube.platform: youtube,
                twin.platform: twin,
            },
        )
        passport = application.create_passport(
            owner_id="person-a",
            name="Useful internet",
            intent="Research, design, and local culture without ragebait.",
            topic_targets={"research": 0.5, "design": 0.3, "local": 0.2},
            creator_preferences={"paper-lab": 1.0, "rage-farm": -1.0},
            format_preferences={"longform": 0.9},
            hard_exclusions=frozenset({"ragebait"}),
            serendipity=0.2,
            max_source_share=0.4,
        )
        broker = ConsentBroker(application, secret="feature-api-secret")
        self.planner = InjectedFeaturePlanner()
        self.bundle = ServiceBundle(
            store=store,
            application=application,
            broker=broker,
            agent_service=CuratorAgentService(application, broker),
            feature_intent_planner=self.planner,  # type: ignore[arg-type]
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

    def _stored_state(self) -> dict[str, Any]:
        return {
            "events": self.bundle.store.load_events(),
            "projections": {
                kind: self.bundle.store.list_projections(kind)
                for kind in self.PROJECTION_KINDS
            },
        }

    def _body(self, request: str) -> dict[str, str]:
        return {
            "actor_id": "person-a",
            "passport_id": self.passport_id,
            "request": request,
        }

    def test_three_feature_kinds_use_server_catalog_and_never_mutate_state(self) -> None:
        stored_before = self._stored_state()

        for kind in ("migration", "temporary_visa", "companion_sync"):
            with self.subTest(kind=kind):
                self.planner.kind = kind
                response = self.client.post(
                    "/api/agent/features/plan",
                    json=self._body(f"Propose a safe {kind} feature."),
                )
                self.assertEqual(response.status_code, 200)
                value = response.json()
                self.assertEqual(value["proposal"]["kind"], kind)
                self.assertEqual(value["evidence"]["authority"], "proposal_only")
                self.assertFalse(value["evidence"]["mutation_tools_exposed"])
                self.assertEqual(
                    value["evidence"]["proposal_text_source"],
                    "deterministic_server_templates",
                )
                self.assertEqual(
                    [item["name"] for item in value["evidence"]["tools"]],
                    [
                        "inspect_selected_passport",
                        "inspect_safe_feature_catalog",
                        {
                            "migration": "submit_migration_proposal",
                            "temporary_visa": "submit_temporary_visa_proposal",
                            "companion_sync": "submit_companion_sync_proposal",
                        }[kind],
                    ],
                )
                serialized = response.text.lower()
                self.assertNotIn("approval_token", serialized)
                self.assertNotIn("destination_account_id", serialized)
                self.assertNotIn("slice_ids", serialized)
                if kind == "migration":
                    expected_capability = next(
                        item
                        for item in self.planner.calls[-1]["catalog"].migration_destinations
                        if item.destination_id == "youtube"
                    ).model_dump(mode="json")
                    self.assertEqual(
                        value["proposal"]["capability"],
                        expected_capability,
                    )

        self.assertEqual(len(self.planner.calls), 3)
        for call in self.planner.calls:
            self.assertEqual(call["actor_id"], "person-a")
            self.assertEqual(call["passport"].id, self.passport_id)
            catalog = call["catalog"]
            self.assertEqual(
                [item.destination_id for item in catalog.migration_destinations],
                ["feed_passport_lab", "youtube"],
            )
            destinations = {
                item.destination_id: item for item in catalog.migration_destinations
            }
            self.assertEqual(destinations["feed_passport_lab"].evidence_level, "lab")
            self.assertEqual(destinations["feed_passport_lab"].execute_mode, "lab")
            self.assertEqual(destinations["youtube"].evidence_level, "guided")
            self.assertEqual(destinations["youtube"].execute_mode, "guided")
            self.assertTrue(destinations["feed_passport_lab"].limitations)
            self.assertTrue(destinations["youtube"].limitations)
            self.assertNotIn("twin:x", destinations)
            self.assertEqual(catalog.temporary_visa_min_minutes, 360)
            self.assertEqual(catalog.temporary_visa_max_minutes, 10_080)
            self.assertEqual(catalog.companion_min_input_percent, 10)
            self.assertEqual(catalog.companion_max_input_percent, 50)
            self.assertEqual(catalog.companion_min_minutes, 2_880)
            self.assertEqual(catalog.companion_max_minutes, 43_200)

        self.assertEqual(self._stored_state(), stored_before)

    def test_disabled_planner_returns_503_without_fallback(self) -> None:
        self.bundle.feature_intent_planner = None

        response = self.client.post(
            "/api/agent/features/plan",
            json=self._body("Propose a migration."),
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("no external or paid provider fallback", response.json()["detail"])
        self.assertEqual(self.planner.calls, [])

    def test_owner_and_missing_passport_fail_before_model_invocation(self) -> None:
        forbidden = self.client.post(
            "/api/agent/features/plan",
            json={**self._body("Propose a temporary visa."), "actor_id": "person-b"},
        )
        missing = self.client.post(
            "/api/agent/features/plan",
            json={**self._body("Propose a migration."), "passport_id": "missing-passport"},
        )

        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(self.planner.calls, [])

    def test_request_rejects_every_client_supplied_authority_field(self) -> None:
        response = self.client.post(
            "/api/agent/features/plan",
            json={
                **self._body("Propose a companion sync."),
                "destination_account_id": "private-account",
                "partner_id": "other-person",
                "catalog": {"migration_destinations": ["invented"]},
                "approval_token": "not-authorized",
                "execute": True,
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.planner.calls, [])

    def test_feature_planner_protocol_failure_uses_existing_gateway_mapping(self) -> None:
        self.planner.failure = FeatureIntentPlannerError("scripted protocol failure")

        response = self.client.post(
            "/api/agent/features/plan",
            json=self._body("Propose a migration."),
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"], "local_model_protocol_failed")


if __name__ == "__main__":
    unittest.main()
