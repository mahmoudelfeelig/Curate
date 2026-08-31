from __future__ import annotations

from datetime import datetime
from typing import Any

from strands import tool

from feed_passport.application import AgentMissionRunner, CuratorApplication
from feed_passport.domain import (
    ActionType,
    AgentMissionAcceptance,
    AgentMissionBudget,
    OverlayMode,
)
from feed_passport.infrastructure.serialization import to_primitive

from .consent import ConsentBroker, require_model_alert_only_monitor


def build_curator_tools(application: CuratorApplication, broker: ConsentBroker) -> list[Any]:
    """Create application-bound tools without leaking framework concerns into the domain."""

    mission_runner = AgentMissionRunner(application)

    def parse_time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return parsed

    @tool
    def inspect_passport(passport_id: str) -> dict[str, Any]:
        """Inspect one versioned Feed Passport and its currently active visas.

        Args:
            passport_id: Exact identifier of the user-owned Passport.
        """
        base = application.get_passport(passport_id)
        effective = application.effective_passport(passport_id)
        overlays = [
            value
            for value in application.projection_list("overlays")
            if value["base_passport_id"] == passport_id
        ]
        return {
            "base": to_primitive(base),
            "effective": to_primitive(effective),
            "visas": overlays,
        }

    @tool
    def inspect_platform_capabilities() -> dict[str, Any]:
        """List every platform adapter with its evidence-backed capability level and health."""
        return {"platforms": list(application.list_platforms())}

    @tool
    def capture_source_passport(
        platform: str,
        account_id: str,
        actor_id: str,
        name: str,
        intent: str,
    ) -> dict[str, Any]:
        """Capture an authorized observation as a new intent-only Passport.

        Args:
            platform: Registered source adapter identifier.
            account_id: Exact authorized source account or declared demo snapshot.
            actor_id: Owner of the new Passport.
            name: Human-readable Passport name.
            intent: Plain-language purpose for the captured policy.
        """
        return to_primitive(
            application.infer_passport_from_account(
                platform=platform,
                account_id=account_id,
                owner_id=actor_id,
                name=name,
                intent=intent,
            )
        )

    @tool
    def preview_translation(
        passport_id: str,
        platform: str,
        destination_account_id: str,
        actor_id: str,
        overlay_id: str = "",
    ) -> dict[str, Any]:
        """Create a counterfactual migration preview; this never executes platform actions.

        Args:
            passport_id: Passport to translate.
            platform: Capability-certified adapter identifier.
            destination_account_id: Destination account selected by the owner.
            actor_id: Passport owner requesting the preview.
            overlay_id: Optional reversible-live visa to activate.
        """
        return application.prepare_migration(
            passport_id=passport_id,
            platform=platform,
            destination_account_id=destination_account_id,
            actor_id=actor_id,
            overlay_id=overlay_id or None,
        )

    @tool
    def apply_approved_migration(
        migration_id: str,
        actor_id: str,
        approval_token: str,
    ) -> dict[str, Any]:
        """Execute only the preview and action budget covered by a one-time approval.

        Args:
            migration_id: Exact previewed migration identifier.
            actor_id: Passport owner who granted consent.
            approval_token: Short-lived signed confirmation returned outside the model.
        """
        grant = broker.consume(
            approval_token,
            operation="execute_migration",
            resource_id=migration_id,
            actor_id=actor_id,
        )
        return application.execute_migration(
            migration_id,
            approved_by=actor_id,
            max_total_actions=int(grant["max_total_actions"]),
        )

    @tool
    def rollback_approved_receipt(
        receipt_id: str,
        platform: str,
        actor_id: str,
        approval_token: str,
    ) -> dict[str, Any]:
        """Roll back reversible actions covered by a one-time owner confirmation.

        Args:
            receipt_id: Auditable receipt to restore.
            platform: Adapter that issued the receipt.
            actor_id: Passport owner granting rollback consent.
            approval_token: Short-lived signed confirmation returned outside the model.
        """
        broker.consume(
            approval_token,
            operation="rollback_receipt",
            resource_id=receipt_id,
            actor_id=actor_id,
        )
        return application.rollback_receipt(receipt_id, actor_id=actor_id, platform=platform)

    @tool
    def preview_local_agent_mission(
        goal: str,
        passport_id: str,
        destination_twin: str,
        destination_account_id: str,
        actor_id: str,
        total_action_budget: int = 6,
        per_iteration_action_budget: int = 2,
        max_iterations: int = 3,
        max_topic_distance: float = 0.18,
        max_unwanted_rate: float = 0.05,
        max_source_concentration: float = 0.4,
        min_serendipity_rate: float = 0.05,
        max_serendipity_rate: float = 1.0,
        min_improvement: float = 0.01,
    ) -> dict[str, Any]:
        """Preview a persisted, bounded mission against an account-free local control twin.

        This observes, evaluates, plans, and pauses for human approval. It never accesses
        a social account or reproduces a platform's private ranking system.

        Args:
            goal: Human-readable outcome for the mission; it does not grant authority.
            passport_id: Exact user-owned Passport that supplies executable intent.
            destination_twin: Exact local adapter id, such as twin:youtube or twin:x.
            destination_account_id: Seeded local scenario id, never a real account id.
            actor_id: Passport owner requesting the preview.
            total_action_budget: Hard ceiling across the complete mission.
            per_iteration_action_budget: Hard ceiling for one observe-act-measure pass.
            max_iterations: Maximum number of adaptation passes.
            max_topic_distance: Largest acceptable measured topic distance.
            max_unwanted_rate: Largest acceptable unwanted-content rate.
            max_source_concentration: Largest acceptable single-source concentration.
            min_serendipity_rate: Smallest acceptable measured serendipity rate.
            max_serendipity_rate: Largest acceptable measured serendipity rate.
            min_improvement: Minimum topic-distance improvement required to continue.
        """
        return mission_runner.preview(
            actor_id=actor_id,
            goal=goal,
            passport_id=passport_id,
            destination_twin=destination_twin,
            destination_account_id=destination_account_id,
            budget=AgentMissionBudget(
                total_actions=total_action_budget,
                per_iteration_actions=per_iteration_action_budget,
                max_iterations=max_iterations,
            ),
            acceptance=AgentMissionAcceptance(
                max_total_variation_distance=max_topic_distance,
                max_unwanted_rate=max_unwanted_rate,
                max_source_concentration=max_source_concentration,
                min_serendipity_rate=min_serendipity_rate,
                max_serendipity_rate=max_serendipity_rate,
            ),
            min_improvement=min_improvement,
        )

    @tool
    def inspect_local_agent_mission(mission_id: str, actor_id: str) -> dict[str, Any]:
        """Inspect the persisted observations, decisions, budgets, receipts, and stop reason."""
        return mission_runner.get(mission_id, actor_id=actor_id)

    @tool
    def execute_approved_agent_mission(
        mission_id: str,
        actor_id: str,
        approval_token: str,
    ) -> dict[str, Any]:
        """Run only the local mission covered by a fresh, one-time human approval token."""
        broker.consume(
            approval_token,
            operation="execute_agent_mission",
            resource_id=mission_id,
            actor_id=actor_id,
        )
        return mission_runner.execute(mission_id, actor_id=actor_id)

    @tool
    def cancel_local_agent_mission(mission_id: str, actor_id: str) -> dict[str, Any]:
        """Cancel an owner-controlled local mission before it executes."""
        return mission_runner.cancel(mission_id, actor_id=actor_id)

    @tool
    def rollback_approved_agent_mission(
        mission_id: str,
        actor_id: str,
        approval_token: str,
    ) -> dict[str, Any]:
        """Reverse mission receipts only under a separate one-time human approval token."""
        broker.consume(
            approval_token,
            operation="rollback_agent_mission",
            resource_id=mission_id,
            actor_id=actor_id,
        )
        return mission_runner.rollback(mission_id, actor_id=actor_id)

    @tool
    def watch_feed_drift(
        passport_id: str,
        platform: str,
        account_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        """Measure feed drift and propose a non-executing repair plan when needed."""
        return application.drift_watch(
            passport_id=passport_id,
            platform=platform,
            account_id=account_id,
            actor_id=actor_id,
        )

    @tool
    def find_creator_continuity(creator_id: str, destination_platform: str) -> dict[str, Any]:
        """Resolve a creator through verified cross-platform identity links."""
        return application.creator_continuity(creator_id, destination_platform)

    @tool
    def list_temporary_visa_templates() -> dict[str, Any]:
        """List safe reusable temporary Feed Passport overlay templates."""
        return {"templates": list(application.list_templates())}

    @tool
    def create_temporary_visa(
        passport_id: str,
        actor_id: str,
        name: str,
        topic_adjustments: dict[str, float],
        starts_at: str,
        expires_at: str,
        mode: str = "isolated",
        add_exclusions: list[str] | None = None,
        serendipity: float | None = None,
        max_outrage: float | None = None,
    ) -> dict[str, Any]:
        """Create an expiring private policy overlay owned by the Passport holder."""
        return application.create_overlay(
            base_passport_id=passport_id,
            name=name,
            topic_adjustments=topic_adjustments,
            add_exclusions=frozenset(add_exclusions or ()),
            remove_exclusions=frozenset(),
            starts_at=parse_time(starts_at),
            expires_at=parse_time(expires_at),
            mode=OverlayMode(mode),
            serendipity=serendipity,
            max_outrage=max_outrage,
            actor_id=actor_id,
        )

    @tool
    def revoke_temporary_visa(visa_id: str, actor_id: str) -> dict[str, Any]:
        """Revoke an expiring visa immediately and roll back any linked live activation."""
        return application.revoke_overlay(visa_id, actor_id=actor_id)

    @tool
    def create_companion_slice(
        passport_id: str,
        actor_id: str,
        topic_names: list[str],
        creator_ids: list[str],
        include_serendipity: bool,
        include_formats: bool,
        include_exclusions: bool,
        expires_at: str,
    ) -> dict[str, Any]:
        """Share only selected, expiring Passport fields for a companion blend."""
        return application.create_share_slice(
            passport_id=passport_id,
            topic_names=tuple(topic_names),
            creator_ids=tuple(creator_ids),
            include_serendipity=include_serendipity,
            include_formats=include_formats,
            include_exclusions=include_exclusions,
            expires_at=parse_time(expires_at),
            actor_id=actor_id,
        )

    @tool
    def create_companion_passport(
        name: str,
        slice_ids: list[str],
        weights: dict[str, float],
        strategy: str,
        expires_at: str,
        actor_id: str,
    ) -> dict[str, Any]:
        """Blend two or more explicit Passport slices without transferring raw history."""
        return application.create_companion_blend(
            name=name,
            slice_ids=tuple(slice_ids),
            weights=weights,
            strategy=strategy,
            expires_at=parse_time(expires_at),
            actor_id=actor_id,
        )

    @tool
    def revoke_companion_slice(slice_id: str, actor_id: str) -> dict[str, Any]:
        """Revoke a shared slice and invalidate every companion blend that depends on it."""
        return application.revoke_share_slice(slice_id, actor_id=actor_id)

    @tool
    def schedule_drift_monitor(
        passport_id: str,
        platform: str,
        account_id: str,
        actor_id: str,
        interval_minutes: int,
        expires_at: str,
        mode: str = "alert_only",
        allowed_actions: list[str] | None = None,
        max_actions_per_run: int = 3,
        minimum_confidence: float = 0.8,
    ) -> dict[str, Any]:
        """Schedule an expiring alert-only drift monitor without mutation authority."""
        action_values = tuple(allowed_actions or ())
        require_model_alert_only_monitor(
            mode=mode,
            allowed_actions=action_values,
        )
        return application.create_drift_monitor(
            passport_id=passport_id,
            platform=platform,
            account_id=account_id,
            actor_id=actor_id,
            interval_minutes=interval_minutes,
            expires_at=parse_time(expires_at),
            mode=mode,
            allowed_actions=frozenset(ActionType(value) for value in action_values),
            max_actions_per_run=max_actions_per_run,
            minimum_confidence=minimum_confidence,
        )

    @tool
    def stop_drift_monitor(monitor_id: str, actor_id: str) -> dict[str, Any]:
        """Stop a scheduled drift monitor immediately."""
        return application.stop_drift_monitor(monitor_id, actor_id=actor_id)

    @tool
    def preserve_creator_continuity(
        passport_id: str,
        creator_id: str,
        destination_platform: str,
        actor_id: str,
    ) -> dict[str, Any]:
        """Persist an owner-reviewed creator identity directory match."""
        return application.preserve_creator_match(
            passport_id=passport_id,
            creator_id=creator_id,
            destination_platform=destination_platform,
            actor_id=actor_id,
        )

    @tool
    def create_passport_checkpoint(passport_id: str, actor_id: str, label: str) -> dict[str, Any]:
        """Save an immutable owner checkpoint before a later private policy edit."""
        return application.create_checkpoint(passport_id, actor_id=actor_id, label=label)

    @tool
    def restore_passport_checkpoint(checkpoint_id: str, actor_id: str) -> dict[str, Any]:
        """Restore a checkpoint as a new auditable Passport version."""
        return to_primitive(application.restore_checkpoint(checkpoint_id, actor_id=actor_id))

    @tool
    def export_portable_passport(passport_id: str, actor_id: str) -> dict[str, Any]:
        """Export user-owned preference intent without credentials or raw activity history."""
        passport = application.get_passport(passport_id)
        if passport.owner_id != actor_id:
            raise PermissionError("only the Passport owner can export it")
        return {"format": "feed-passport/v1", "passport": to_primitive(passport)}

    @tool
    def import_portable_passport(
        actor_id: str,
        format_name: str,
        passport: dict[str, Any],
    ) -> dict[str, Any]:
        """Import a validated intent-only Passport document as a new local identity."""
        return application.import_passport(
            actor_id=actor_id,
            format_name=format_name,
            passport_data=passport,
        )

    return [
        inspect_passport,
        inspect_platform_capabilities,
        capture_source_passport,
        preview_translation,
        apply_approved_migration,
        rollback_approved_receipt,
        preview_local_agent_mission,
        inspect_local_agent_mission,
        execute_approved_agent_mission,
        cancel_local_agent_mission,
        rollback_approved_agent_mission,
        watch_feed_drift,
        find_creator_continuity,
        list_temporary_visa_templates,
        create_temporary_visa,
        revoke_temporary_visa,
        create_companion_slice,
        create_companion_passport,
        revoke_companion_slice,
        schedule_drift_monitor,
        stop_drift_monitor,
        preserve_creator_continuity,
        create_passport_checkpoint,
        restore_passport_checkpoint,
        export_portable_passport,
        import_portable_passport,
    ]
