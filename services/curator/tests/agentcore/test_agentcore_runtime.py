from __future__ import annotations

import asyncio
import base64
import json
import os
import unittest
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any
from unittest.mock import patch

from bedrock_agentcore import RequestContext
from pydantic import ValidationError
from strands.models import Model

from feed_passport.agent.feature_intent_planner import (
    FeatureIntentPlanner,
    SafeFeatureCatalog,
)
from feed_passport.agent.feed_goal_planner import FeedGoalPlanner
from feed_passport.agent.model_provider import (
    AgentCoreBedrockModelConfig,
    ModelExecutionProfile,
)
from feed_passport.runtime.agentcore_app import create_agentcore_app
from feed_passport.runtime.agentcore_models import RuntimeJwtSubjectResolver


class ScriptedFeatureModel(Model):
    """No-network model that drives the real Strands proposal tool loop."""

    def __init__(self, calls: list[tuple[str, dict[str, Any]]]) -> None:
        self.calls = calls
        self.seen_tool_specs: list[set[str]] = []
        self._config: dict[str, Any] = {
            "model_id": "scripted-feature-planner",
            "context_window_limit": 4096,
        }

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return dict(self._config)

    async def structured_output(
        self,
        output_model: type[Any],
        prompt: list[dict[str, Any]],
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        if False:
            yield {"output": output_model()}

    def stream(
        self,
        messages: list[dict[str, Any]],
        tool_specs: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[dict[str, Any]]:
        self.seen_tool_specs.append({str(item["name"]) for item in tool_specs or []})
        completed = sum(
            1
            for message in messages
            for block in message.get("content", [])
            if "toolResult" in block
        )
        if completed < len(self.calls):
            name, values = self.calls[completed]
            return self._tool_call(name, values, f"scripted-{completed + 1}")
        return self._text("The proposal was submitted for deterministic server review.")

    async def _tool_call(
        self,
        name: str,
        values: dict[str, Any],
        tool_use_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        yield {"messageStart": {"role": "assistant"}}
        yield {
            "contentBlockStart": {
                "start": {"toolUse": {"toolUseId": tool_use_id, "name": name}}
            }
        }
        yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(values)}}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 23, "outputTokens": 11, "totalTokens": 34},
                "metrics": {"latencyMs": 1},
            }
        }

    async def _text(self, value: str) -> AsyncIterator[dict[str, Any]]:
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {}}}
        yield {"contentBlockDelta": {"delta": {"text": value}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 23, "outputTokens": 11, "totalTokens": 34},
                "metrics": {"latencyMs": 1},
            }
        }


def scripted_calls(kind: str, details: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    submission_tool = {
        "migration": "submit_migration_proposal",
        "temporary_visa": "submit_temporary_visa_proposal",
        "companion_sync": "submit_companion_sync_proposal",
    }[kind]
    return [
        ("inspect_selected_passport", {}),
        ("inspect_safe_feature_catalog", {}),
        (submission_tool, details),
    ]


def _unsigned_runtime_token(*, subject: str) -> str:
    def encoded(value: dict[str, Any]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return ".".join(
        (
            encoded({"alg": "RS256", "typ": "JWT"}),
            encoded(
                {
                    "sub": subject,
                    "iss": "https://cognito-idp.eu-central-1.amazonaws.com/test-pool",
                    "token_use": "access",
                }
            ),
            "runtime-validated-signature",
        )
    )


def _request_context(*, subject: str) -> RequestContext:
    return RequestContext(
        request_headers={"Authorization": f"Bearer {_unsigned_runtime_token(subject=subject)}"}
    )


def _passport_payload(*, owner_id: str = "cognito-user-123") -> dict[str, Any]:
    return {
        "id": "passport-version-3",
        "owner_id": owner_id,
        "name": "Research Passport",
        "version": 3,
        "intent": "Prefer careful research and deliberately chosen creators.",
        "topic_targets": {"research": 0.7, "engineering": 0.3},
        "creator_preferences": {"paper-lab": 0.8},
        "format_preferences": {"long_form": 0.9},
        "languages": ["en"],
        "hard_exclusions": ["ragebait"],
        "serendipity": 0.18,
        "max_outrage": 0.04,
        "max_source_share": 0.3,
    }


def _catalog() -> SafeFeatureCatalog:
    return SafeFeatureCatalog(
        migration_destinations=(
            {
                "destination_id": "youtube",
                "evidence_level": "guided",
                "execute_mode": "guided",
                "limitations": (
                    "Requires review in the platform's official interface.",
                ),
            },
        ),
        temporary_visa_modes=("isolated", "reversible_live"),
        companion_field_categories=(
            "topics",
            "creators",
            "formats",
            "exclusions",
            "serendipity",
        ),
        companion_strategies=("common_ground", "taste_swap", "bridge", "weighted"),
    )


class AgentCoreBedrockConfigurationTests(unittest.TestCase):
    def test_bedrock_provider_is_explicit_and_truthfully_metered(self) -> None:
        config = AgentCoreBedrockModelConfig.from_values(
            model_id="eu.amazon.nova-lite-v1:0",
            region_name="eu-central-1",
            timeout_seconds=30,
        )

        self.assertEqual(config.endpoint_scope, "aws_bedrock")
        self.assertEqual(
            config.execution_profile,
            ModelExecutionProfile(
                provider="bedrock",
                model_id="eu.amazon.nova-lite-v1:0",
                endpoint_scope="aws_bedrock",
                external_model_calls=True,
                paid_model_calls=True,
            ),
        )
        self.assertTrue(config.summary()["external_model_calls"])
        self.assertTrue(config.summary()["paid_model_calls"])

        with patch("feed_passport.agent.model_provider.BedrockModel") as bedrock_model:
            config.create_model()
        bedrock_model.assert_called_once_with(
            region_name="eu-central-1",
            model_id="eu.amazon.nova-lite-v1:0",
            max_tokens=600,
            temperature=0.0,
            streaming=True,
        )

    def test_bedrock_provider_has_no_region_or_model_fallback(self) -> None:
        for model_id, region_name in (("", "eu-central-1"), ("model", "")):
            with self.subTest(model_id=model_id, region_name=region_name):
                with self.assertRaises(ValueError):
                    AgentCoreBedrockModelConfig.from_values(
                        model_id=model_id,
                        region_name=region_name,
                    )

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "FEED_PASSPORT_BEDROCK_MODEL_ID"):
                AgentCoreBedrockModelConfig.from_env()

    def test_execution_profile_rejects_false_cloud_or_true_local_evidence(self) -> None:
        with self.assertRaises(ValueError):
            ModelExecutionProfile(
                provider="bedrock",
                model_id="model",
                endpoint_scope="aws_bedrock",
                external_model_calls=False,
                paid_model_calls=False,
            )
        with self.assertRaises(ValueError):
            ModelExecutionProfile(
                provider="scripted",
                model_id="model",
                endpoint_scope="scripted_no_network",
                external_model_calls=True,
                paid_model_calls=True,
            )


class AgentCoreRuntimeTests(unittest.TestCase):
    identity_resolver = RuntimeJwtSubjectResolver(
        expected_issuer="https://cognito-idp.eu-central-1.amazonaws.com/test-pool"
    )

    def _planner(self, model: ScriptedFeatureModel) -> FeatureIntentPlanner:
        return FeatureIntentPlanner(
            model_factory=lambda: model,
            provider="scripted_local",
            model_id="scripted-feature-planner",
            timeout_seconds=5,
            endpoint_scope="scripted_no_network",
        )

    @staticmethod
    def _feed_planner(model: ScriptedFeatureModel) -> FeedGoalPlanner:
        return FeedGoalPlanner(
            model_factory=lambda: model,
            execution_profile=ModelExecutionProfile.local(
                provider="scripted_local",
                model_id="scripted-feed-planner",
                endpoint_scope="scripted_no_network",
            ),
            timeout_seconds=5,
        )

    def test_async_plan_feature_runs_real_strands_without_mutation_tools(self) -> None:
        model = ScriptedFeatureModel(
            scripted_calls("temporary_visa", {"duration_minutes": 360, "mode": "isolated"})
        )
        application, handler = create_agentcore_app(
            planner_factory=lambda: self._planner(model),
            feature_catalog=_catalog(),
            identity_resolver=self.identity_resolver,
        )

        self.assertIs(application.handlers["main"], handler)
        response = asyncio.run(
            handler(
                {
                    "kind": "plan_feature",
                    "passport": _passport_payload(),
                    "request": "Give me a separate research feed for six hours.",
                },
                _request_context(subject="cognito-user-123"),
            )
        )

        self.assertEqual(response["kind"], "feature_proposal")
        self.assertEqual(response["proposal"]["kind"], "temporary_visa")
        self.assertEqual(response["evidence"]["authority"], "proposal_only")
        self.assertFalse(response["evidence"]["mutation_tools_exposed"])
        self.assertNotIn("actor_id", json.dumps(response))
        self.assertEqual(
            model.seen_tool_specs[0],
            {
                "inspect_selected_passport",
                "inspect_safe_feature_catalog",
                "submit_migration_proposal",
                "submit_temporary_visa_proposal",
                "submit_companion_sync_proposal",
            },
        )

    def test_runtime_derives_actor_from_validated_jwt_context(self) -> None:
        model = ScriptedFeatureModel(
            scripted_calls("migration", {"destination": "youtube"})
        )
        _, handler = create_agentcore_app(
            planner_factory=lambda: self._planner(model),
            feature_catalog=_catalog(),
            identity_resolver=self.identity_resolver,
        )

        with self.assertRaises(PermissionError):
            asyncio.run(
                handler(
                    {
                        "kind": "plan_feature",
                        "passport": _passport_payload(owner_id="different-user"),
                        "request": "Copy this Passport to YouTube.",
                    },
                    _request_context(subject="cognito-user-123"),
                )
            )
        with self.assertRaises(PermissionError):
            asyncio.run(
                handler(
                    {
                        "kind": "plan_feature",
                        "passport": _passport_payload(),
                        "request": "Copy this Passport to YouTube.",
                    },
                    RequestContext(request_headers={}),
                )
            )

    def test_plan_feed_runs_strands_on_sanitized_evidence_without_mutation_authority(self) -> None:
        model = ScriptedFeatureModel(
            [
                ("inspect_selected_passport", {}),
                ("inspect_sanitized_evidence", {}),
                (
                    "submit_feed_goal_proposal",
                    {
                        "target_topic_weights": {
                            "pet_science": 0.5,
                            "cute_drawing": 0.3,
                            "exploration": 0.2,
                        },
                        "hard_exclusions": ["ragebait"],
                        "rationale": (
                            "The explicit percentages are preserved and the selected sample "
                            "supports pet science and drawing interests."
                        ),
                    },
                ),
            ]
        )
        _, handler = create_agentcore_app(
            planner_factory=lambda: self._planner(
                ScriptedFeatureModel(scripted_calls("migration", {"destination": "youtube"}))
            ),
            feed_planner_factory=lambda: self._feed_planner(model),
            feature_catalog=_catalog(),
            identity_resolver=self.identity_resolver,
        )
        response = asyncio.run(
            handler(
                {
                    "kind": "plan_feed",
                    "passport": _passport_payload(),
                    "request": (
                        "Reduce ragebait. Make this 60% pet science and 20% cute drawing; "
                        "keep the remainder exploratory."
                    ),
                    "evidence": [
                        {
                            "platform": "bluesky",
                            "metadata_source": "bluesky_public_appview",
                            "metadata_verified": True,
                            "title": "A veterinary study of cat behavior",
                            "description": "Calm pet science with a hand-drawn explainer.",
                            "inferred_topics": ["pet_science", "cute_drawing"],
                            "ragebait_signal": False,
                            "confidence": 0.84,
                        }
                    ],
                },
                _request_context(subject="cognito-user-123"),
            )
        )

        self.assertEqual(response["kind"], "feed_goal_proposal")
        self.assertEqual(response["proposal"]["target_topic_weights"]["pet_science"], 0.6)
        self.assertEqual(response["proposal"]["target_topic_weights"]["cute_drawing"], 0.2)
        self.assertTrue(response["evidence"]["explicit_percentages_enforced"])
        self.assertFalse(response["evidence"]["mutation_tools_exposed"])
        self.assertFalse(response["evidence"]["links_transmitted"])
        self.assertFalse(response["evidence"]["account_identifier_fields_transmitted"])
        self.assertNotIn("owner_id", json.dumps(response))
        self.assertEqual(
            model.seen_tool_specs[0],
            {
                "inspect_selected_passport",
                "inspect_sanitized_evidence",
                "submit_feed_goal_proposal",
            },
        )
    def test_runtime_rejects_mutation_commands_free_text_and_actor_spoofing(self) -> None:
        _, handler = create_agentcore_app(
            planner_factory=lambda: self._planner(
                ScriptedFeatureModel(scripted_calls("migration", {"destination": "youtube"}))
            ),
            feature_catalog=_catalog(),
            identity_resolver=self.identity_resolver,
        )
        rejected = (
            {"kind": "execute_migration", "migration_id": "migration-1"},
            {"message": "Choose a provider and execute it for me."},
            {
                "kind": "plan_feature",
                "actor_id": "spoofed-user",
                "passport": _passport_payload(),
                "request": "Copy this Passport to YouTube.",
            },
            {
                "kind": "plan_feed",
                "passport": _passport_payload(),
                "request": "Prefer calm research.",
                "evidence": [
                    {
                        "platform": "youtube",
                        "metadata_source": "youtube_data_api_v3",
                        "metadata_verified": True,
                        "title": "A source containing https://private.example/path",
                        "description": "",
                        "inferred_topics": ["research"],
                        "ragebait_signal": False,
                        "confidence": 0.8,
                    }
                ],
            },
            {"command": "approve_migration", "arguments": {}},
        )
        for payload in rejected:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    asyncio.run(handler(payload, _request_context(subject="cognito-user-123")))

    def test_health_is_network_free_and_reports_no_mutation_surface(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            _, handler = create_agentcore_app()
            response = asyncio.run(handler({"kind": "health"}))

        self.assertEqual(response["status"], "healthy")
        self.assertEqual(response["operations"], ["health", "plan_feature", "plan_feed"])
        self.assertFalse(response["mutation_tools_exposed"])
        self.assertFalse(response["model"]["configured"])


if __name__ == "__main__":
    unittest.main()
