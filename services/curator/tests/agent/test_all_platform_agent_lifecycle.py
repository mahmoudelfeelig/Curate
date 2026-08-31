from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from feed_passport.adapters.lab import LabAdapter
from feed_passport.adapters.platforms import PLATFORM_PROFILES, UnsupportedPlatformAction
from feed_passport.adapters.twin import (
    PUBLIC_ENGAGEMENT_ACTIONS,
    build_twin_adapters,
    twin_platform_id,
)
from feed_passport.application import AgentMissionRunner
from feed_passport.domain import (
    ActionType,
    AgentMissionAcceptance,
    AgentMissionBudget,
    ProposedAction,
)
from feed_passport.runtime import build_service_bundle


TWIN_PLATFORMS = (
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
IMPOSSIBLE_ACCEPTANCE = AgentMissionAcceptance(
    max_total_variation_distance=0.0,
    max_unwanted_rate=0.0,
    max_source_concentration=0.0,
    min_serendipity_rate=1.0,
)
NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def test_agent_lifecycle_matrix_covers_every_authoritative_twin_profile() -> None:
    assert TWIN_PLATFORMS == tuple(sorted(PLATFORM_PROFILES))


@pytest.mark.parametrize("platform", TWIN_PLATFORMS)
def test_complete_consented_agent_lifecycle_persists_and_rolls_back_exactly(
    platform: str,
    tmp_path: Path,
) -> None:
    bundle = build_service_bundle(
        database_path=tmp_path / f"{platform}-mission.db",
        consent_secret=f"{platform}-mission-test-secret",
        seed_demo=True,
    )
    try:
        passport = bundle.application.list_passports()[0]
        destination_twin = twin_platform_id(platform)
        adapter = bundle.application.adapters[destination_twin]
        before_fingerprint = adapter.state_fingerprint("destination-new")
        runner = AgentMissionRunner(bundle.application)

        preview = runner.preview(
            actor_id=passport.owner_id,
            goal=f"Exercise the complete consented {platform} local-twin lifecycle.",
            passport_id=passport.id,
            destination_twin=destination_twin,
            destination_account_id="destination-new",
            budget=AgentMissionBudget(
                total_actions=4,
                per_iteration_actions=2,
                max_iterations=2,
            ),
            acceptance=IMPOSSIBLE_ACCEPTANCE,
            min_improvement=0.0,
        )

        assert preview["status"] == "awaiting_approval"
        assert preview["pending_plan"]["action_count"] > 0
        assert preview["trace"][-1]["stage"] == "consent"
        assert preview["trace"][-1]["status"] == "required"
        assert adapter.state_fingerprint("destination-new") == before_fingerprint
        persisted_preview = AgentMissionRunner(bundle.application).get(
            str(preview["id"]),
            actor_id=passport.owner_id,
        )
        assert persisted_preview["approval_scope"] == preview["approval_scope"]

        execute_grant = bundle.broker.issue_for_mission(
            str(preview["id"]),
            actor_id=passport.owner_id,
        )
        consumed_scope = bundle.broker.consume(
            execute_grant.token,
            operation="execute_agent_mission",
            resource_id=str(preview["id"]),
            actor_id=passport.owner_id,
        )
        assert consumed_scope["operation"] == "execute_agent_mission"
        executed = AgentMissionRunner(bundle.application).execute(
            str(preview["id"]),
            actor_id=passport.owner_id,
        )

        assert executed["status"] == "needs_human"
        assert len(executed["iterations"]) == 2
        assert len(executed["receipt_ids"]) == 2
        assert executed["rollback_baseline"]["fingerprint"] == before_fingerprint
        assert adapter.state_fingerprint("destination-new") != before_fingerprint
        for receipt_id in executed["receipt_ids"]:
            persisted_receipt = bundle.application.projection_get("receipts", str(receipt_id))
            assert persisted_receipt["id"] == receipt_id
            assert persisted_receipt["destination_id"] == "destination-new"
            assert persisted_receipt["outcomes"]
            assert all(
                outcome["status"] == "executed" for outcome in persisted_receipt["outcomes"]
            )
        persisted_execution = AgentMissionRunner(bundle.application).get(
            str(preview["id"]),
            actor_id=passport.owner_id,
        )
        assert persisted_execution["receipt_ids"] == executed["receipt_ids"]

        rollback_grant = bundle.broker.issue_for_mission_rollback(
            str(preview["id"]),
            actor_id=passport.owner_id,
        )
        consumed_rollback_scope = bundle.broker.consume(
            rollback_grant.token,
            operation="rollback_agent_mission",
            resource_id=str(preview["id"]),
            actor_id=passport.owner_id,
        )
        assert consumed_rollback_scope["operation"] == "rollback_agent_mission"
        rolled_back = AgentMissionRunner(bundle.application).rollback(
            str(preview["id"]),
            actor_id=passport.owner_id,
        )

        assert rolled_back["status"] == "rolled_back"
        assert rolled_back["rollback"]["failure_count"] == 0
        assert rolled_back["rollback"]["receipt_order"] == list(
            reversed(executed["receipt_ids"])
        )
        assert all(
            result["status"] == "rolled_back"
            for result in rolled_back["rollback"]["results"]
        )
        verification = rolled_back["rollback"]["verification"]
        assert verification["status"] == "completed"
        assert verification["state_restored"] is True
        assert verification["expected_fingerprint"] == before_fingerprint
        assert verification["observed_fingerprint"] == before_fingerprint
        assert adapter.state_fingerprint("destination-new") == before_fingerprint
        for receipt_id in executed["receipt_ids"]:
            persisted_receipt = bundle.application.projection_get("receipts", str(receipt_id))
            assert persisted_receipt["status"] == "rolled_back"
    finally:
        bundle.close()


@pytest.mark.parametrize("platform", TWIN_PLATFORMS)
def test_each_twin_rejects_a_profile_undeclared_control(platform: str) -> None:
    adapter = build_twin_adapters()[twin_platform_id(platform)]
    manifest = adapter.capabilities("destination-new")
    lab_controls = LabAdapter().capabilities("destination-new").execute
    undeclared_controls = sorted(
        lab_controls - manifest.execute - PUBLIC_ENGAGEMENT_ACTIONS,
        key=lambda action_type: action_type.value,
    )
    assert undeclared_controls
    action_type: ActionType = undeclared_controls[0]
    before_fingerprint = adapter.state_fingerprint("destination-new")
    action = ProposedAction(
        id=f"unsupported-{platform}-{action_type.value}",
        destination_id="destination-new",
        action_type=action_type,
        target="unsupported-control-target",
        reason="Prove the local twin rejects a control absent from its profile.",
        idempotency_key=f"unsupported-{platform}-{action_type.value}",
        reversible=True,
        parameters={},
    )

    with pytest.raises(UnsupportedPlatformAction) as caught:
        adapter.execute("destination-new", action, now=NOW)

    assert caught.value.code == "twin_control_unavailable"
    assert caught.value.platform == twin_platform_id(platform)
    assert caught.value.action_type is action_type
    assert adapter.state_fingerprint("destination-new") == before_fingerprint
