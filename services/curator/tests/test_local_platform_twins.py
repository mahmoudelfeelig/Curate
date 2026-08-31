from __future__ import annotations

from datetime import UTC, datetime

import pytest

from feed_passport.adapters.lab import LabAdapter
from feed_passport.adapters.platforms import PLATFORM_PROFILES, UnsupportedPlatformAction
from feed_passport.adapters.twin import (
    PUBLIC_ENGAGEMENT_ACTIONS,
    LocalPlatformTwinAdapter,
    build_twin_adapters,
    twin_platform_id,
)
from feed_passport.domain import (
    ActionReceipt,
    ActionStatus,
    ActionType,
    CapabilityLevel,
    FeedPassport,
    ProposedAction,
)


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def _passport() -> FeedPassport:
    return FeedPassport(
        id="passport-local-twin",
        owner_id="person-1",
        name="Research without ragebait",
        version=2,
        intent="Prefer research and design while preserving selected creators.",
        topic_targets={"research": 0.65, "design": 0.35},
        creator_preferences={"paper-lab": 1.0, "rage-farm": -1.0},
        format_preferences={"longform": 0.8},
        hard_exclusions=frozenset({"ragebait"}),
        serendipity=0.25,
        max_outrage=0.03,
        max_source_share=0.2,
        created_at=NOW,
        updated_at=NOW,
    )


def _manual_action(
    action_type: ActionType,
    *,
    target: str = "ragebait",
    parameters: dict[str, object] | None = None,
) -> ProposedAction:
    return ProposedAction(
        id=f"manual-{action_type.value}",
        destination_id="destination-new",
        action_type=action_type,
        target=target,
        reason="Exercise the isolated local control twin.",
        idempotency_key=f"manual-key-{action_type.value}",
        reversible=True,
        parameters=parameters or {},
    )


def test_registry_uses_explicit_twin_ids_for_every_profile() -> None:
    adapters = build_twin_adapters()

    assert set(adapters) == {f"twin:{platform}" for platform in PLATFORM_PROFILES}
    assert all(key == adapter.platform for key, adapter in adapters.items())
    assert all(isinstance(adapter, LocalPlatformTwinAdapter) for adapter in adapters.values())
    assert twin_platform_id(" X ") == "twin:x"


@pytest.mark.parametrize("platform", sorted(PLATFORM_PROFILES))
def test_manifest_is_lab_only_and_limited_to_profile_controls(platform: str) -> None:
    adapter = build_twin_adapters()[twin_platform_id(platform)]
    profile = PLATFORM_PROFILES[platform]
    lab_controls = LabAdapter().capabilities("destination-new").execute

    manifest = adapter.capabilities("destination-new")
    expected = (profile.official_actions | profile.native_handoff_actions) & lab_controls
    expected -= PUBLIC_ENGAGEMENT_ACTIONS

    assert manifest.platform == f"twin:{platform}"
    assert manifest.level is CapabilityLevel.LAB
    assert manifest.execute == expected
    assert manifest.rollback == expected
    assert not manifest.execute & PUBLIC_ENGAGEMENT_ACTIONS
    assert manifest.requires_user_handoff == frozenset()
    assert manifest.evidence_url is not None
    assert "simulation-only" in manifest.evidence_url
    assert "no-ranking-fidelity" in manifest.evidence_url


@pytest.mark.parametrize("platform", sorted(PLATFORM_PROFILES))
def test_compiler_never_exceeds_the_twin_manifest_or_claims_ranking_fidelity(platform: str) -> None:
    adapter = build_twin_adapters()[twin_platform_id(platform)]
    observation = adapter.observe("destination-new", now=NOW)

    plan = adapter.compile(_passport(), observation, now=NOW)
    manifest = adapter.capabilities("destination-new")

    assert plan.capability_level is CapabilityLevel.LAB
    assert {action.action_type for action in plan.actions} <= manifest.execute
    assert all(action.parameters["simulation"] is True for action in plan.actions)
    assert all(action.parameters["ranking_fidelity"] == "not_claimed" for action in plan.actions)
    assert all(action.parameters["evidence_scope"] == "control_semantics_only" for action in plan.actions)
    assert any(loss.field == "ranking_fidelity" for loss in plan.losses)
    assert any("deterministic" in loss.reason.lower() for loss in plan.losses)


def test_clone_observe_sample_and_execute_are_deterministic_and_isolated() -> None:
    adapter = build_twin_adapters()["twin:tiktok"]
    observation = adapter.observe("destination-new", now=NOW)
    plan = adapter.compile(_passport(), observation, now=NOW)
    topic_action = next(
        action for action in plan.actions if action.action_type is ActionType.SET_TOPIC_PREFERENCE
    )
    original_state = adapter.export_state("destination-new")
    original_fingerprint = adapter.state_fingerprint("destination-new")

    first = adapter.clone()
    second = adapter.clone()
    assert first.state_fingerprint("destination-new") == original_fingerprint
    assert second.state_fingerprint("destination-new") == original_fingerprint
    first_outcome = first.execute("destination-new", topic_action, now=NOW)
    second_outcome = second.execute("destination-new", topic_action, now=NOW)

    assert first_outcome == second_outcome
    assert first.observe("destination-new", now=NOW) == second.observe("destination-new", now=NOW)
    assert first.sample("destination-new", now=NOW) == second.sample("destination-new", now=NOW)
    assert adapter.export_state("destination-new") == original_state
    assert adapter.state_fingerprint("destination-new") == original_fingerprint
    assert first.export_state("destination-new") != original_state
    assert first.state_fingerprint("destination-new") != original_fingerprint


def test_allowed_control_is_idempotent_and_rolls_back_to_exact_state() -> None:
    adapter = build_twin_adapters()["twin:x"]
    action = _manual_action(ActionType.MUTE_KEYWORD)
    before = adapter.export_state("destination-new")
    before_fingerprint = adapter.state_fingerprint("destination-new")

    assert set(before_fingerprint) == {"kind", "schema", "algorithm", "digest", "scope"}
    assert before_fingerprint["schema"] == "local-twin-control-state/v1"
    assert before_fingerprint["algorithm"] == "sha256"
    assert before_fingerprint["scope"] == "deterministic_local_twin_only"
    assert len(before_fingerprint["digest"]) == 64
    assert not {
        "topic_affinity",
        "following",
        "muted_creators",
        "muted_keywords",
        "subscriptions",
        "lists",
        "custom_feeds",
        "serendipity",
        "source_cap",
    } & set(before_fingerprint)

    first = adapter.execute("destination-new", action, now=NOW)
    repeated = adapter.execute("destination-new", action, now=NOW)
    assert first is repeated
    assert first.status is ActionStatus.EXECUTED
    assert adapter.export_state("destination-new") != before
    assert adapter.state_fingerprint("destination-new") != before_fingerprint

    receipt = ActionReceipt(
        id="receipt-local-twin",
        passport_id="passport-local-twin",
        passport_version=2,
        destination_id="destination-new",
        outcomes=(first,),
        issued_at=NOW,
        trace_id="trace-local-twin",
        previous_checkpoint_id="checkpoint-local-twin",
    )
    rollback = adapter.rollback("destination-new", receipt, now=NOW)

    assert rollback.restored_actions == (action.id,)
    assert rollback.failed_actions == ()
    assert any("simulation" in caveat.lower() for caveat in rollback.caveats)
    assert any("ranking" in caveat.lower() for caveat in rollback.caveats)
    assert adapter.export_state("destination-new") == before
    assert adapter.state_fingerprint("destination-new") == before_fingerprint


@pytest.mark.parametrize("action_type", sorted(PUBLIC_ENGAGEMENT_ACTIONS, key=str))
def test_public_engagement_controls_are_rejected(action_type: ActionType) -> None:
    adapter = build_twin_adapters()["twin:x"]

    with pytest.raises(UnsupportedPlatformAction) as caught:
        adapter.execute("destination-new", _manual_action(action_type), now=NOW)

    assert caught.value.code == "public_engagement_forbidden"


def test_health_and_observation_are_explicitly_simulated() -> None:
    adapter = build_twin_adapters()["twin:instagram"]

    health = adapter.health(now=NOW)
    observation = adapter.observe("destination-new", now=NOW)

    assert health.healthy is True
    assert health.mode == "deterministic_local_control_twin"
    assert "simulation" in health.detail.lower()
    assert "not evidence" in health.detail.lower()
    assert "ranking" in health.detail.lower()
    assert observation.platform == "twin:instagram"
    assert observation.sample.platform == "twin:instagram"
