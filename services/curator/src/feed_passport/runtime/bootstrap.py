from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from feed_passport.adapters.lab import LabAdapter
from feed_passport.agent import (
    ConsentBroker,
    CuratorAgentService,
    FeatureIntentPlanner,
    LocalModelProviderConfig,
    MissionPlanner,
)
from feed_passport.application import CuratorApplication
from feed_passport.infrastructure import SQLiteStore


@dataclass(slots=True)
class ServiceBundle:
    store: SQLiteStore
    application: CuratorApplication
    broker: ConsentBroker
    agent_service: CuratorAgentService
    model_provider: LocalModelProviderConfig = field(
        default_factory=lambda: LocalModelProviderConfig.from_values(
            provider="disabled",
            base_url="http://127.0.0.1:8080",
            model_id="unused",
        )
    )
    mission_planner: MissionPlanner | None = None
    feature_intent_planner: FeatureIntentPlanner | None = None

    def close(self) -> None:
        self.store.close()


def build_service_bundle(
    *,
    database_path: str | Path | None = None,
    consent_secret: str | bytes | None = None,
    portable_trusted_secrets: tuple[str | bytes, ...] = (),
    seed_demo: bool = True,
) -> ServiceBundle:
    configured_path = database_path or os.getenv("FEED_PASSPORT_DB_PATH")
    path = Path(configured_path) if configured_path else Path(tempfile.gettempdir()) / "feed-passport-curator.db"
    store = SQLiteStore(path)
    lab = LabAdapter()
    adapters = {lab.platform: lab}

    # Local platform twins are deterministic, account-free Lab adapters. Keep
    # the import optional so the core service can still start while a packaging
    # subset is being assembled; mission preview then reports the missing
    # twin:<platform> registration explicitly instead of falling back to a live
    # or guided adapter.
    if seed_demo:
        try:
            from feed_passport.adapters.twin import build_twin_adapters

            adapters.update(build_twin_adapters())
        except ImportError:
            pass

    # External capability adapters are optional at import time so the deterministic
    # Lab remains independently runnable during local development and tests.
    try:
        from feed_passport.adapters.platforms import build_platform_adapters

        adapters.update(build_platform_adapters())
    except ImportError:
        pass

    configured_secret = consent_secret or os.getenv("FEED_PASSPORT_CONSENT_SECRET")
    if configured_secret is None:
        configured_secret = store.get_or_create_secret("deployment-signing-v1")
    application = CuratorApplication(
        store=store,
        adapters=adapters,
        portable_secret=configured_secret,
        portable_trusted_secrets=portable_trusted_secrets,
    )
    if seed_demo and not application.list_passports():
        application.create_passport(
            owner_id="demo-owner",
            name="My useful internet",
            intent="Research, independent work, thoughtful design, and local culture without ragebait.",
            topic_targets={"research": 0.4, "indie": 0.25, "design": 0.2, "local": 0.15},
            creator_preferences={"studio-a": 1.0, "paper-lab": 1.0, "rage-farm": -1.0},
            format_preferences={"longform": 0.9, "short_video": -0.8},
            hard_exclusions=frozenset({"ragebait"}),
            serendipity=0.2,
            max_outrage=0.05,
            max_source_share=0.4,
        )
    broker = ConsentBroker(
        application,
        secret=configured_secret,
    )
    agent_service = CuratorAgentService(application, broker)
    model_provider = LocalModelProviderConfig.from_env()
    mission_planner = (
        MissionPlanner(
            application,
            agent_service.mission_runner,
            model_factory=model_provider.create_model,
            provider=model_provider.provider,
            model_id=model_provider.model_id,
            timeout_seconds=model_provider.timeout_seconds,
            endpoint_scope=model_provider.endpoint_scope,
        )
        if model_provider.configured
        else None
    )
    feature_intent_planner = (
        FeatureIntentPlanner(
            model_factory=model_provider.create_model,
            provider=model_provider.provider,
            model_id=model_provider.model_id,
            timeout_seconds=model_provider.timeout_seconds,
            endpoint_scope=model_provider.endpoint_scope,
        )
        if model_provider.configured
        else None
    )
    return ServiceBundle(
        store=store,
        application=application,
        broker=broker,
        agent_service=agent_service,
        model_provider=model_provider,
        mission_planner=mission_planner,
        feature_intent_planner=feature_intent_planner,
    )
