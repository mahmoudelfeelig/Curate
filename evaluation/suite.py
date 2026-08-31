from __future__ import annotations

import json
import sys
import tempfile
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


# Keep the repository-local evaluator runnable without installing the service.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_CURATOR_SOURCE = _REPOSITORY_ROOT / "services" / "curator" / "src"
if str(_CURATOR_SOURCE) not in sys.path:
    sys.path.insert(0, str(_CURATOR_SOURCE))

from feed_passport.adapters.lab import LabAdapter  # noqa: E402
from feed_passport.adapters.platforms import PLATFORM_PROFILES  # noqa: E402
from feed_passport.adapters.twin import (  # noqa: E402
    PUBLIC_ENGAGEMENT_ACTIONS,
    build_twin_adapters,
)
from feed_passport.agent import ConsentBroker, ConsentError  # noqa: E402
from feed_passport.application import AgentMissionRunner, CuratorApplication  # noqa: E402
from feed_passport.application.curator import InvalidStateError  # noqa: E402
from feed_passport.domain import (  # noqa: E402
    ActionEnvelope,
    AgentMissionAcceptance,
    AgentMissionBudget,
    ActionStatus,
    ActionType,
    CapabilityLevel,
    OverlayMode,
    PolicyGuard,
    ProposedAction,
)
from feed_passport.infrastructure import SQLiteStore  # noqa: E402


EVALUATION_TIME = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)
SCENARIO_IDS = (
    "passport_portability_and_checkpoints",
    "migration_quality",
    "policy_safety",
    "rollback_fidelity",
    "temporary_overlay_isolation_expiry",
    "temporary_visa_early_revoke",
    "consent_scoped_companion_blending",
    "idempotency",
    "capability_honesty",
    "local_platform_twin_matrix",
    "bounded_agent_mission_lifecycle",
    "drift_detection",
    "scheduled_drift_monitor",
    "creator_continuity",
)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class IncrementingIds:
    def __init__(self) -> None:
        self._value = 0

    def __call__(self) -> str:
        self._value += 1
        return f"{self._value:04d}"


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    id: str
    title: str
    checks: Mapping[str, bool]
    metrics: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        checks = dict(self.checks)
        return {
            "id": self.id,
            "title": self.title,
            "status": "passed" if all(checks.values()) else "failed",
            "checks": checks,
            "metrics": dict(self.metrics),
        }


class EvaluationHarness(AbstractContextManager["EvaluationHarness"]):
    def __init__(self, scenario_dir: Path) -> None:
        self.clock = MutableClock(EVALUATION_TIME)
        self.ids = IncrementingIds()
        self.store = SQLiteStore(scenario_dir / "state.db")
        self.adapter = LabAdapter()
        self.twins = build_twin_adapters()
        self.app = CuratorApplication(
            store=self.store,
            adapters={self.adapter.platform: self.adapter, **self.twins},
            clock=self.clock,
            id_factory=self.ids,
        )
        self.passport = self.app.create_passport(
            owner_id="person-a",
            name="Portable signal",
            intent="Research, independent games, design, and local culture without ragebait.",
            topic_targets={"research": 0.4, "indie": 0.25, "design": 0.2, "local": 0.15},
            creator_preferences={"studio-a": 1.0, "rage-farm": -1.0},
            format_preferences={"longform": 0.9},
            hard_exclusions=frozenset({"ragebait"}),
            serendipity=0.2,
            max_outrage=0.05,
            max_source_share=0.4,
        )

    def __enter__(self) -> "EvaluationHarness":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.store.close()


def _rounded(value: Any) -> float:
    return round(float(value), 6)


def _passport_portability_and_checkpoints(root: Path) -> ScenarioResult:
    with EvaluationHarness(root / "passport-portability") as harness:
        checkpoint = harness.app.create_checkpoint(
            harness.passport.id,
            actor_id="person-a",
            label="Before conference",
        )
        revised = harness.app.revise_passport(
            harness.passport.id,
            actor_id="person-a",
            changes={"topic_targets": {"research": 0.2, "design": 0.8}},
        )
        restored = harness.app.restore_checkpoint(checkpoint["id"], actor_id="person-a")
        exported = harness.app.export_passport(restored.id)
        imported = harness.app.import_passport(
            actor_id="person-importer",
            format_name="feed-passport/v1",
            passport_data=exported,
        )
        imported_passport = imported["passport"]
        exported_json = json.dumps(exported, sort_keys=True)
        portable_intent_preserved = (
            imported_passport["name"] == restored.name
            and imported_passport["intent"] == restored.intent
            and imported_passport["topic_targets"] == dict(restored.topic_targets)
            and imported_passport["creator_preferences"]
            == dict(restored.creator_preferences)
            and imported_passport["format_preferences"]
            == dict(restored.format_preferences)
            and imported_passport["languages"] == list(restored.languages)
            and set(imported_passport["hard_exclusions"])
            == set(restored.hard_exclusions)
            and imported_passport["serendipity"] == restored.serendipity
            and imported_passport["max_outrage"] == restored.max_outrage
            and imported_passport["max_source_share"] == restored.max_source_share
        )
        unsafe_rejected = False
        try:
            harness.app.import_passport(
                actor_id="person-a",
                format_name="feed-passport/v1",
                passport_data={**exported, "raw_history": ["private-event"]},
            )
        except ValueError:
            unsafe_rejected = True
        return ScenarioResult(
            id="passport_portability_and_checkpoints",
            title="Portable intent round-trips while checkpoints remain append-only",
            checks={
                "revision_changed_policy": dict(revised.topic_targets)
                != dict(harness.passport.topic_targets),
                "restore_created_new_version": restored.version == revised.version + 1,
                "restore_matches_checkpoint": dict(restored.topic_targets)
                == dict(harness.passport.topic_targets),
                "public_export_uses_versioned_contract": exported["schema_version"] == "1.0.0",
                "public_export_hides_local_identity": restored.id not in exported_json
                and restored.owner_id not in exported_json,
                "import_created_new_identity": imported_passport["id"] != restored.id,
                "import_created_new_owner": imported_passport["owner_id"] == "person-importer",
                "import_preserved_portable_intent": portable_intent_preserved,
                "private_history_field_rejected": unsafe_rejected,
            },
            metrics={
                "checkpoint_version": checkpoint["passport_version"],
                "restored_version": restored.version,
                "imported_version": imported_passport["version"],
                "source_identifier_reused": imported_passport["id"] == restored.id,
                "source_owner_reused": imported_passport["owner_id"] == restored.owner_id,
                "portable_intent_preserved": portable_intent_preserved,
                "export_schema_version": exported["schema_version"],
            },
        )


def _migration_quality(root: Path) -> ScenarioResult:
    with EvaluationHarness(root / "migration-quality") as harness:
        prepared = harness.app.prepare_migration(
            passport_id=harness.passport.id,
            platform=harness.adapter.platform,
            destination_account_id="destination-new",
            actor_id="person-a",
        )
        executed = harness.app.execute_migration(prepared["id"], approved_by="person-a")
        before = _rounded(prepared["before"]["total_variation_distance"])
        preview = _rounded(prepared["preview"]["total_variation_distance"])
        after = _rounded(executed["after"]["total_variation_distance"])
        action_count = len(prepared["plan"]["actions"])
        return ScenarioResult(
            id="migration_quality",
            title="A migration measurably improves the destination feed",
            checks={
                "preview_was_generated": prepared["preview"] is not None,
                "plan_contains_actions": action_count > 0,
                "preview_improves_topic_distance": preview < before,
                "executed_feed_improves_topic_distance": after < before,
                "execution_matches_preview": after == preview,
                "receipt_was_issued": bool(executed["receipt_id"]),
            },
            metrics={
                "before_topic_distance": before,
                "preview_topic_distance": preview,
                "after_topic_distance": after,
                "improvement": _rounded(before - after),
                "planned_actions": action_count,
                "executed_actions": len(
                    harness.app.projection_get("receipts", executed["receipt_id"])["outcomes"]
                ),
            },
        )


def _policy_safety(root: Path) -> ScenarioResult:
    del root
    adapter = LabAdapter()
    capability = adapter.capabilities("destination-new")
    public_actions = tuple(
        sorted(
            (
                ActionType.LIKE,
                ActionType.COMMENT,
                ActionType.POST,
                ActionType.REPOST,
                ActionType.SEND_MESSAGE,
            ),
            key=lambda item: item.value,
        )
    )
    allowed_actions = frozenset((*public_actions, ActionType.FOLLOW_CREATOR))
    envelope = ActionEnvelope(
        id="envelope-policy-evaluation",
        passport_id="passport-policy-evaluation",
        passport_version=1,
        destination_ids=frozenset({"destination-new"}),
        allowed_actions=allowed_actions,
        max_total_actions=len(allowed_actions),
        max_per_type={action_type: 1 for action_type in allowed_actions},
        expires_at=EVALUATION_TIME + timedelta(minutes=15),
        approved_by="person-a",
        approved_at=EVALUATION_TIME,
        stop_conditions=("target reached", "budget exhausted", "human judgment"),
    )
    guard = PolicyGuard()

    def action(action_type: ActionType, index: int) -> ProposedAction:
        return ProposedAction(
            id=f"policy-action-{index}",
            destination_id="destination-new",
            action_type=action_type,
            target="studio-a",
            reason="Exercise the policy boundary.",
            idempotency_key=f"policy-key-{index}",
            reversible=True,
        )

    denials = {
        action_type.value: guard.check(
            action=action(action_type, index),
            envelope=envelope,
            capability=capability,
            prior_actions=(),
            now=EVALUATION_TIME + timedelta(minutes=1),
        )
        for index, action_type in enumerate(public_actions, start=1)
    }
    control = guard.check(
        action=action(ActionType.FOLLOW_CREATOR, 99),
        envelope=envelope,
        capability=capability,
        prior_actions=(),
        now=EVALUATION_TIME + timedelta(minutes=1),
    )
    return ScenarioResult(
        id="policy_safety",
        title="Public engagement stays forbidden even inside an approval envelope",
        checks={
            "all_public_actions_denied": all(not decision.allowed for decision in denials.values()),
            "denials_use_public_engagement_policy": all(
                decision.code == "public_engagement_forbidden" for decision in denials.values()
            ),
            "non_public_control_is_allowed": control.allowed and control.code == "allowed",
        },
        metrics={
            "denied_public_actions": sorted(denials),
            "denial_codes": {key: value.code for key, value in sorted(denials.items())},
            "allowed_control_action": ActionType.FOLLOW_CREATOR.value,
        },
    )


def _rollback_fidelity(root: Path) -> ScenarioResult:
    with EvaluationHarness(root / "rollback-fidelity") as harness:
        original_state = harness.adapter.export_state("destination-new")
        prepared = harness.app.prepare_migration(
            passport_id=harness.passport.id,
            platform=harness.adapter.platform,
            destination_account_id="destination-new",
            actor_id="person-a",
        )
        executed = harness.app.execute_migration(prepared["id"], approved_by="person-a")
        changed_state = harness.adapter.export_state("destination-new")
        receipt = harness.app.projection_get("receipts", executed["receipt_id"])
        rollback = harness.app.rollback_receipt(
            executed["receipt_id"],
            actor_id="person-a",
            platform=harness.adapter.platform,
        )
        restored_state = harness.adapter.export_state("destination-new")
        restored_count = len(rollback["rollback"]["restored_actions"])
        receipt_count = len(receipt["outcomes"])
        return ScenarioResult(
            id="rollback_fidelity",
            title="A receipt restores the exact pre-migration Lab state",
            checks={
                "migration_changed_state": changed_state != original_state,
                "rollback_has_no_failures": not rollback["rollback"]["failed_actions"],
                "all_executed_actions_restored": restored_count == receipt_count,
                "state_restored_exactly": restored_state == original_state,
            },
            metrics={
                "receipt_actions": receipt_count,
                "restored_actions": restored_count,
                "failed_actions": len(rollback["rollback"]["failed_actions"]),
                "state_restored_exactly": restored_state == original_state,
            },
        )


def _temporary_overlay_isolation_expiry(root: Path) -> ScenarioResult:
    with EvaluationHarness(root / "temporary-overlay") as harness:
        base_before = harness.app.get_passport(harness.passport.id)
        overlay = harness.app.create_overlay(
            base_passport_id=harness.passport.id,
            name="Three-hour sports detour",
            topic_adjustments={"sports": 0.45, "research": -0.2},
            add_exclusions=frozenset({"spoilers"}),
            remove_exclusions=frozenset(),
            starts_at=harness.clock(),
            expires_at=harness.clock() + timedelta(hours=3),
            mode=OverlayMode.ISOLATED,
            serendipity=0.35,
            max_outrage=0.03,
            actor_id="person-a",
        )
        active = harness.app.effective_passport(harness.passport.id)
        base_during = harness.app.get_passport(harness.passport.id)
        harness.clock.advance(timedelta(hours=3, seconds=1))
        first_expiry = harness.app.process_due_jobs()
        repeated_expiry = harness.app.process_due_jobs()
        effective_after = harness.app.effective_passport(harness.passport.id)
        base_after = harness.app.get_passport(harness.passport.id)
        overlay_after = harness.app.projection_get("overlays", overlay["id"])
        base_unchanged = base_before == base_during == base_after
        return ScenarioResult(
            id="temporary_overlay_isolation_expiry",
            title="An isolated temporary feed expires once without training the base Passport",
            checks={
                "active_overlay_changes_effective_topics": "sports" in active.topic_targets,
                "active_overlay_changes_effective_exclusions": "spoilers" in active.hard_exclusions,
                "base_passport_unchanged": base_unchanged,
                "expiry_processed_once": len(first_expiry) == 1 and not repeated_expiry,
                "overlay_marked_expired": overlay_after["status"] == "expired",
                "effective_passport_returns_to_base": effective_after == base_after,
            },
            metrics={
                "mode": overlay["mode"],
                "active_topics": sorted(active.topic_targets),
                "expiry_jobs_first_pass": len(first_expiry),
                "expiry_jobs_second_pass": len(repeated_expiry),
                "base_passport_unchanged": base_unchanged,
            },
        )


def _temporary_visa_early_revoke(root: Path) -> ScenarioResult:
    with EvaluationHarness(root / "temporary-early-revoke") as harness:
        overlay = harness.app.create_overlay(
            base_passport_id=harness.passport.id,
            name="Quiet afternoon",
            topic_adjustments={"learning": 0.25},
            add_exclusions=frozenset({"short_form"}),
            remove_exclusions=frozenset(),
            starts_at=harness.clock(),
            expires_at=harness.clock() + timedelta(hours=2),
            mode=OverlayMode.ISOLATED,
            serendipity=0.1,
            max_outrage=0.02,
            actor_id="person-a",
        )
        active = harness.app.effective_passport(harness.passport.id)
        revoked = harness.app.revoke_overlay(overlay["id"], actor_id="person-a")
        after = harness.app.effective_passport(harness.passport.id)
        harness.clock.advance(timedelta(hours=2, seconds=1))
        due = harness.app.process_due_jobs()
        return ScenarioResult(
            id="temporary_visa_early_revoke",
            title="A temporary visa can close early without changing the base Passport",
            checks={
                "visa_changed_effective_policy": "learning" in active.topic_targets,
                "revoke_closed_visa": revoked["status"] == "revoked",
                "effective_policy_returned_to_base": after
                == harness.app.get_passport(harness.passport.id),
                "scheduled_expiry_preserved_revocation": due[0]["status"] == "revoked",
                "revocation_is_idempotent": harness.app.revoke_overlay(
                    overlay["id"], actor_id="person-a"
                )["status"]
                == "revoked",
            },
            metrics={
                "mode": overlay["mode"],
                "final_status": harness.app.projection_get("overlays", overlay["id"])["status"],
                "scheduled_job_result": due[0]["status"],
            },
        )


def _consent_scoped_companion_blending(root: Path) -> ScenarioResult:
    with EvaluationHarness(root / "companion-blend") as harness:
        first_base_before = harness.app.get_passport(harness.passport.id)
        other = harness.app.create_passport(
            owner_id="person-b",
            name="Music and design",
            intent="Music and design discovery while keeping health interests private.",
            topic_targets={"music": 0.6, "design": 0.3, "private-health-topic": 0.1},
            creator_preferences={"radio-night": 1.0, "private-clinician": 0.9},
            hard_exclusions=frozenset({"private-condition"}),
        )
        expires_at = harness.clock() + timedelta(hours=8)
        first = harness.app.create_share_slice(
            passport_id=harness.passport.id,
            topic_names=("design", "indie"),
            creator_ids=("studio-a",),
            include_serendipity=True,
            include_formats=True,
            include_exclusions=True,
            expires_at=expires_at,
            actor_id="person-a",
            scope="continuous",
            refresh_on_revision=True,
            target_passport_ids=(harness.passport.id,),
            pair_id="pair-person-a-person-b",
            counterparty_owner_id="person-b",
        )
        second = harness.app.create_share_slice(
            passport_id=other.id,
            topic_names=("design", "music"),
            creator_ids=("radio-night",),
            include_serendipity=True,
            expires_at=expires_at,
            actor_id="person-b",
            scope="continuous",
            refresh_on_revision=True,
            target_passport_ids=(other.id,),
            pair_id="pair-person-a-person-b",
            counterparty_owner_id="person-a",
        )
        blend = harness.app.create_companion_blend(
            name="Weekend bridge",
            slice_ids=(first["id"], second["id"]),
            weights={"person-a": 1.0, "person-b": 1.0},
            strategy="bridge",
            expires_at=expires_at,
            actor_id="person-a",
            scope="continuous",
        )
        effective_before_revision = harness.app.effective_passport(harness.passport.id)
        private_fields = {"private-health-topic", "private-clinician", "private-condition", "rage-farm"}
        exposed_fields = (
            set(blend["topic_targets"])
            | set(blend["creator_preferences"])
            | set(blend["format_preferences"])
            | set(blend["hard_exclusions"])
        )
        leaked = sorted(private_fields & exposed_fields)
        harness.app.revise_passport(
            other.id,
            actor_id="person-b",
            changes={
                "topic_targets": {
                    "music": 0.2,
                    "design": 0.7,
                    "private-health-topic": 0.1,
                }
            },
        )
        synced = harness.app.projection_get("companions", blend["id"])
        effective_after_revision = harness.app.effective_passport(harness.passport.id)
        first_base_after_sync = harness.app.get_passport(harness.passport.id)
        harness.app.revoke_share_slice(first["id"], actor_id="person-a")
        invalidated = harness.app.projection_get("companions", blend["id"])
        effective_after_revoke = harness.app.effective_passport(harness.passport.id)
        revoked_consent_blocked = False
        try:
            harness.app.create_companion_blend(
                name="Invalid replay",
                slice_ids=(first["id"], second["id"]),
                weights={"person-a": 1.0, "person-b": 1.0},
                strategy="bridge",
                expires_at=expires_at,
                actor_id="person-a",
                scope="continuous",
            )
        except InvalidStateError:
            revoked_consent_blocked = True
        return ScenarioResult(
            id="consent_scoped_companion_blending",
            title="Continuous companion blending uses two explicit, revocable consents",
            checks={
                "only_selected_topics_are_present": set(blend["topic_targets"])
                == {"design", "indie", "music"},
                "only_selected_creators_are_present": set(blend["creator_preferences"])
                == {"studio-a", "radio-night"},
                "only_selected_formats_are_present": set(blend["format_preferences"])
                == {"longform"},
                "only_selected_exclusions_are_present": set(blend["hard_exclusions"])
                == {"ragebait"},
                "private_fields_do_not_leak": not leaked,
                "every_participant_has_consent": len(blend["consent_ids"])
                == len(blend["participant_ids"])
                == 2,
                "continuous_scope_is_explicit": blend["scope"] == "continuous"
                and blend["refresh_on_revision"] is True
                and blend["sync_revision"] == 1,
                "both_people_target_only_their_own_passport": set(blend["target_passport_ids"])
                == {harness.passport.id, other.id},
                "pair_and_counterparties_are_reciprocally_bound": blend["pair_id"]
                == first["pair_id"]
                == second["pair_id"]
                == "pair-person-a-person-b"
                and first["counterparty_owner_id"] == "person-b"
                and second["counterparty_owner_id"] == "person-a",
                "selected_revision_propagates": synced["sync_revision"] == 2
                and effective_before_revision.topic_targets["design"]
                != effective_after_revision.topic_targets["design"],
                "base_passport_remains_immutable": first_base_before == first_base_after_sync,
                "revocation_removes_effective_blend": invalidated["status"]
                == "consent_invalidated"
                and dict(effective_after_revoke.topic_targets)
                == dict(first_base_before.topic_targets),
                "revoked_consent_blocks_new_blend": revoked_consent_blocked,
            },
            metrics={
                "participants": sorted(blend["participant_ids"]),
                "shared_topics": sorted(blend["topic_targets"]),
                "shared_creators": sorted(blend["creator_preferences"]),
                "shared_formats": sorted(blend["format_preferences"]),
                "shared_exclusions": sorted(blend["hard_exclusions"]),
                "private_fields_leaked": leaked,
                "consent_receipts": len(blend["consent_ids"]),
                "scope": blend["scope"],
                "initial_sync_revision": blend["sync_revision"],
                "refreshed_sync_revision": synced["sync_revision"],
                "status_after_revoke": invalidated["status"],
            },
        )


def _idempotency(root: Path) -> ScenarioResult:
    with EvaluationHarness(root / "idempotency") as harness:
        observation = harness.adapter.observe("destination-new", now=harness.clock(), sample_size=24)
        plan = harness.adapter.compile(harness.passport, observation, now=harness.clock())
        action = plan.actions[0]
        state_before = harness.adapter.export_state("destination-new")
        first = harness.adapter.execute("destination-new", action, now=harness.clock())
        state_after_first = harness.adapter.export_state("destination-new")
        replay = harness.adapter.execute("destination-new", action, now=harness.clock())
        state_after_replay = harness.adapter.export_state("destination-new")
        collision_blocked = False
        try:
            harness.adapter.execute(
                "destination-new",
                replace(action, target=f"{action.target}-collision"),
                now=harness.clock(),
            )
        except ValueError:
            collision_blocked = True
        references = {first.platform_reference, replay.platform_reference}
        return ScenarioResult(
            id="idempotency",
            title="Replaying one approved action is exactly-once and collision-safe",
            checks={
                "first_execution_changes_state": state_after_first != state_before,
                "replay_returns_original_outcome": replay == first,
                "replay_does_not_change_state": state_after_replay == state_after_first,
                "key_collision_is_rejected": collision_blocked,
            },
            metrics={
                "action_type": action.action_type.value,
                "executions_recorded": len(references),
                "state_changed_on_replay": state_after_replay != state_after_first,
                "collision_rejected": collision_blocked,
            },
        )


def _capability_honesty(root: Path) -> ScenarioResult:
    del root
    adapter = LabAdapter()
    manifest = adapter.capabilities("destination-new")
    health = adapter.health(now=EVALUATION_TIME)
    public_actions = {
        ActionType.LIKE,
        ActionType.COMMENT,
        ActionType.POST,
        ActionType.REPOST,
        ActionType.SEND_MESSAGE,
    }
    return ScenarioResult(
        id="capability_honesty",
        title="The deterministic simulator declares Lab capability, never live access",
        checks={
            "capability_is_lab": manifest.level is CapabilityLevel.LAB,
            "lab_is_not_live_certified": manifest.certified_at is None,
            "evidence_is_labeled_as_lab": manifest.evidence_url == "docs://proof-lab",
            "rollback_is_subset_of_execution": manifest.rollback <= manifest.execute,
            "public_engagement_is_not_executable": not (public_actions & manifest.execute),
            "health_mode_is_deterministic_lab": health.mode == "deterministic_lab",
        },
        metrics={
            "declared_level": manifest.level.value,
            "certified_at": None,
            "evidence_url": manifest.evidence_url,
            "executable_controls": sorted(item.value for item in manifest.execute),
            "health_mode": health.mode,
        },
    )


def _local_platform_twin_matrix(root: Path) -> ScenarioResult:
    with EvaluationHarness(root / "local-platform-twin-matrix") as harness:
        details: dict[str, dict[str, Any]] = {}
        for twin_id, adapter in sorted(harness.twins.items()):
            account_id = "destination-new"
            manifest = adapter.capabilities(account_id)
            health = adapter.health(now=harness.clock())
            state_before = adapter.export_state(account_id)
            prepared = harness.app.prepare_migration(
                passport_id=harness.passport.id,
                platform=twin_id,
                destination_account_id=account_id,
                actor_id="person-a",
            )
            plan_actions = list(prepared["plan"]["actions"])
            planned_types = {ActionType(item["action_type"]) for item in plan_actions}
            executed = harness.app.execute_migration(
                prepared["id"],
                approved_by="person-a",
            )
            state_after = adapter.export_state(account_id)
            receipt = harness.app.projection_get("receipts", executed["receipt_id"])
            rollback = harness.app.rollback_receipt(
                executed["receipt_id"],
                actor_id="person-a",
                platform=twin_id,
            )
            state_restored = adapter.export_state(account_id)
            details[twin_id] = {
                "capability_level": manifest.level.value,
                "health_mode": health.mode,
                "planned_actions": len(plan_actions),
                "executed_actions": len(receipt["outcomes"]),
                "plan_within_manifest": planned_types <= manifest.execute,
                "public_engagement_executable": bool(
                    PUBLIC_ENGAGEMENT_ACTIONS & manifest.execute
                ),
                "state_changed": state_after != state_before,
                "rollback_failures": len(rollback["rollback"]["failed_actions"]),
                "state_restored_exactly": state_restored == state_before,
                "simulation_disclosed": (
                    manifest.evidence_url is not None
                    and "simulation-only" in manifest.evidence_url
                    and "no-ranking-fidelity" in manifest.evidence_url
                ),
            }

        expected_twins = {f"twin:{platform}" for platform in PLATFORM_PROFILES}
        return ScenarioResult(
            id="local_platform_twin_matrix",
            title="All ten platform control twins execute and roll back account-free",
            checks={
                "all_declared_platforms_have_twins": set(details) == expected_twins,
                "exactly_ten_twins_evaluated": len(details) == 10,
                "every_twin_is_lab_only": all(
                    item["capability_level"] == CapabilityLevel.LAB.value
                    for item in details.values()
                ),
                "every_twin_declares_simulation": all(
                    item["simulation_disclosed"] for item in details.values()
                ),
                "every_plan_stays_within_manifest": all(
                    item["plan_within_manifest"] for item in details.values()
                ),
                "public_engagement_stays_forbidden": not any(
                    item["public_engagement_executable"] for item in details.values()
                ),
                "every_twin_executes_a_control": all(
                    item["executed_actions"] > 0 for item in details.values()
                ),
                "every_twin_changes_local_state": all(
                    item["state_changed"] for item in details.values()
                ),
                "every_rollback_is_failure_free": all(
                    item["rollback_failures"] == 0 for item in details.values()
                ),
                "every_state_is_restored_exactly": all(
                    item["state_restored_exactly"] for item in details.values()
                ),
            },
            metrics={
                "platform_count": len(details),
                "platforms": sorted(details),
                "twins": details,
                "external_accounts_used": 0,
                "external_services_used": 0,
            },
        )


def _bounded_agent_mission_lifecycle(root: Path) -> ScenarioResult:
    with EvaluationHarness(root / "bounded-agent-mission") as harness:
        adapter = harness.twins["twin:x"]
        account_id = "destination-new"
        state_before = adapter.export_state(account_id)
        runner = AgentMissionRunner(harness.app)
        broker = ConsentBroker(harness.app, secret="offline-evaluation-mission-secret")
        mission = runner.preview(
            actor_id="person-a",
            goal="Improve the local X control twin within two reversible actions.",
            passport_id=harness.passport.id,
            destination_twin="twin:x",
            destination_account_id=account_id,
            budget=AgentMissionBudget(
                total_actions=2,
                per_iteration_actions=1,
                max_iterations=2,
            ),
            acceptance=AgentMissionAcceptance(
                max_total_variation_distance=0.0,
                max_unwanted_rate=0.0,
                max_source_concentration=0.0,
                min_serendipity_rate=1.0,
                max_serendipity_rate=1.0,
            ),
            min_improvement=0.0,
        )
        execution_grant = broker.issue_for_mission(mission["id"], actor_id="person-a")
        broker.consume(
            execution_grant.token,
            operation="execute_agent_mission",
            resource_id=mission["id"],
            actor_id="person-a",
        )
        replay_rejected = False
        try:
            broker.consume(
                execution_grant.token,
                operation="execute_agent_mission",
                resource_id=mission["id"],
                actor_id="person-a",
            )
        except ConsentError:
            replay_rejected = True
        executed = runner.execute(mission["id"], actor_id="person-a")
        state_after = adapter.export_state(account_id)
        rollback_grant = broker.issue_for_mission_rollback(
            mission["id"],
            actor_id="person-a",
        )
        broker.consume(
            rollback_grant.token,
            operation="rollback_agent_mission",
            resource_id=mission["id"],
            actor_id="person-a",
        )
        rolled_back = runner.rollback(mission["id"], actor_id="person-a")
        state_restored = adapter.export_state(account_id)
        submitted = sum(
            int(iteration["submitted_action_count"])
            for iteration in executed["iterations"]
        )
        stages = {item["stage"] for item in executed["trace"]}
        receipt_order = list(reversed(executed["receipt_ids"]))
        return ScenarioResult(
            id="bounded_agent_mission_lifecycle",
            title="A local agent mission observes, adapts, receipts, and rolls back",
            checks={
                "preview_requires_explicit_approval": mission["status"] == "awaiting_approval",
                "execution_approval_is_one_time": replay_rejected,
                "mission_reaches_a_terminal_state": executed["status"]
                in {"completed", "needs_human"},
                "total_action_budget_is_enforced": submitted <= 2,
                "per_iteration_budget_is_enforced": all(
                    int(iteration["submitted_action_count"]) <= 1
                    for iteration in executed["iterations"]
                ),
                "observable_loop_is_recorded": {
                    "observe",
                    "evaluate",
                    "plan",
                    "policy",
                    "consent",
                    "act",
                    "reobserve",
                    "adapt",
                    "receipt",
                }
                <= stages,
                "mission_changed_only_local_state": state_after != state_before,
                "mission_issued_receipts": len(executed["receipt_ids"]) == 2,
                "rollback_uses_reverse_receipt_order": rolled_back["rollback"][
                    "receipt_order"
                ]
                == receipt_order,
                "rollback_completed": rolled_back["status"] == "rolled_back",
                "rollback_restored_exact_state": state_restored == state_before,
            },
            metrics={
                "platform": "twin:x",
                "environment": mission["environment"],
                "iterations": len(executed["iterations"]),
                "submitted_actions": submitted,
                "receipt_count": len(executed["receipt_ids"]),
                "remaining_action_budget": executed["remaining_action_budget"],
                "stop_reason": executed["stop_reason"],
                "rollback_receipt_count": len(receipt_order),
                "state_restored_exactly": state_restored == state_before,
                "external_accounts_used": 0,
                "external_services_used": 0,
            },
        )


def _drift_detection(root: Path) -> ScenarioResult:
    with EvaluationHarness(root / "drift-detection") as harness:
        drift = harness.app.drift_watch(
            passport_id=harness.passport.id,
            platform=harness.adapter.platform,
            account_id="destination-twin",
            actor_id="person-a",
        )
        aligned_state = harness.adapter.export_state("destination-twin")
        aligned_state.update(
            {
                "topic_affinity": dict(harness.passport.topic_targets),
                "following": ["studio-a"],
                "muted_creators": ["rage-farm"],
                "muted_keywords": ["ragebait"],
                "serendipity": harness.passport.serendipity,
                "source_cap": harness.passport.max_source_share,
            }
        )
        harness.adapter.import_state(aligned_state)
        aligned = harness.app.drift_watch(
            passport_id=harness.passport.id,
            platform=harness.adapter.platform,
            account_id="destination-twin",
            actor_id="person-a",
        )
        proposed_actions = (
            len(drift["proposed_plan"]["actions"]) if drift["proposed_plan"] is not None else 0
        )
        before_distance = _rounded(drift["evaluation"]["total_variation_distance"])
        after_distance = _rounded(aligned["evaluation"]["total_variation_distance"])
        return ScenarioResult(
            id="drift_detection",
            title="Drift watch distinguishes a degraded feed from an aligned control state",
            checks={
                "degraded_feed_requires_decision": drift["status"] == "decision_required",
                "drift_has_corrective_plan": proposed_actions > 0,
                "aligned_control_state_needs_no_action": aligned["status"] == "aligned",
                "aligned_control_state_has_lower_distance": after_distance < before_distance,
            },
            metrics={
                "drift_status": drift["status"],
                "corrective_actions": proposed_actions,
                "pre_migration_topic_distance": before_distance,
                "aligned_topic_distance": after_distance,
                "aligned_status": aligned["status"],
            },
        )


def _scheduled_drift_monitor(root: Path) -> ScenarioResult:
    with EvaluationHarness(root / "scheduled-drift-monitor") as harness:
        allowed = frozenset(
            {
                ActionType.SET_TOPIC_PREFERENCE,
                ActionType.HIDE_TOPIC,
                ActionType.FOLLOW_CREATOR,
                ActionType.MUTE_CREATOR,
                ActionType.SET_SERENDIPITY,
                ActionType.SET_SOURCE_CAP,
            }
        )
        monitor = harness.app.create_drift_monitor(
            passport_id=harness.passport.id,
            platform=harness.adapter.platform,
            account_id="destination-new",
            actor_id="person-a",
            interval_minutes=15,
            expires_at=harness.clock() + timedelta(hours=1),
            mode="bounded_auto",
            allowed_actions=allowed,
            max_actions_per_run=3,
        )
        harness.clock.advance(timedelta(minutes=15))
        first = harness.app.process_due_jobs()[0]
        current = harness.app.projection_get("drift_monitors", monitor["id"])
        stopped = harness.app.stop_drift_monitor(monitor["id"], actor_id="person-a")
        harness.clock.advance(timedelta(minutes=15))
        second = harness.app.process_due_jobs()[0]
        return ScenarioResult(
            id="scheduled_drift_monitor",
            title="A bounded monitor repairs only certified Lab controls and stops immediately",
            checks={
                "first_check_ran": first["status"] == "checked",
                "bounded_run_issued_receipt": bool(first["receipt_id"]),
                "run_count_recorded": current["run_count"] == 1,
                "emergency_stop_recorded": stopped["status"] == "stopped",
                "next_job_did_not_run": second["status"] == "stopped",
                "action_allowlist_preserved": set(current["allowed_actions"])
                == {item.value for item in allowed},
            },
            metrics={
                "mode": current["mode"],
                "max_actions_per_run": current["max_actions_per_run"],
                "run_count": current["run_count"],
                "receipt_id": first["receipt_id"],
                "status_after_stop": second["status"],
            },
        )


def _creator_continuity(root: Path) -> ScenarioResult:
    with EvaluationHarness(root / "creator-continuity") as harness:
        verified = harness.app.creator_continuity("studio-a", "youtube")
        unavailable = harness.app.creator_continuity("paper-lab", "instagram")
        unknown = harness.app.creator_continuity("unverified-person", "youtube")
        return ScenarioResult(
            id="creator_continuity",
            title="Verified cross-platform identity is preserved without guessing unknown creators",
            checks={
                "known_creator_resolves": verified["status"] == "verified"
                and verified["match"] == "@studio-a",
                "known_missing_destination_is_not_found": unavailable["status"] == "not_found"
                and unavailable["match"] is None,
                "unknown_creator_is_not_guessed": unknown["status"] == "unresolved"
                and unknown["match"] is None,
                "unknown_creator_requires_human_confirmation": unknown["requires_human_confirmation"]
                is True,
            },
            metrics={
                "verified_destination": verified["match"],
                "verified_confidence": verified["confidence"],
                "missing_destination_status": unavailable["status"],
                "unknown_status": unknown["status"],
                "unknown_requires_human_confirmation": unknown["requires_human_confirmation"],
            },
        )


_SCENARIOS: tuple[Callable[[Path], ScenarioResult], ...] = (
    _passport_portability_and_checkpoints,
    _migration_quality,
    _policy_safety,
    _rollback_fidelity,
    _temporary_overlay_isolation_expiry,
    _temporary_visa_early_revoke,
    _consent_scoped_companion_blending,
    _idempotency,
    _capability_honesty,
    _local_platform_twin_matrix,
    _bounded_agent_mission_lifecycle,
    _drift_detection,
    _scheduled_drift_monitor,
    _creator_continuity,
)


def run_suite(*, work_dir: str | Path | None = None) -> dict[str, Any]:
    """Run all offline scenarios and return a deterministic in-memory report.

    This function never creates a report artifact. SQLite state is staged in a
    temporary directory and removed before returning.
    """

    staging_parent: Path | None = None
    if work_dir is not None:
        staging_parent = Path(work_dir)
        staging_parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="feed-passport-evaluation-", dir=staging_parent) as temporary:
        root = Path(temporary)
        results: list[dict[str, Any]] = []
        for expected_id, scenario in zip(SCENARIO_IDS, _SCENARIOS, strict=True):
            try:
                result = scenario(root)
                if result.id != expected_id:
                    raise ValueError("scenario returned a mismatched identifier")
                results.append(result.to_dict())
            except Exception as exc:  # Keep the CLI report complete when one scenario fails.
                results.append(
                    {
                        "id": expected_id,
                        "title": expected_id.replace("_", " ").title(),
                        "status": "failed",
                        "checks": {"completed_without_exception": False},
                        "metrics": {"error_type": type(exc).__name__},
                    }
                )

    passed = sum(item["status"] == "passed" for item in results)
    failed = len(results) - passed
    return {
        "schema_version": "1.0",
        "suite_id": "feed-passport-deterministic-acceptance-v1",
        "generated_at": EVALUATION_TIME.isoformat(),
        "execution": {
            "adapter": "feed_passport_lab plus ten local platform control twins",
            "adapters": [
                "feed_passport_lab",
                *(f"twin:{platform}" for platform in sorted(PLATFORM_PROFILES)),
            ],
            "mission_adapter": "twin:x",
            "mode": "deterministic_offline",
            "external_services": [],
        },
        "summary": {
            "failed": failed,
            "passed": passed,
            "status": "passed" if failed == 0 else "failed",
            "total": len(results),
        },
        "scenarios": results,
    }
