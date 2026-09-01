from __future__ import annotations

import os
from typing import Any

from feed_passport.agent.feature_intent_planner import (
    CompanionFieldCategory,
    CompanionStrategy,
    FeatureIntentPlanner,
    SafeFeatureCatalog,
    TemporaryVisaMode,
)
from feed_passport.agent.model_provider import AgentCoreBedrockModelConfig


_GUIDED_DESTINATIONS = (
    "bluesky",
    "facebook",
    "instagram",
    "linkedin",
    "reddit",
    "snapchat",
    "threads",
    "tiktok",
    "x",
    "youtube",
)
_GUIDED_LIMITATIONS = (
    "Credential-free planning only; actions require review in the platform's official interface.",
    "No synthetic snapshot is evidence of private ranking behavior or ranking fidelity.",
)


def default_agentcore_feature_catalog() -> SafeFeatureCatalog:
    """Return the server-owned, non-identifying proposal catalog.

    Every external platform remains honestly ``guided``. The deterministic Lab
    is the only local executable proof environment; this catalog grants no
    account access, consent, approval, or mutation authority.
    """

    guided = tuple(
        {
            "destination_id": destination,
            "evidence_level": "guided",
            "execute_mode": "guided",
            "limitations": _GUIDED_LIMITATIONS,
        }
        for destination in _GUIDED_DESTINATIONS
    )
    lab = {
        "destination_id": "feed_passport_lab",
        "evidence_level": "lab",
        "execute_mode": "lab",
        "limitations": (
            "Synthetic deterministic proof environment; no live-platform integration is claimed.",
            "Fixture behavior is not evidence of a private platform ranking system.",
        ),
    }
    return SafeFeatureCatalog(
        migration_destinations=(*guided, lab),
        temporary_visa_modes=(
            TemporaryVisaMode.ISOLATED,
            TemporaryVisaMode.REVERSIBLE_LIVE,
        ),
        companion_field_categories=tuple(CompanionFieldCategory),
        companion_strategies=tuple(CompanionStrategy),
    )


def build_bedrock_feature_planner() -> FeatureIntentPlanner:
    """Build the real Strands planner without invoking the configured model."""

    config = AgentCoreBedrockModelConfig.from_env()
    return FeatureIntentPlanner(
        model_factory=config.create_model,
        provider=config.execution_profile.provider,
        model_id=config.model_id,
        timeout_seconds=config.timeout_seconds,
        endpoint_scope=config.endpoint_scope,
        execution_profile=config.execution_profile,
    )


def bedrock_configuration_status() -> dict[str, Any]:
    """Describe configuration without creating a boto client or calling AWS."""

    required = (
        "FEED_PASSPORT_BEDROCK_MODEL_ID",
        "FEED_PASSPORT_BEDROCK_REGION",
    )
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        return {
            "configured": False,
            "provider": "bedrock",
            "model_id": None,
            "region": None,
            "endpoint_scope": "aws_bedrock",
            "external_model_calls": True,
            "paid_model_calls": True,
            "missing": missing,
            "reason": (
                "Planning is disabled. A future configured invocation would be external and "
                "potentially billable."
            ),
        }
    try:
        return AgentCoreBedrockModelConfig.from_env().summary()
    except (RuntimeError, ValueError) as exc:
        return {
            "configured": False,
            "provider": "bedrock",
            "model_id": None,
            "region": None,
            "endpoint_scope": "aws_bedrock",
            "external_model_calls": True,
            "paid_model_calls": True,
            "missing": [],
            "reason": f"Planning is disabled because the explicit configuration is invalid: {exc}",
        }
