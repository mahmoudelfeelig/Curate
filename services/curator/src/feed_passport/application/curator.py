from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from threading import Lock, RLock
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from feed_passport.domain import (
    ActionEnvelope,
    ActionLedger,
    ActionOutcome,
    RollbackOutcome,
    ActionStatus,
    ActionType,
    FeedPassport,
    OverlayMode,
    PassportOverlay,
    PolicyGuard,
    PreferenceEvidence,
    ShareablePassportSlice,
    StopReason,
    apply_overlay,
    blend_slices,
    evaluate_feed,
)
from feed_passport.infrastructure import ConcurrencyConflict, SQLiteStore
from feed_passport.infrastructure.portable_passport import (
    PortablePassportCodec,
    PortableProvenanceTrustStore,
)
from feed_passport.infrastructure.serialization import (
    capability_manifest_to_contract,
    parse_datetime,
    to_primitive,
)
from feed_passport.ports import PlatformAdapter
from feed_passport.ports.action_journal import ActionJournal, RemoteActionAttempt, RemoteActionState
from feed_passport.ports.connections import ExternalConnectionRepository
from feed_passport.ports.live_platform import (
    LivePlatformError,
    PreparedRemoteAction,
    RemoteOutcomeUnknown,
)

from .model_io import (
    action_from_dict,
    overlay_from_dict,
    passport_from_dict,
    passport_to_dict,
    receipt_from_dict,
    slice_from_dict,
)


class NotFoundError(KeyError):
    pass


class InvalidStateError(RuntimeError):
    pass


_REMOTE_RECOVERY_MAX_ATTEMPTS = 5
_REMOTE_RECOVERY_BASE_DELAY_SECONDS = 5
_REMOTE_RECOVERY_MAX_DELAY_SECONDS = 300


def _remote_recovery_delay(attempt_count: int) -> timedelta:
    exponent = max(0, min(attempt_count - 2, 10))
    seconds = min(
        _REMOTE_RECOVERY_BASE_DELAY_SECONDS * (2**exponent),
        _REMOTE_RECOVERY_MAX_DELAY_SECONDS,
    )
    return timedelta(seconds=seconds)


DEFAULT_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "id": "template-conference-week",
        "name": "Conference week",
        "description": "Temporarily emphasize research, speakers, and local context without training the base Passport.",
        "topic_adjustments": {"research": 0.25, "local": 0.15},
        "add_exclusions": ["ragebait"],
        "serendipity": 0.3,
        "duration_hours": 72,
        "mode": "isolated",
    },
    {
        "id": "template-new-city",
        "name": "New city",
        "description": "Find local culture, public spaces, events, and independent businesses for one week.",
        "topic_adjustments": {"local": 0.35, "culture": 0.2, "events": 0.15},
        "add_exclusions": ["ragebait"],
        "serendipity": 0.4,
        "duration_hours": 168,
        "mode": "isolated",
    },
    {
        "id": "template-deep-work",
        "name": "Deep work",
        "description": "Reduce short-form noise and emphasize long-form learning for a focused day.",
        "topic_adjustments": {"research": 0.3, "learning": 0.25},
        "add_exclusions": ["ragebait", "short_form"],
        "serendipity": 0.1,
        "duration_hours": 24,
        "mode": "isolated",
    },
)


CREATOR_DIRECTORY: tuple[dict[str, Any], ...] = (
    {
        "canonical_id": "creator-studio-a",
        "display_name": "Studio A",
        "verified_links": {
            "feed_passport_lab": "studio-a",
            "bluesky": "studio-a.example",
            "youtube": "@studio-a",
            "x": "studio_a",
        },
    },
    {
        "canonical_id": "creator-paper-lab",
        "display_name": "Paper Lab",
        "verified_links": {
            "feed_passport_lab": "paper-lab",
            "bluesky": "paper-lab.example",
            "youtube": "@paper-lab",
        },
    },
    {
        "canonical_id": "creator-city-zine",
        "display_name": "City Zine",
        "verified_links": {
            "feed_passport_lab": "city-zine",
            "bluesky": "city-zine.example",
            "instagram": "city.zine",
        },
    },
)


_UNKNOWN_CONTENT_SOURCE_MARKERS = frozenset(
    {"", "unknown", "unavailable", "not_provided", "not-provided", "redacted"}
)


class CuratorApplication:
    """Transaction boundary for all Feed Passport product workflows."""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        adapters: Mapping[str, PlatformAdapter],
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        portable_secret: str | bytes | None = None,
        portable_trusted_secrets: Iterable[str | bytes] = (),
        connections: ExternalConnectionRepository | None = None,
        action_journal: ActionJournal | None = None,
    ) -> None:
        self.store = store
        self.adapters = dict(adapters)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.connections = connections
        self.action_journal = action_journal or store
        self._execution_owner = uuid4().hex
        self._execution_locks_guard = Lock()
        self._execution_locks: dict[str, RLock] = {}
        resolved_portable_secret = portable_secret or store.get_or_create_secret(
            "portable-passport-signing-v1"
        )
        portable_trust = PortableProvenanceTrustStore.from_secrets(
            (resolved_portable_secret, *tuple(portable_trusted_secrets))
        )
        self._portable_passports = PortablePassportCodec(
            resolved_portable_secret,
            trust_store=portable_trust,
        )
        self.guard = PolicyGuard()
        self._restore_adapter_state()

    def list_templates(self) -> tuple[dict[str, Any], ...]:
        return DEFAULT_TEMPLATES

    def list_platforms(self) -> tuple[dict[str, Any], ...]:
        values: list[dict[str, Any]] = []
        for platform, adapter in sorted(self.adapters.items()):
            known_account_ids = self._known_account_ids(platform)
            account_id = known_account_ids[0] if known_account_ids else "unconfigured"
            public_account_ids = self._public_demo_account_ids(adapter)
            try:
                manifest = adapter.capabilities(account_id)
                health = adapter.health(now=self._now())
                profile = getattr(adapter, "PROFILE", None)
                values.append(
                    {
                        "platform": platform,
                        "manifest": capability_manifest_to_contract(
                            manifest,
                            adapter_name=type(adapter).__name__,
                            evidence_urls=getattr(profile, "evidence_urls", ()),
                        ),
                        "health": to_primitive(health),
                        "accounts": public_account_ids,
                    }
                )
            except (KeyError, ValueError):
                values.append(
                    {
                        "platform": platform,
                        "manifest": None,
                        "health": {"healthy": False, "mode": "unconfigured"},
                        "accounts": public_account_ids,
                    }
                )
        return tuple(values)

    def create_passport(
        self,
        *,
        owner_id: str,
        name: str,
        intent: str,
        topic_targets: Mapping[str, float],
        creator_preferences: Mapping[str, float] | None = None,
        format_preferences: Mapping[str, float] | None = None,
        languages: tuple[str, ...] = ("en",),
        hard_exclusions: frozenset[str] = frozenset(),
        serendipity: float = 0.2,
        max_outrage: float = 0.05,
        max_source_share: float = 0.25,
        expires_at: datetime | None = None,
        provenance: tuple[PreferenceEvidence, ...] = (),
        trace_id: str | None = None,
    ) -> FeedPassport:
        now = self._now()
        passport = FeedPassport(
            id=self._id("passport"),
            owner_id=owner_id,
            name=name,
            version=1,
            intent=intent,
            topic_targets=topic_targets,
            creator_preferences=creator_preferences or {},
            format_preferences=format_preferences or {},
            languages=languages,
            hard_exclusions=hard_exclusions,
            serendipity=serendipity,
            max_outrage=max_outrage,
            max_source_share=max_source_share,
            expires_at=expires_at,
            provenance=provenance,
            created_at=now,
            updated_at=now,
        )
        # Persistence is the public contract boundary: a Passport that cannot be
        # exported is not a valid stored Passport. Validate before recording so
        # failed creates are atomic and never leave an unusable projection.
        self._portable_passports.export(passport)
        self._record(
            kind="passports",
            aggregate_id=passport.id,
            aggregate_type="feed_passport",
            event_type="passport.created",
            projection=passport_to_dict(passport),
            payload={"owner_id": owner_id, "version": 1},
            actor_id=owner_id,
            trace_id=trace_id,
        )
        return passport

    def import_passport(
        self,
        *,
        actor_id: str,
        format_name: str,
        passport_data: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate and import the public portable contract as a new local identity."""

        if format_name != "feed-passport/v1":
            raise ValueError("unsupported Passport import format")
        source = self._portable_passports.load(passport_data)
        imported = self.create_passport(
            owner_id=actor_id,
            name=source.name,
            intent=source.intent,
            topic_targets=source.topic_targets,
            creator_preferences=source.creator_preferences,
            format_preferences=source.format_preferences,
            languages=source.languages,
            hard_exclusions=source.hard_exclusions,
            serendipity=source.serendipity,
            max_outrage=source.max_outrage,
            max_source_share=source.max_source_share,
            expires_at=source.expires_at,
            provenance=source.provenance,
        )
        return {
            "format": format_name,
            "source_passport_id": source.source_passport_id,
            "source_version": source.source_version,
            "provenance_trust": source.provenance_trust,
            "translation_losses": list(source.translation_losses),
            "passport": passport_to_dict(imported),
        }

    def export_passport(self, passport_id: str) -> dict[str, Any]:
        """Return the strict public contract without local IDs or raw history."""

        return self._portable_passports.export(self.get_passport(passport_id))

    def infer_passport_from_account(
        self,
        *,
        platform: str,
        account_id: str,
        owner_id: str,
        name: str,
        intent: str,
        trace_id: str | None = None,
    ) -> FeedPassport:
        adapter = self._adapter(platform)
        observation = adapter.observe(account_id, now=self._now(), sample_size=32)
        if observation.confidence <= 0 or not observation.sample.items:
            raise InvalidStateError(
                "the source account has no authorized observation; provide a declared snapshot or use a seeded demo account"
            )
        creator_preferences = {creator: 1.0 for creator in observation.followed_creators}
        return self.create_passport(
            owner_id=owner_id,
            name=name,
            intent=intent,
            topic_targets=observation.topic_distribution,
            creator_preferences=creator_preferences,
            serendipity=sum(item.novelty >= 0.6 for item in observation.sample.items)
            / max(1, len(observation.sample.items)),
            max_outrage=max(
                0.01,
                sum(item.outrage for item in observation.sample.items)
                / max(1, len(observation.sample.items)),
            ),
            max_source_share=self._source_concentration(observation.sample.items),
            provenance=self._observation_provenance(observation),
            trace_id=trace_id,
        )

    def get_passport(self, passport_id: str) -> FeedPassport:
        value = self._projection("passports", passport_id)
        return passport_from_dict(value)

    def list_passports(self) -> tuple[FeedPassport, ...]:
        return tuple(passport_from_dict(value) for _, _, value in self.store.list_projections("passports"))

    def revise_passport(self, passport_id: str, *, actor_id: str, changes: Mapping[str, Any]) -> FeedPassport:
        current = self.get_passport(passport_id)
        if current.owner_id != actor_id:
            raise PermissionError("only the Passport owner can revise it")
        allowed = {
            "name",
            "intent",
            "topic_targets",
            "creator_preferences",
            "format_preferences",
            "languages",
            "hard_exclusions",
            "serendipity",
            "max_outrage",
            "max_source_share",
        }
        unexpected = set(changes) - allowed
        if unexpected:
            raise ValueError(f"unsupported Passport fields: {', '.join(sorted(unexpected))}")
        normalized = dict(changes)
        if "hard_exclusions" in normalized:
            normalized["hard_exclusions"] = frozenset(normalized["hard_exclusions"])
        if "languages" in normalized:
            normalized["languages"] = tuple(normalized["languages"])
        revised = current.revise(updated_at=self._now(), **normalized)
        # Keep every durable revision portable, and reject invalid revisions
        # before either the event log or continuous Companion slices change.
        self._portable_passports.export(revised)
        self._record(
            kind="passports",
            aggregate_id=passport_id,
            aggregate_type="feed_passport",
            event_type="passport.revised",
            projection=passport_to_dict(revised),
            payload={"from_version": current.version, "to_version": revised.version, "fields": sorted(changes)},
            actor_id=actor_id,
        )
        self._refresh_continuous_companions_for_passport(passport_id)
        return revised

    def create_checkpoint(self, passport_id: str, *, actor_id: str, label: str) -> dict[str, Any]:
        passport = self.get_passport(passport_id)
        if passport.owner_id != actor_id:
            raise PermissionError("only the Passport owner can create a checkpoint")
        if not label.strip():
            raise ValueError("checkpoint label is required")
        checkpoint_id = self._id("checkpoint")
        projection = {
            "id": checkpoint_id,
            "passport_id": passport.id,
            "passport_version": passport.version,
            "owner_id": passport.owner_id,
            "label": label.strip(),
            "snapshot": passport_to_dict(passport),
            "created_at": self._now().isoformat(),
            "last_restored_at": None,
        }
        self._record(
            kind="checkpoints",
            aggregate_id=checkpoint_id,
            aggregate_type="passport_checkpoint",
            event_type="checkpoint.created",
            projection=projection,
            payload={"passport_id": passport.id, "passport_version": passport.version, "label": label.strip()},
            actor_id=actor_id,
        )
        return projection

    def restore_checkpoint(self, checkpoint_id: str, *, actor_id: str) -> FeedPassport:
        checkpoint = self._projection("checkpoints", checkpoint_id)
        if checkpoint["owner_id"] != actor_id:
            raise PermissionError("only the Passport owner can restore its checkpoint")
        snapshot = passport_from_dict(checkpoint["snapshot"])
        restored = self.revise_passport(
            snapshot.id,
            actor_id=actor_id,
            changes={
                "name": snapshot.name,
                "intent": snapshot.intent,
                "topic_targets": snapshot.topic_targets,
                "creator_preferences": snapshot.creator_preferences,
                "format_preferences": snapshot.format_preferences,
                "languages": snapshot.languages,
                "hard_exclusions": snapshot.hard_exclusions,
                "serendipity": snapshot.serendipity,
                "max_outrage": snapshot.max_outrage,
                "max_source_share": snapshot.max_source_share,
            },
        )
        checkpoint["last_restored_at"] = self._now().isoformat()
        checkpoint["restored_as_version"] = restored.version
        self._record(
            kind="checkpoints",
            aggregate_id=checkpoint_id,
            aggregate_type="passport_checkpoint",
            event_type="checkpoint.restored",
            projection=checkpoint,
            payload={"restored_as_version": restored.version},
            actor_id=actor_id,
        )
        return restored

    def create_overlay(
        self,
        *,
        base_passport_id: str,
        name: str,
        topic_adjustments: Mapping[str, float],
        add_exclusions: frozenset[str],
        remove_exclusions: frozenset[str],
        starts_at: datetime,
        expires_at: datetime,
        mode: OverlayMode,
        serendipity: float | None,
        max_outrage: float | None,
        actor_id: str,
    ) -> dict[str, Any]:
        passport = self.get_passport(base_passport_id)
        if passport.owner_id != actor_id:
            raise PermissionError("only the Passport owner can create a visa")
        overlay = PassportOverlay(
            id=self._id("visa"),
            base_passport_id=base_passport_id,
            name=name,
            topic_adjustments=topic_adjustments,
            add_exclusions=add_exclusions,
            remove_exclusions=remove_exclusions,
            starts_at=starts_at,
            expires_at=expires_at,
            mode=mode,
            serendipity=serendipity,
            max_outrage=max_outrage,
        )
        projection = {
            **to_primitive(overlay),
            "owner_id": passport.owner_id,
            "status": "scheduled" if starts_at > self._now() else "active",
            "activations": [],
        }
        self._record(
            kind="overlays",
            aggregate_id=overlay.id,
            aggregate_type="passport_overlay",
            event_type="overlay.created",
            projection=projection,
            payload={"base_passport_id": base_passport_id, "mode": mode.value},
            actor_id=actor_id,
        )
        self.store.schedule_once(
            f"expire-{overlay.id}",
            "overlay.expire",
            overlay.expires_at,
            {"overlay_id": overlay.id},
        )
        if overlay.starts_at > self._now():
            self.store.schedule_once(
                f"activate-{overlay.id}",
                "overlay.activate",
                overlay.starts_at,
                {"overlay_id": overlay.id},
            )
        return projection

    def create_overlay_from_template(
        self,
        template_id: str,
        *,
        base_passport_id: str,
        actor_id: str,
        starts_at: datetime | None = None,
    ) -> dict[str, Any]:
        template = next((item for item in DEFAULT_TEMPLATES if item["id"] == template_id), None)
        if template is None:
            raise NotFoundError(template_id)
        starts = starts_at or self._now()
        return self.create_overlay(
            base_passport_id=base_passport_id,
            name=str(template["name"]),
            topic_adjustments=dict(template["topic_adjustments"]),
            add_exclusions=frozenset(template["add_exclusions"]),
            remove_exclusions=frozenset(),
            starts_at=starts,
            expires_at=starts + timedelta(hours=int(template["duration_hours"])),
            mode=OverlayMode(str(template["mode"])),
            serendipity=float(template["serendipity"]),
            max_outrage=None,
            actor_id=actor_id,
        )

    def revoke_overlay(self, overlay_id: str, *, actor_id: str) -> dict[str, Any]:
        value = self._projection("overlays", overlay_id)
        if value["owner_id"] != actor_id:
            raise PermissionError("only the Passport owner can revoke a visa")
        if value.get("status") in {"revoked", "expired"}:
            return value
        rollback_results = self._rollback_overlay_activations(value, actor_id=actor_id)
        value["status"] = "revoked"
        value["revoked_at"] = self._now().isoformat()
        self._record(
            kind="overlays",
            aggregate_id=overlay_id,
            aggregate_type="passport_overlay",
            event_type="overlay.revoked",
            projection=value,
            payload={"revoked_at": value["revoked_at"], "rollbacks": rollback_results},
            actor_id=actor_id,
        )
        return value

    def effective_passport(self, passport_id: str, *, at: datetime | None = None) -> FeedPassport:
        now = at or self._now()
        result = self.get_passport(passport_id)
        companions = [
            value
            for _, _, value in self.store.list_projections("companions")
            if value.get("scope", "snapshot") == "continuous"
            and passport_id in value.get("target_passport_ids", ())
        ]
        for value in sorted(companions, key=lambda item: (str(item.get("created_at", "")), str(item["id"]))):
            current = self._continuous_companion_for_effective(value, at=now)
            if current is not None:
                result = self._apply_continuous_companion(result, current)
        overlays = [
            overlay_from_dict(value)
            for _, _, value in self.store.list_projections("overlays")
            if value["base_passport_id"] == passport_id and value.get("status") not in {"expired", "revoked"}
        ]
        for overlay in sorted(overlays, key=lambda item: (item.starts_at, item.id)):
            if overlay.is_active(now):
                result = apply_overlay(result, overlay, now)
        return result

    def process_due_jobs(self, *, at: datetime | None = None) -> tuple[dict[str, Any], ...]:
        now = at or self._now()
        results: list[dict[str, Any]] = []
        for job in self.store.claim_due_jobs(now):
            try:
                if job["kind"] == "drift.monitor":
                    monitor_id = str(job["payload"]["monitor_id"])
                    monitor = self._projection("drift_monitors", monitor_id)
                    if monitor.get("status") != "active":
                        results.append({"job_id": job["id"], "status": "stopped", "monitor_id": monitor_id})
                    elif now >= parse_datetime(str(monitor["expires_at"])):
                        monitor["status"] = "expired"
                        monitor["expired_at"] = now.isoformat()
                        self._record(
                            kind="drift_monitors",
                            aggregate_id=monitor_id,
                            aggregate_type="drift_monitor",
                            event_type="drift_monitor.expired",
                            projection=monitor,
                            payload={"expired_at": now.isoformat()},
                            actor_id="scheduler",
                        )
                        results.append({"job_id": job["id"], "status": "expired", "monitor_id": monitor_id})
                    else:
                        alert = self.drift_watch(
                            passport_id=str(monitor["passport_id"]),
                            platform=str(monitor["platform"]),
                            account_id=str(monitor["account_id"]),
                            actor_id="scheduler",
                        )
                        correction = None
                        if (
                            monitor["mode"] == "bounded_auto"
                            and alert["status"] == "decision_required"
                            and float(alert["confidence"]) >= float(monitor["minimum_confidence"])
                        ):
                            migration = self.prepare_migration(
                                passport_id=str(monitor["passport_id"]),
                                platform=str(monitor["platform"]),
                                destination_account_id=str(monitor["account_id"]),
                                actor_id=str(monitor["owner_id"]),
                            )
                            allowed = frozenset(ActionType(item) for item in monitor["allowed_actions"])
                            eligible = [
                                item
                                for item in migration["plan"]["actions"]
                                if ActionType(item["action_type"]) in allowed
                            ]
                            if eligible:
                                correction = self.execute_migration(
                                    migration["id"],
                                    approved_by=str(monitor["owner_id"]),
                                    max_total_actions=min(int(monitor["max_actions_per_run"]), len(eligible)),
                                    allowed_action_types=allowed,
                                )
                        monitor["last_checked_at"] = now.isoformat()
                        monitor["last_alert_id"] = alert["id"]
                        monitor["last_receipt_id"] = correction["receipt_id"] if correction else None
                        monitor["run_count"] = int(monitor.get("run_count", 0)) + 1
                        next_run = now + timedelta(minutes=int(monitor["interval_minutes"]))
                        if next_run < parse_datetime(str(monitor["expires_at"])):
                            self.store.schedule_once(
                                f"drift-{monitor_id}-{next_run.isoformat()}",
                                "drift.monitor",
                                next_run,
                                {"monitor_id": monitor_id},
                            )
                            monitor["next_run_at"] = next_run.isoformat()
                        else:
                            monitor["next_run_at"] = None
                        self._record(
                            kind="drift_monitors",
                            aggregate_id=monitor_id,
                            aggregate_type="drift_monitor",
                            event_type="drift_monitor.checked",
                            projection=monitor,
                            payload={
                                "alert_id": alert["id"],
                                "receipt_id": correction["receipt_id"] if correction else None,
                            },
                            actor_id="scheduler",
                        )
                        results.append(
                            {
                                "job_id": job["id"],
                                "status": "checked",
                                "monitor_id": monitor_id,
                                "alert_id": alert["id"],
                                "receipt_id": correction["receipt_id"] if correction else None,
                            }
                        )
                elif job["kind"] == "share.expire":
                    slice_id = str(job["payload"]["slice_id"])
                    value = self._projection("shares", slice_id)
                    if value.get("status") == "active":
                        value["status"] = "expired"
                        value["expired_at"] = now.isoformat()
                        self._record(
                            kind="shares",
                            aggregate_id=slice_id,
                            aggregate_type="shareable_passport_slice",
                            event_type="share.expired",
                            projection=value,
                            payload={"expired_at": now.isoformat()},
                            actor_id="scheduler",
                        )
                        self._invalidate_companions_for_slice(slice_id, reason="share_expired", actor_id="scheduler")
                    results.append({"job_id": job["id"], "status": "expired", "slice_id": slice_id})
                elif job["kind"] == "companion.expire":
                    companion_id = str(job["payload"]["companion_id"])
                    value = self._projection("companions", companion_id)
                    if value.get("status") == "active":
                        value["status"] = "expired"
                        value["expired_at"] = now.isoformat()
                        self._record(
                            kind="companions",
                            aggregate_id=companion_id,
                            aggregate_type="companion_blend",
                            event_type="companion.expired",
                            projection=value,
                            payload={"expired_at": now.isoformat()},
                            actor_id="scheduler",
                        )
                    results.append({"job_id": job["id"], "status": "expired", "companion_id": companion_id})
                elif job["kind"] == "overlay.activate":
                    overlay_id = str(job["payload"]["overlay_id"])
                    value = self._projection("overlays", overlay_id)
                    if value.get("status") == "scheduled":
                        value["status"] = "active"
                        value["activated_at"] = now.isoformat()
                        self._record(
                            kind="overlays",
                            aggregate_id=overlay_id,
                            aggregate_type="passport_overlay",
                            event_type="overlay.started",
                            projection=value,
                            payload={"activated_at": now.isoformat()},
                            actor_id="scheduler",
                        )
                    results.append({"job_id": job["id"], "status": "active", "overlay_id": overlay_id})
                elif job["kind"] == "overlay.expire":
                    overlay_id = str(job["payload"]["overlay_id"])
                    value = self._projection("overlays", overlay_id)
                    if value.get("status") == "revoked":
                        results.append({"job_id": job["id"], "status": "revoked", "overlay_id": overlay_id})
                        self.store.complete_job(job["id"], now)
                        continue
                    rollback_results = self._rollback_overlay_activations(
                        value,
                        actor_id=str(value["owner_id"]),
                    )
                    if value.get("status") != "expired":
                        value["status"] = "expired"
                        value["expired_at"] = now.isoformat()
                        self._record(
                            kind="overlays",
                            aggregate_id=overlay_id,
                            aggregate_type="passport_overlay",
                            event_type="overlay.expired",
                            projection=value,
                            payload={"expired_at": now.isoformat(), "rollbacks": rollback_results},
                            actor_id="scheduler",
                        )
                    results.append(
                        {
                            "job_id": job["id"],
                            "status": "expired",
                            "overlay_id": overlay_id,
                            "rollbacks": rollback_results,
                        }
                    )
                else:
                    raise InvalidStateError(f"unknown scheduled job kind: {job['kind']}")
                self.store.complete_job(job["id"], now)
            except Exception as exc:
                self.store.complete_job(job["id"], now, error=type(exc).__name__)
                results.append({"job_id": job["id"], "status": "failed", "error": type(exc).__name__})
        return tuple(results)

    def create_share_slice(
        self,
        *,
        passport_id: str,
        topic_names: tuple[str, ...],
        creator_ids: tuple[str, ...],
        include_serendipity: bool,
        include_formats: bool = False,
        include_exclusions: bool = False,
        expires_at: datetime,
        actor_id: str,
        scope: str = "snapshot",
        refresh_on_revision: bool = False,
        target_passport_ids: tuple[str, ...] = (),
        pair_id: str | None = None,
        counterparty_owner_id: str | None = None,
    ) -> dict[str, Any]:
        passport = self.get_passport(passport_id)
        if passport.owner_id != actor_id:
            raise PermissionError("only the Passport owner can share a slice")
        now = self._now()
        if expires_at <= now:
            raise ValueError("shared slice expiry must be in the future")
        if scope not in {"snapshot", "continuous"}:
            raise ValueError("share scope must be snapshot or continuous")
        targets = tuple(dict.fromkeys(target_passport_ids))
        if scope == "continuous":
            if not refresh_on_revision:
                raise ValueError("continuous consent must explicitly refresh on revision")
            if not targets:
                raise ValueError("continuous consent must opt in at least one target Passport")
            if not pair_id or not counterparty_owner_id:
                raise ValueError("continuous consent must bind a pair and counterparty")
            if counterparty_owner_id == actor_id:
                raise ValueError("continuous consent counterparty must be a different principal")
            for target_id in targets:
                target = self.get_passport(target_id)
                if target.owner_id != actor_id:
                    raise PermissionError(
                        "a continuous consent may target only Passports owned by its creator"
                    )
        elif refresh_on_revision or targets or pair_id is not None or counterparty_owner_id is not None:
            raise ValueError("snapshot consent cannot bind continuous sync fields")

        selected_fields = {
            "topic_names": sorted({key for key in topic_names if key in passport.topic_targets}),
            "creator_ids": sorted({key for key in creator_ids if key in passport.creator_preferences}),
            "include_serendipity": bool(include_serendipity),
            "include_formats": bool(include_formats),
            "include_exclusions": bool(include_exclusions),
        }
        if not (
            selected_fields["topic_names"]
            or selected_fields["creator_ids"]
            or include_formats
            or include_exclusions
            or include_serendipity
        ):
            raise ValueError("at least one existing Passport field must be explicitly shared")
        consent_id = self._id("consent")
        shared = self._materialize_share_slice(
            passport,
            selected_fields=selected_fields,
            expires_at=expires_at,
            consent_id=consent_id,
        )
        slice_id = self._id("slice")
        projection = {
            "id": slice_id,
            **to_primitive(shared),
            "status": "active",
            "revoked_at": None,
            "scope": scope,
            "refresh_on_revision": refresh_on_revision,
            "target_passport_ids": list(targets),
            "selected_fields": selected_fields,
            "last_refreshed_at": now.isoformat() if scope == "continuous" else None,
            "pair_id": pair_id if scope == "continuous" else None,
            "counterparty_owner_id": counterparty_owner_id if scope == "continuous" else None,
            "participant_owner_ids": (
                sorted((actor_id, str(counterparty_owner_id)))
                if scope == "continuous"
                else []
            ),
        }
        self._record(
            kind="shares",
            aggregate_id=slice_id,
            aggregate_type="shareable_passport_slice",
            event_type="share.created",
            projection=projection,
            payload={
                "passport_id": passport_id,
                "fields": [
                    field_name
                    for field_name, included in (
                        ("topics", bool(selected_fields["topic_names"])),
                        ("creators", bool(selected_fields["creator_ids"])),
                        ("formats", include_formats),
                        ("exclusions", include_exclusions),
                        ("serendipity", include_serendipity),
                    )
                    if included
                ],
                "scope": scope,
                "refresh_on_revision": refresh_on_revision,
                "target_passport_ids": list(targets),
                "pair_id": pair_id if scope == "continuous" else None,
                "counterparty_owner_id": (
                    counterparty_owner_id if scope == "continuous" else None
                ),
                "participant_owner_ids": (
                    sorted((actor_id, str(counterparty_owner_id)))
                    if scope == "continuous"
                    else []
                ),
            },
            actor_id=actor_id,
        )
        self.store.schedule_once(
            f"expire-share-{slice_id}",
            "share.expire",
            expires_at,
            {"slice_id": slice_id},
        )
        return projection

    def revoke_share_slice(self, slice_id: str, *, actor_id: str) -> dict[str, Any]:
        value = self._projection("shares", slice_id)
        if value["owner_id"] != actor_id:
            raise PermissionError("only the slice owner can revoke it")
        if value.get("status") == "revoked":
            return value
        value["status"] = "revoked"
        value["revoked_at"] = self._now().isoformat()
        self._record(
            kind="shares",
            aggregate_id=slice_id,
            aggregate_type="shareable_passport_slice",
            event_type="share.revoked",
            projection=value,
            payload={"revoked_at": value["revoked_at"]},
            actor_id=actor_id,
        )
        self._invalidate_companions_for_slice(slice_id, reason="share_revoked", actor_id=actor_id)
        return value

    def create_companion_blend(
        self,
        *,
        name: str,
        slice_ids: tuple[str, ...],
        weights: Mapping[str, float],
        strategy: str,
        expires_at: datetime,
        actor_id: str,
        scope: str = "snapshot",
    ) -> dict[str, Any]:
        if scope not in {"snapshot", "continuous"}:
            raise ValueError("companion scope must be snapshot or continuous")
        if len(set(slice_ids)) != len(slice_ids):
            raise ValueError("a companion blend cannot reuse a consent slice")
        if scope == "continuous" and len(slice_ids) != 2:
            raise ValueError("continuous companion sync requires exactly two consent slices")
        values = [self._projection("shares", slice_id) for slice_id in slice_ids]
        now = self._now()
        if any(
            value.get("status") != "active" or parse_datetime(str(value["expires_at"])) <= now
            for value in values
        ):
            raise InvalidStateError("every shared slice must be active")
        if any(value.get("scope", "snapshot") != scope for value in values):
            raise InvalidStateError("every shared slice must grant the requested companion scope")
        slices = tuple(slice_from_dict(value) for value in values)
        if actor_id not in {item.owner_id for item in slices}:
            raise PermissionError("a companion blend must be created by a participant")
        if scope == "continuous":
            if any(not value.get("refresh_on_revision") for value in values):
                raise InvalidStateError("every continuous consent must allow refresh on revision")
            values = [self._refresh_continuous_share(value, actor_id="companion-sync") for value in values]
            slices = tuple(slice_from_dict(value) for value in values)
            self._validate_continuous_targets(values)
        source_passport_ids = [str(value["passport_id"]) for value in values]
        target_passport_ids = sorted(
            {
                str(target_id)
                for value in values
                for target_id in value.get("target_passport_ids", ())
            }
        )
        selected_by_passport = {
            str(value["passport_id"]): dict(value.get("selected_fields", {}))
            for value in values
        }
        blend = blend_slices(
            blend_id=self._id("companion"),
            name=name,
            slices=slices,
            weights=weights,
            strategy=strategy,
            created_at=now,
            expires_at=expires_at,
        )
        projection = {
            **to_primitive(blend),
            "status": "active",
            "slice_ids": list(slice_ids),
            "requested_weights": {
                owner_id: float(weights.get(owner_id, 1.0))
                for owner_id in blend.participant_ids
            },
            "scope": scope,
            "refresh_on_revision": scope == "continuous",
            "source_passport_ids": source_passport_ids,
            "source_passport_versions": {
                str(value["passport_id"]): int(value["passport_version"])
                for value in values
            },
            "target_passport_ids": target_passport_ids,
            "participant_selected_fields": selected_by_passport,
            "selected_fields": self._combined_selected_fields(values),
            "sync_revision": 1 if scope == "continuous" else 0,
            "last_synced_at": now.isoformat() if scope == "continuous" else None,
            "pair_id": values[0].get("pair_id") if scope == "continuous" else None,
        }
        self._record(
            kind="companions",
            aggregate_id=blend.id,
            aggregate_type="companion_blend",
            event_type="companion.created",
            projection=projection,
            payload={
                "participant_ids": list(blend.participant_ids),
                "strategy": strategy,
                "scope": scope,
                "source_passport_ids": source_passport_ids,
                "target_passport_ids": target_passport_ids,
            },
            actor_id=actor_id,
        )
        self.store.schedule_once(
            f"expire-companion-{blend.id}",
            "companion.expire",
            blend.expires_at,
            {"companion_id": blend.id},
        )
        return projection

    def prepare_migration(
        self,
        *,
        passport_id: str,
        platform: str,
        destination_account_id: str,
        actor_id: str,
        overlay_id: str | None = None,
    ) -> dict[str, Any]:
        base_passport = self.get_passport(passport_id)
        if base_passport.owner_id != actor_id:
            raise PermissionError("only the Passport owner can prepare a migration")
        if overlay_id is not None:
            overlay_value = self._projection("overlays", overlay_id)
            if overlay_value["base_passport_id"] != passport_id:
                raise InvalidStateError("visa targets a different Passport")
            overlay = overlay_from_dict(overlay_value)
            if overlay_value.get("status") in {"expired", "revoked"} or not overlay.is_active(self._now()):
                raise InvalidStateError("visa must be active before it can be activated live")
            if overlay_value.get("mode") != OverlayMode.REVERSIBLE_LIVE.value:
                raise InvalidStateError("only reversible-live visas can target a platform account")
        passport = self.effective_passport(passport_id)
        adapter = self._adapter(platform)
        if self._is_live_adapter(adapter):
            if self.connections is None:
                raise InvalidStateError("live platform execution requires an owner-bound connection registry")
            connection = self.connections.get_connection(
                destination_account_id,
                owner_id=actor_id,
            )
            if connection.platform != platform:
                raise ValueError("destination connection belongs to a different platform")
        now = self._now()
        observation = adapter.observe(destination_account_id, now=now, sample_size=24)
        before = evaluate_feed(passport, observation.sample)
        plan = adapter.compile(passport, observation, now=now)

        preview_adapter = adapter.clone() if hasattr(adapter, "clone") else None
        preview = None
        if preview_adapter is not None:
            for action in plan.actions:
                preview_adapter.execute(destination_account_id, action, now=now)
            preview_sample = preview_adapter.sample(destination_account_id, now=now, limit=24)
            preview = evaluate_feed(passport, preview_sample)

        migration_id = self._id("migration")
        projection = {
            "id": migration_id,
            "status": "awaiting_approval",
            "passport_id": passport.id,
            "owner_id": actor_id,
            "passport_version": passport.version,
            "effective_passport_fingerprint": self._passport_fingerprint(passport),
            "platform": platform,
            "destination_account_id": destination_account_id,
            "destination_connection_id": (
                destination_account_id if self._is_live_adapter(adapter) else None
            ),
            "overlay_id": overlay_id,
            "plan": to_primitive(plan),
            "before": to_primitive(before),
            "preview": to_primitive(preview) if preview is not None else None,
            "created_at": now.isoformat(),
            "approved_at": None,
            "receipt_id": None,
        }
        self._record(
            kind="migrations",
            aggregate_id=migration_id,
            aggregate_type="migration",
            event_type="migration.prepared",
            projection=projection,
            payload={"passport_id": passport.id, "platform": platform, "action_count": len(plan.actions)},
            actor_id=actor_id,
        )
        return projection

    def execute_migration(
        self,
        migration_id: str,
        *,
        approved_by: str,
        max_total_actions: int | None = None,
        allowed_action_types: frozenset[ActionType] | None = None,
    ) -> dict[str, Any]:
        with self._execution_locks_guard:
            lock = self._execution_locks.setdefault(migration_id, RLock())
        with lock:
            return self._execute_migration_locked(
                migration_id,
                approved_by=approved_by,
                max_total_actions=max_total_actions,
                allowed_action_types=allowed_action_types,
            )

    def _execute_migration_locked(
        self,
        migration_id: str,
        *,
        approved_by: str,
        max_total_actions: int | None = None,
        allowed_action_types: frozenset[ActionType] | None = None,
    ) -> dict[str, Any]:
        stored_migration = self.store.get_projection("migrations", migration_id)
        if stored_migration is None:
            raise NotFoundError(f"migrations:{migration_id}")
        migration_version, migration_value = stored_migration
        migration = dict(migration_value)
        base_passport = self.get_passport(str(migration["passport_id"]))
        if base_passport.owner_id != approved_by:
            raise PermissionError("only the Passport owner can approve a migration")
        existing_receipt = self._migration_receipt(
            migration,
            owner_id=approved_by,
        )
        if existing_receipt is not None:
            return self._finalize_migration_receipt(
                migration,
                existing_receipt,
                actor_id=approved_by,
            )
        migration_status = str(migration["status"])
        if migration_status not in {"awaiting_approval", "failed_recoverable", "executing"}:
            raise InvalidStateError(f"migration cannot execute from {migration['status']}")
        passport = self.effective_passport(str(migration["passport_id"]))
        if passport.version != int(migration["passport_version"]):
            raise InvalidStateError("Passport changed after preview; prepare a new migration")
        expected_fingerprint = migration.get("effective_passport_fingerprint")
        if expected_fingerprint and expected_fingerprint != self._passport_fingerprint(passport):
            raise InvalidStateError("effective Passport changed after preview; prepare a new migration")
        platform = str(migration["platform"])
        account_id = str(migration["destination_account_id"])
        adapter = self._adapter(platform)
        is_live = self._is_live_adapter(adapter)
        if migration_status == "executing":
            claim_owner = migration.get("execution_claim_owner")
            claim_expires_at = migration.get("execution_claim_expires_at")
            if (
                isinstance(claim_owner, str)
                and claim_owner
                and claim_owner != self._execution_owner
                and isinstance(claim_expires_at, str)
                and parse_datetime(claim_expires_at) > self._now()
            ):
                raise InvalidStateError("migration execution is already claimed by another worker")
            if not is_live:
                raise InvalidStateError("only a live migration can resume from executing")
            attempts = self.action_journal.list_remote_actions(
                owner_id=approved_by,
                migration_id=migration_id,
                connection_id=account_id,
            )
            if attempts:
                raise InvalidStateError(
                    "an executing migration with durable remote action reservations must reconcile first"
                )
        actions = tuple(
            action
            for action in (action_from_dict(item) for item in migration["plan"]["actions"])
            if allowed_action_types is None or action.action_type in allowed_action_types
        )
        budget = min(max_total_actions or len(actions), len(actions))
        if budget < 1 and actions:
            raise ValueError("an executable migration needs a positive action budget")
        now = self._now()
        counts = Counter(action.action_type for action in actions[:budget])
        envelope = ActionEnvelope(
            id=self._id("envelope"),
            passport_id=passport.id,
            passport_version=passport.version,
            destination_ids=frozenset({account_id}),
            allowed_actions=frozenset(counts),
            max_total_actions=max(1, budget),
            max_per_type=dict(counts),
            expires_at=now + timedelta(minutes=15),
            approved_by=approved_by,
            approved_at=now,
            stop_conditions=("target reached", "budget exhausted", "unsupported capability", "human judgment"),
        )
        capability = adapter.capabilities(account_id)
        ledger = ActionLedger()
        prior: list[tuple[ActionType, ActionStatus]] = []
        decisions: list[dict[str, Any]] = []
        trace_id = str(migration.get("trace_id") or self._id("trace"))
        migration.update(
            {
                "status": "executing",
                "approved_at": now.isoformat(),
                "approved_by": approved_by,
                "envelope": to_primitive(envelope),
                "trace_id": trace_id,
                "execution_started_at": self._now().isoformat(),
                "execution_claim_owner": self._execution_owner,
                "execution_claim_expires_at": (self._now() + timedelta(minutes=2)).isoformat(),
            }
        )
        self._record(
            kind="migrations",
            aggregate_id=migration_id,
            aggregate_type="migration",
            event_type="migration.execution_started",
            projection=migration,
            payload={
                "platform": platform,
                "destination_account_id": account_id,
                "action_count": budget,
                "live_transport": is_live,
            },
            actor_id=approved_by,
            trace_id=trace_id,
            expected_projection_version=migration_version,
        )
        pause_status: str | None = None
        final_failure = False
        for action in actions[:budget]:
            decision = self.guard.check(
                action=action,
                envelope=envelope,
                capability=capability,
                prior_actions=prior,
                now=self._now(),
            )
            decisions.append({"action_id": action.id, **to_primitive(decision)})
            if not decision.allowed:
                continue
            if decision.requires_handoff:
                prior.append((action.action_type, ActionStatus.GUIDED))
                continue
            if is_live:
                outcome, pause_status = self._execute_live_action(
                    adapter=adapter,
                    migration_id=migration_id,
                    owner_id=approved_by,
                    connection_id=account_id,
                    action=action,
                )
            else:
                outcome = adapter.execute(account_id, action, now=self._now())
            if outcome is None:
                break
            ledger.record(outcome)
            prior.append((action.action_type, outcome.status))
            if outcome.status is ActionStatus.FAILED:
                final_failure = pause_status is None
            if pause_status is not None or final_failure:
                break

        if pause_status is not None:
            migration.update(
                {
                    "status": pause_status,
                    "decisions": decisions,
                    "journal_attempts": [
                        to_primitive(value)
                        for value in self.action_journal.list_remote_actions(
                            owner_id=approved_by,
                            migration_id=migration_id,
                            connection_id=account_id,
                        )
                    ],
                    "paused_at": self._now().isoformat(),
                }
            )
            self._record(
                kind="migrations",
                aggregate_id=migration_id,
                aggregate_type="migration",
                event_type=f"migration.{pause_status}",
                projection=migration,
                payload={"platform": platform, "status": pause_status},
                actor_id=approved_by,
                trace_id=trace_id,
            )
            return migration

        after_sample = adapter.sample(account_id, now=self._now(), limit=24)
        evaluation = evaluate_feed(passport, after_sample)
        receipt = ledger.issue_receipt(
            receipt_id=self._id("receipt"),
            passport_id=passport.id,
            passport_version=passport.version,
            destination_id=account_id,
            issued_at=self._now(),
            trace_id=trace_id,
            previous_checkpoint_id=f"checkpoint-{migration_id}",
            rollback_caveats=(
                "A platform may retain recommendation-learning signals after reversible controls are restored.",
            ),
        )
        migration.update(
            {
                "status": (
                    "issued_partial"
                    if final_failure
                    else "issued"
                    if evaluation.stop_reason is StopReason.TARGET_REACHED
                    else "issued_with_drift"
                ),
                "approved_at": now.isoformat(),
                "approved_by": approved_by,
                "envelope": to_primitive(envelope),
                "decisions": decisions,
                "after": to_primitive(evaluation),
                "receipt_id": receipt.id,
                "journal_attempts": [
                    to_primitive(value)
                    for value in self.action_journal.list_remote_actions(
                        owner_id=approved_by,
                        migration_id=migration_id,
                        connection_id=account_id,
                    )
                ]
                if is_live
                else [],
                "completion_stop_reason": evaluation.stop_reason.value,
                "completed_at": self._now().isoformat(),
            }
        )
        adapter_state_finalization = (
            to_primitive(adapter.export_state(account_id))
            if hasattr(adapter, "export_state")
            else None
        )
        receipt_projection = {
            **to_primitive(receipt),
            "platform": platform,
            "owner_id": approved_by,
            "migration_id": migration_id,
            "migration_finalization": dict(migration),
            "adapter_state_finalization": adapter_state_finalization,
        }
        self._record(
            kind="receipts",
            aggregate_id=receipt.id,
            aggregate_type="action_receipt",
            event_type="receipt.issued",
            projection=receipt_projection,
            payload={
                "migration_id": migration_id,
                "platform": platform,
                "outcome_count": len(receipt.outcomes),
            },
            actor_id=approved_by,
            trace_id=trace_id,
        )
        return self._finalize_migration_receipt(
            migration,
            receipt_projection,
            actor_id=approved_by,
        )

    def _migration_receipt(
        self,
        migration: Mapping[str, Any],
        *,
        owner_id: str,
    ) -> dict[str, Any] | None:
        migration_id = str(migration["id"])
        matches = [
            dict(value)
            for value in self.projection_list("receipts")
            if value.get("migration_id") == migration_id
        ]
        if len(matches) > 1:
            raise InvalidStateError("migration is bound to more than one receipt")
        if not matches:
            return None
        receipt = matches[0]
        if (
            receipt.get("owner_id") != owner_id
            or receipt.get("platform") != migration.get("platform")
            or receipt.get("destination_id") != migration.get("destination_account_id")
            or receipt.get("passport_id") != migration.get("passport_id")
            or int(receipt.get("passport_version", 0)) != int(migration["passport_version"])
        ):
            raise InvalidStateError("migration receipt binding is invalid")
        return receipt

    def _finalize_migration_receipt(
        self,
        migration: Mapping[str, Any],
        receipt: Mapping[str, Any],
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        snapshot = receipt.get("migration_finalization")
        if not isinstance(snapshot, Mapping):
            if migration.get("receipt_id") == receipt.get("id"):
                return dict(migration)
            raise InvalidStateError(
                "receipt lacks the durable migration finalization snapshot"
            )
        finalization = dict(snapshot)
        receipt_id = str(receipt["id"])
        migration_id = str(migration["id"])
        if (
            finalization.get("id") != migration_id
            or finalization.get("receipt_id") != receipt_id
            or finalization.get("owner_id") != actor_id
            or finalization.get("platform") != receipt.get("platform")
            or finalization.get("destination_account_id") != receipt.get("destination_id")
            or finalization.get("passport_id") != receipt.get("passport_id")
            or int(finalization.get("passport_version", 0))
            != int(receipt.get("passport_version", 0))
        ):
            raise InvalidStateError("receipt migration finalization binding is invalid")
        trace_id = str(finalization.get("trace_id") or receipt.get("trace_id") or "")
        if not trace_id:
            raise InvalidStateError("receipt migration finalization is missing its trace")

        adapter = self._adapter(str(receipt["platform"]))
        adapter_state = receipt.get("adapter_state_finalization")
        if adapter_state is not None:
            if not isinstance(adapter_state, Mapping) or not hasattr(adapter, "import_state"):
                raise InvalidStateError("receipt adapter finalization snapshot is invalid")
            adapter.import_state(dict(adapter_state))
            if not hasattr(adapter, "export_state") or to_primitive(
                adapter.export_state(str(receipt["destination_id"]))
            ) != to_primitive(adapter_state):
                raise InvalidStateError("receipt adapter finalization snapshot could not be restored")
        self._persist_adapter_state(
            str(receipt["platform"]),
            str(receipt["destination_id"]),
            actor_id,
            trace_id,
        )
        current = self._projection("migrations", migration_id)
        if current.get("receipt_id") != receipt_id or current.get("status") != finalization.get("status"):
            self._record(
                kind="migrations",
                aggregate_id=migration_id,
                aggregate_type="migration",
                event_type="migration.executed",
                projection=finalization,
                payload={
                    "receipt_id": receipt_id,
                    "stop_reason": finalization.get("completion_stop_reason", "unknown"),
                },
                actor_id=actor_id,
                trace_id=trace_id,
            )
            current = finalization
        self._activate_overlay_once(current, receipt, actor_id=actor_id, trace_id=trace_id)
        return dict(current)

    def _activate_overlay_once(
        self,
        migration: Mapping[str, Any],
        receipt: Mapping[str, Any],
        *,
        actor_id: str,
        trace_id: str,
    ) -> None:
        overlay_id = migration.get("overlay_id")
        if not overlay_id:
            return
        overlay = self._projection("overlays", str(overlay_id))
        activations = list(overlay.get("activations", ()))
        matching = [
            value
            for value in activations
            if value.get("migration_id") == migration.get("id")
        ]
        if matching:
            if len(matching) != 1 or matching[0].get("receipt_id") != receipt.get("id"):
                raise InvalidStateError("overlay activation binding is inconsistent")
            return
        activation = {
            "platform": receipt["platform"],
            "account_id": receipt["destination_id"],
            "migration_id": migration["id"],
            "receipt_id": receipt["id"],
            "status": "active",
            "activated_at": self._now().isoformat(),
        }
        overlay["activations"] = [*activations, activation]
        self._record(
            kind="overlays",
            aggregate_id=str(overlay_id),
            aggregate_type="passport_overlay",
            event_type="overlay.activated",
            projection=overlay,
            payload={
                "migration_id": migration["id"],
                "receipt_id": receipt["id"],
                "platform": receipt["platform"],
            },
            actor_id=actor_id,
            trace_id=trace_id,
        )

    def reconcile_migration(
        self,
        migration_id: str,
        *,
        actor_id: str,
        respect_retry_schedule: bool = False,
    ) -> dict[str, Any]:
        """Resolve uncertain remote writes by observation without replaying them."""

        migration = self._projection("migrations", migration_id)
        passport = self.get_passport(str(migration["passport_id"]))
        if passport.owner_id != actor_id:
            raise PermissionError("only the Passport owner can reconcile a migration")
        if migration.get("status") not in {
            "reconciliation_required",
            "failed_recoverable",
            "executing",
        }:
            raise InvalidStateError(f"migration cannot reconcile from {migration.get('status')}")
        platform = str(migration["platform"])
        connection_id = str(migration["destination_account_id"])
        adapter = self._adapter(platform)
        if not self._is_live_adapter(adapter):
            raise InvalidStateError("only a live transport migration has remote actions to reconcile")
        now = self._now()
        attempts = list(
            self.action_journal.list_remote_actions(
                owner_id=actor_id,
                migration_id=migration_id,
                connection_id=connection_id,
            )
        )
        if not attempts:
            raise InvalidStateError("migration has no durable remote action reservations")
        for attempt in attempts:
            current = attempt
            if (
                current.state in {
                    RemoteActionState.DISPATCHING,
                    RemoteActionState.RECONCILING,
                }
                and current.lease_expires_at is not None
                and current.lease_expires_at <= now
            ):
                expired_state = current.state
                current = self.action_journal.transition_remote_action(
                    current.id,
                    expected_state=expired_state,
                    new_state=RemoteActionState.UNKNOWN,
                    updated_at=now,
                    error_code=(
                        "dispatch_lease_expired"
                        if expired_state is RemoteActionState.DISPATCHING
                        else "reconciliation_lease_expired"
                    ),
                )
            if current.state is not RemoteActionState.UNKNOWN:
                continue
            if (
                respect_retry_schedule
                and current.next_attempt_at is not None
                and current.next_attempt_at > now
            ):
                continue
            if (
                respect_retry_schedule
                and current.attempt_count >= _REMOTE_RECOVERY_MAX_ATTEMPTS
            ):
                self.action_journal.transition_remote_action(
                    current.id,
                    expected_state=RemoteActionState.UNKNOWN,
                    new_state=RemoteActionState.NEEDS_HUMAN,
                    updated_at=now,
                    error_code="reconciliation_attempt_limit",
                )
                continue
            current = self.action_journal.transition_remote_action(
                current.id,
                expected_state=RemoteActionState.UNKNOWN,
                new_state=RemoteActionState.RECONCILING,
                updated_at=now,
                lease_owner=f"reconcile:{migration_id}",
                lease_expires_at=now + timedelta(minutes=2),
            )
            prepared = self._prepared_from_attempt(current)
            try:
                outcome = adapter.reconcile_remote_action(prepared, now=self._now())
            except RemoteOutcomeUnknown as exc:
                self._defer_or_stop_remote_recovery(current, error_code=exc.code)
            except LivePlatformError as exc:
                self._defer_or_stop_remote_recovery(current, error_code=exc.code)
            except Exception:
                self._defer_or_stop_remote_recovery(
                    current,
                    error_code="reconciliation_interrupted",
                )
            else:
                target = (
                    RemoteActionState.SUCCEEDED
                    if outcome.status in {ActionStatus.EXECUTED, ActionStatus.SKIPPED}
                    else RemoteActionState.FAILED_RETRYABLE
                )
                self.action_journal.transition_remote_action(
                    current.id,
                    expected_state=RemoteActionState.RECONCILING,
                    new_state=target,
                    updated_at=self._now(),
                    platform_reference=outcome.platform_reference,
                    before_state=outcome.before_state,
                    after_state=outcome.after_state,
                    error_code=outcome.error_code,
                    next_attempt_at=(
                        self._now() + timedelta(seconds=5)
                        if target is RemoteActionState.FAILED_RETRYABLE
                        else None
                    ),
                )
        refreshed = self.action_journal.list_remote_actions(
            owner_id=actor_id,
            migration_id=migration_id,
            connection_id=connection_id,
        )
        states = {value.state for value in refreshed}
        if states & {
            RemoteActionState.UNKNOWN,
            RemoteActionState.RECONCILING,
            RemoteActionState.DISPATCHING,
        }:
            status = "reconciliation_required"
        elif RemoteActionState.NEEDS_HUMAN in states:
            status = "needs_human"
        elif RemoteActionState.FAILED_FINAL in states:
            status = "failed_final"
        else:
            status = "failed_recoverable"
        migration.update(
            {
                "status": status,
                "journal_attempts": [to_primitive(value) for value in refreshed],
                "reconciled_at": self._now().isoformat(),
            }
        )
        self._record(
            kind="migrations",
            aggregate_id=migration_id,
            aggregate_type="migration",
            event_type="migration.reconciled",
            projection=migration,
            payload={"status": status, "attempt_count": len(refreshed)},
            actor_id=actor_id,
            trace_id=str(migration.get("trace_id") or self._id("trace")),
        )
        return migration

    def _defer_or_stop_remote_recovery(
        self,
        attempt: RemoteActionAttempt,
        *,
        error_code: str,
    ) -> RemoteActionAttempt:
        failed_at = self._now()
        exhausted = attempt.attempt_count >= _REMOTE_RECOVERY_MAX_ATTEMPTS
        return self.action_journal.transition_remote_action(
            attempt.id,
            expected_state=RemoteActionState.RECONCILING,
            new_state=(
                RemoteActionState.NEEDS_HUMAN if exhausted else RemoteActionState.UNKNOWN
            ),
            updated_at=failed_at,
            error_code=("reconciliation_attempt_limit" if exhausted else error_code),
            next_attempt_at=(
                None
                if exhausted
                else failed_at + _remote_recovery_delay(attempt.attempt_count)
            ),
        )

    def _defer_or_stop_rollback_recovery(
        self,
        attempt: RemoteActionAttempt,
        *,
        error_code: str,
    ) -> RemoteActionAttempt:
        failed_at = self._now()
        exhausted = attempt.attempt_count >= _REMOTE_RECOVERY_MAX_ATTEMPTS
        return self.action_journal.transition_remote_action(
            attempt.id,
            expected_state=RemoteActionState.ROLLBACK_PENDING,
            new_state=(
                RemoteActionState.NEEDS_HUMAN
                if exhausted
                else RemoteActionState.ROLLBACK_UNKNOWN
            ),
            updated_at=failed_at,
            error_code=("rollback_attempt_limit" if exhausted else error_code),
            next_attempt_at=(
                None
                if exhausted
                else failed_at + _remote_recovery_delay(attempt.attempt_count)
            ),
        )

    def recover_uncertain_remote_actions(
        self,
        *,
        at: datetime | None = None,
        limit: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        """Observe expired uncertain writes and resume explicitly authorized rollbacks.

        This runtime recovery pass never dispatches a pending or retryable
        forward mutation. Those require a fresh user-approved execute request.
        It may observe an already-dispatched write, or continue a rollback that
        is already bound to a durable receipt.
        """

        now = at or self._now()
        recoverable = self.action_journal.list_recoverable_remote_actions(now, limit=limit)
        grouped: dict[tuple[str, str], list[RemoteActionAttempt]] = {}
        for attempt in recoverable:
            grouped.setdefault((attempt.owner_id, attempt.migration_id), []).append(attempt)
        results: list[dict[str, Any]] = []
        forward_uncertain = {
            RemoteActionState.UNKNOWN,
            RemoteActionState.DISPATCHING,
            RemoteActionState.RECONCILING,
        }
        rollback_uncertain = {
            RemoteActionState.ROLLBACK_UNKNOWN,
            RemoteActionState.ROLLBACK_PENDING,
        }
        for (owner_id, migration_id), attempts in sorted(grouped.items()):
            states = {attempt.state for attempt in attempts}
            if states & forward_uncertain:
                try:
                    migration = self.reconcile_migration(
                        migration_id,
                        actor_id=owner_id,
                        respect_retry_schedule=True,
                    )
                except ConcurrencyConflict:
                    results.append(
                        {
                            "migration_id": migration_id,
                            "owner_id": owner_id,
                            "status": "claimed_elsewhere",
                            "operation": "reconcile",
                        }
                    )
                except (InvalidStateError, NotFoundError, KeyError):
                    self._mark_remote_attempts_needs_human(
                        attempts,
                        at=now,
                        error_code="automatic_reconciliation_unavailable",
                    )
                    results.append(
                        {
                            "migration_id": migration_id,
                            "owner_id": owner_id,
                            "status": "needs_human",
                            "operation": "reconcile",
                        }
                    )
                else:
                    results.append(
                        {
                            "migration_id": migration_id,
                            "owner_id": owner_id,
                            "status": migration["status"],
                            "operation": "reconcile",
                        }
                    )
                continue
            if not states & rollback_uncertain:
                continue
            receipt = next(
                (
                    value
                    for value in self.projection_list("receipts")
                    if value.get("migration_id") == migration_id
                    and value.get("owner_id") == owner_id
                    and value.get("status") != "rolled_back"
                ),
                None,
            )
            if receipt is None:
                self._mark_remote_attempts_needs_human(
                    attempts,
                    at=now,
                    error_code="rollback_receipt_unavailable",
                )
                results.append(
                    {
                        "migration_id": migration_id,
                        "owner_id": owner_id,
                        "status": "needs_human",
                        "operation": "rollback",
                    }
                )
                continue
            try:
                recovered = self.rollback_receipt(
                    str(receipt["id"]),
                    actor_id=owner_id,
                    platform=str(receipt["platform"]),
                )
            except ConcurrencyConflict:
                status = "claimed_elsewhere"
            except (InvalidStateError, NotFoundError, KeyError):
                self._mark_remote_attempts_needs_human(
                    attempts,
                    at=now,
                    error_code="automatic_rollback_unavailable",
                )
                status = "needs_human"
            else:
                status = str(recovered["status"])
            results.append(
                {
                    "migration_id": migration_id,
                    "owner_id": owner_id,
                    "status": status,
                    "operation": "rollback",
                }
            )
        return tuple(results)

    def _mark_remote_attempts_needs_human(
        self,
        attempts: Iterable[RemoteActionAttempt],
        *,
        at: datetime,
        error_code: str,
    ) -> None:
        eligible = {
            RemoteActionState.PENDING,
            RemoteActionState.DISPATCHING,
            RemoteActionState.UNKNOWN,
            RemoteActionState.RECONCILING,
            RemoteActionState.FAILED_RETRYABLE,
            RemoteActionState.SUCCEEDED,
            RemoteActionState.ROLLBACK_PENDING,
            RemoteActionState.ROLLBACK_UNKNOWN,
        }
        for attempt in attempts:
            try:
                current = self.action_journal.get_remote_action(
                    attempt.id,
                    owner_id=attempt.owner_id,
                )
                if current.state not in eligible:
                    continue
                self.action_journal.transition_remote_action(
                    current.id,
                    expected_state=current.state,
                    new_state=RemoteActionState.NEEDS_HUMAN,
                    updated_at=at,
                    error_code=error_code,
                )
            except (ConcurrencyConflict, KeyError, ValueError):
                continue

    def _execute_live_action(
        self,
        *,
        adapter: Any,
        migration_id: str,
        owner_id: str,
        connection_id: str,
        action: Any,
    ) -> tuple[ActionOutcome | None, str | None]:
        now = self._now()
        action = self._migration_scoped_action(migration_id, action)
        attempt_id = self._remote_attempt_id(migration_id, action.id, action.idempotency_key)
        platform = str(adapter.platform)
        try:
            attempt = self.action_journal.get_remote_action(attempt_id, owner_id=owner_id)
        except KeyError:
            prepared = adapter.prepare_remote_action(connection_id, action, now=now)
            self._validate_fresh_prepared_action(
                prepared,
                platform=platform,
                connection_id=connection_id,
                action=action,
            )
            try:
                attempt = self.action_journal.reserve_remote_action(
                    attempt_id=attempt_id,
                    owner_id=owner_id,
                    connection_id=connection_id,
                    platform=platform,
                    migration_id=migration_id,
                    action_id=action.id,
                    operation=action.action_type.value,
                    idempotency_key=action.idempotency_key,
                    request_fingerprint=prepared.idempotency_fingerprint,
                    action_payload=self._prepared_payload(prepared),
                    created_at=now,
                )
            except ConcurrencyConflict:
                # Another runtime may have reserved and dispatched between the
                # lookup and prepare. Its persisted baseline is authoritative;
                # never prepare again from the now-mutated remote state.
                attempt = self.action_journal.get_remote_action(
                    attempt_id,
                    owner_id=owner_id,
                )
        prepared = self._prepared_from_bound_attempt(
            attempt,
            attempt_id=attempt_id,
            owner_id=owner_id,
            connection_id=connection_id,
            platform=platform,
            migration_id=migration_id,
            action=action,
        )
        if attempt.state is RemoteActionState.SUCCEEDED:
            return self._outcome_from_attempt(attempt), None
        if attempt.state is RemoteActionState.FAILED_FINAL:
            return self._outcome_from_attempt(attempt, failed=True), None
        if (
            attempt.state is RemoteActionState.FAILED_RETRYABLE
            and attempt.next_attempt_at is not None
            and attempt.next_attempt_at > now
        ):
            return None, "failed_recoverable"
        if attempt.state is RemoteActionState.NEEDS_HUMAN:
            return None, "needs_human"
        if attempt.state in {
            RemoteActionState.UNKNOWN,
            RemoteActionState.RECONCILING,
            RemoteActionState.ROLLBACK_PENDING,
            RemoteActionState.ROLLBACK_UNKNOWN,
            RemoteActionState.ROLLED_BACK,
        }:
            return None, "reconciliation_required"
        if attempt.state is RemoteActionState.DISPATCHING:
            if attempt.lease_expires_at is None or attempt.lease_expires_at > now:
                return None, "reconciliation_required"
            attempt = self.action_journal.transition_remote_action(
                attempt.id,
                expected_state=RemoteActionState.DISPATCHING,
                new_state=RemoteActionState.UNKNOWN,
                updated_at=now,
                error_code="dispatch_lease_expired",
            )
            return None, "reconciliation_required"
        previous_state = attempt.state
        attempt = self.action_journal.transition_remote_action(
            attempt.id,
            expected_state=previous_state,
            new_state=RemoteActionState.DISPATCHING,
            updated_at=now,
            lease_owner=f"execute:{migration_id}",
            lease_expires_at=now + timedelta(minutes=2),
        )
        if previous_state is RemoteActionState.FAILED_RETRYABLE:
            try:
                reconciled = adapter.reconcile_remote_action(prepared, now=self._now())
            except RemoteOutcomeUnknown as exc:
                self.action_journal.transition_remote_action(
                    attempt.id,
                    expected_state=RemoteActionState.DISPATCHING,
                    new_state=RemoteActionState.UNKNOWN,
                    updated_at=self._now(),
                    error_code=exc.code,
                )
                return None, "reconciliation_required"
            except LivePlatformError as exc:
                self.action_journal.transition_remote_action(
                    attempt.id,
                    expected_state=RemoteActionState.DISPATCHING,
                    new_state=RemoteActionState.FAILED_RETRYABLE,
                    updated_at=self._now(),
                    next_attempt_at=self._now() + timedelta(seconds=5),
                    error_code=exc.code,
                )
                return None, "failed_recoverable"
            except Exception:
                self.action_journal.transition_remote_action(
                    attempt.id,
                    expected_state=RemoteActionState.DISPATCHING,
                    new_state=RemoteActionState.FAILED_RETRYABLE,
                    updated_at=self._now(),
                    next_attempt_at=self._now() + timedelta(seconds=5),
                    error_code="reconciliation_read_interrupted",
                )
                return None, "failed_recoverable"
            if reconciled.status in {ActionStatus.EXECUTED, ActionStatus.SKIPPED}:
                succeeded = self.action_journal.transition_remote_action(
                    attempt.id,
                    expected_state=RemoteActionState.DISPATCHING,
                    new_state=RemoteActionState.SUCCEEDED,
                    updated_at=self._now(),
                    platform_reference=reconciled.platform_reference,
                    before_state=reconciled.before_state,
                    after_state=reconciled.after_state,
                    error_code=reconciled.error_code,
                )
                return self._outcome_from_attempt(succeeded), None
        try:
            outcome = adapter.apply_prepared_action(prepared, now=self._now())
        except RemoteOutcomeUnknown as exc:
            self.action_journal.transition_remote_action(
                attempt.id,
                expected_state=RemoteActionState.DISPATCHING,
                new_state=RemoteActionState.UNKNOWN,
                updated_at=self._now(),
                error_code=exc.code,
            )
            return None, "reconciliation_required"
        except LivePlatformError as exc:
            if exc.outcome_unknown:
                state = RemoteActionState.UNKNOWN
                pause = "reconciliation_required"
            elif exc.retryable:
                state = RemoteActionState.FAILED_RETRYABLE
                pause = "failed_recoverable"
            else:
                state = RemoteActionState.FAILED_FINAL
                pause = None
            failed = self.action_journal.transition_remote_action(
                attempt.id,
                expected_state=RemoteActionState.DISPATCHING,
                new_state=state,
                updated_at=self._now(),
                next_attempt_at=(
                    self._now() + timedelta(seconds=max(5, getattr(exc, "retry_after_seconds", 5) or 5))
                    if state is RemoteActionState.FAILED_RETRYABLE
                    else None
                ),
                before_state=prepared.before_state,
                after_state=prepared.before_state,
                error_code=exc.code,
            )
            return (
                self._outcome_from_attempt(failed, failed=True)
                if state is not RemoteActionState.UNKNOWN
                else None,
                pause,
            )
        except Exception:
            self.action_journal.transition_remote_action(
                attempt.id,
                expected_state=RemoteActionState.DISPATCHING,
                new_state=RemoteActionState.UNKNOWN,
                updated_at=self._now(),
                error_code="unclassified_dispatch_interruption",
            )
            return None, "reconciliation_required"
        succeeded = self.action_journal.transition_remote_action(
            attempt.id,
            expected_state=RemoteActionState.DISPATCHING,
            new_state=RemoteActionState.SUCCEEDED,
            updated_at=self._now(),
            platform_reference=outcome.platform_reference,
            before_state=outcome.before_state,
            after_state=outcome.after_state,
            error_code=outcome.error_code,
        )
        return self._outcome_from_attempt(succeeded), None

    @staticmethod
    def _prepared_payload(prepared: PreparedRemoteAction) -> dict[str, Any]:
        return {
            "platform": prepared.platform,
            "connection_id": prepared.connection_id,
            "action": to_primitive(prepared.action),
            "before_state": dict(prepared.before_state),
            "desired_state": dict(prepared.desired_state),
            "prepared_at": prepared.prepared_at.isoformat(),
        }

    @staticmethod
    def _prepared_from_attempt(attempt: RemoteActionAttempt) -> PreparedRemoteAction:
        value = attempt.action_payload
        return PreparedRemoteAction(
            platform=str(value["platform"]),
            connection_id=str(value["connection_id"]),
            action=action_from_dict(value["action"]),
            before_state=dict(value["before_state"]),
            desired_state=dict(value["desired_state"]),
            prepared_at=parse_datetime(value["prepared_at"]),
        )

    @staticmethod
    def _validate_fresh_prepared_action(
        prepared: PreparedRemoteAction,
        *,
        platform: str,
        connection_id: str,
        action: Any,
    ) -> None:
        if (
            prepared.platform != platform
            or prepared.connection_id != connection_id
            or to_primitive(prepared.action) != to_primitive(action)
        ):
            raise InvalidStateError("live adapter prepared an action outside its immutable binding")

    @classmethod
    def _prepared_from_bound_attempt(
        cls,
        attempt: RemoteActionAttempt,
        *,
        attempt_id: str,
        owner_id: str,
        connection_id: str,
        platform: str,
        migration_id: str,
        action: Any,
    ) -> PreparedRemoteAction:
        try:
            prepared = cls._prepared_from_attempt(attempt)
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidStateError("durable remote action payload is invalid") from exc
        immutable = (
            attempt.id == attempt_id
            and attempt.owner_id == owner_id
            and attempt.connection_id == connection_id
            and attempt.platform == platform
            and attempt.migration_id == migration_id
            and attempt.action_id == action.id
            and attempt.operation == action.action_type.value
            and attempt.idempotency_key == action.idempotency_key
            and prepared.idempotency_fingerprint == attempt.request_fingerprint
            and prepared.platform == platform
            and prepared.connection_id == connection_id
            and to_primitive(prepared.action) == to_primitive(action)
        )
        if not immutable:
            raise InvalidStateError("durable remote action attempt failed immutable binding validation")
        return prepared

    @staticmethod
    def _outcome_from_attempt(
        attempt: RemoteActionAttempt,
        *,
        failed: bool = False,
    ) -> ActionOutcome:
        action = action_from_dict(attempt.action_payload["action"])
        before = dict(attempt.before_state or attempt.action_payload.get("before_state", {}))
        after = dict(attempt.after_state or before)
        if failed:
            status = ActionStatus.FAILED
        else:
            status = ActionStatus.SKIPPED if before == after else ActionStatus.EXECUTED
        return ActionOutcome(
            action=action,
            status=status,
            before_state=before,
            after_state=after,
            executed_at=attempt.updated_at or attempt.created_at or datetime.now(timezone.utc),
            platform_reference=attempt.platform_reference,
            error_code=attempt.error_code,
        )

    @staticmethod
    def _remote_attempt_id(migration_id: str, action_id: str, idempotency_key: str) -> str:
        digest = sha256(f"{migration_id}\0{action_id}\0{idempotency_key}".encode("utf-8")).hexdigest()
        return f"remote-{digest[:48]}"

    @staticmethod
    def _migration_scoped_action(migration_id: str, action: Any) -> Any:
        """Bind provider and journal idempotency to one approved migration.

        Compilers intentionally produce stable action plans. A later migration
        may therefore contain the same plan action after the first migration was
        rolled back. Scoping both identifiers keeps retries of one migration
        stable while ensuring a distinct approval can reserve and dispatch a new
        provider request.
        """

        digest = sha256(
            f"{migration_id}\0{action.id}\0{action.idempotency_key}".encode("utf-8")
        ).hexdigest()
        return replace(
            action,
            id=f"{action.id}-{digest[:16]}",
            idempotency_key=f"migration-{digest[:48]}",
        )

    def rollback_receipt(self, receipt_id: str, *, actor_id: str, platform: str) -> dict[str, Any]:
        receipt_value = self._projection("receipts", receipt_id)
        receipt = receipt_from_dict(receipt_value)
        passport = self.get_passport(receipt.passport_id)
        if passport.owner_id != actor_id:
            raise PermissionError("only the Passport owner can roll back its receipt")
        receipt_platform = receipt_value.get("platform")
        if not isinstance(receipt_platform, str) or not receipt_platform:
            raise InvalidStateError("receipt is missing its bound platform")
        if platform != receipt_platform:
            raise ValueError("rollback platform does not match the receipt's bound platform")
        if receipt_value.get("status") == "rolled_back":
            return receipt_value
        adapter = self._adapter(receipt_platform)
        live_attempts: tuple[RemoteActionAttempt, ...] = ()
        if self._is_live_adapter(adapter):
            migration_id = receipt_value.get("migration_id")
            if not isinstance(migration_id, str) or not migration_id:
                raise InvalidStateError("live receipt is missing its durable migration journal binding")
            candidates = self.action_journal.list_remote_actions(
                owner_id=actor_id,
                migration_id=migration_id,
                connection_id=receipt.destination_id,
            )
            now = self._now()
            if any(
                attempt.state is RemoteActionState.ROLLBACK_PENDING
                and (attempt.lease_expires_at is None or attempt.lease_expires_at > now)
                for attempt in candidates
            ):
                raise InvalidStateError("rollback is already in progress")
            pending: list[RemoteActionAttempt] = []
            for attempt in candidates:
                if attempt.state is RemoteActionState.ROLLBACK_PENDING:
                    attempt = self.action_journal.transition_remote_action(
                        attempt.id,
                        expected_state=RemoteActionState.ROLLBACK_PENDING,
                        new_state=RemoteActionState.ROLLBACK_UNKNOWN,
                        updated_at=now,
                        error_code="rollback_lease_expired",
                        next_attempt_at=now,
                    )
                if attempt.state is RemoteActionState.SUCCEEDED:
                    pending.append(
                        self.action_journal.transition_remote_action(
                            attempt.id,
                            expected_state=RemoteActionState.SUCCEEDED,
                            new_state=RemoteActionState.ROLLBACK_PENDING,
                            updated_at=now,
                            lease_owner=f"rollback:{receipt_id}",
                            lease_expires_at=now + timedelta(minutes=2),
                        )
                    )
                elif attempt.state is RemoteActionState.ROLLBACK_UNKNOWN:
                    if attempt.next_attempt_at is not None and attempt.next_attempt_at > now:
                        continue
                    if attempt.attempt_count >= _REMOTE_RECOVERY_MAX_ATTEMPTS:
                        self.action_journal.transition_remote_action(
                            attempt.id,
                            expected_state=RemoteActionState.ROLLBACK_UNKNOWN,
                            new_state=RemoteActionState.NEEDS_HUMAN,
                            updated_at=now,
                            error_code="rollback_attempt_limit",
                        )
                        continue
                    pending.append(
                        self.action_journal.transition_remote_action(
                            attempt.id,
                            expected_state=RemoteActionState.ROLLBACK_UNKNOWN,
                            new_state=RemoteActionState.ROLLBACK_PENDING,
                            updated_at=now,
                            lease_owner=f"rollback:{receipt_id}",
                            lease_expires_at=now + timedelta(minutes=2),
                        )
                    )
            live_attempts = tuple(pending)
            if not live_attempts and not all(
                attempt.state is RemoteActionState.ROLLED_BACK for attempt in candidates
            ):
                raise InvalidStateError("live rollback has no eligible durable action attempts")
        rollback_input = receipt
        if live_attempts:
            eligible_action_ids = {attempt.action_id for attempt in live_attempts}
            rollback_input = replace(
                receipt,
                outcomes=tuple(
                    value
                    for value in receipt.outcomes
                    if value.action.id in eligible_action_ids
                ),
            )
            if len(rollback_input.outcomes) != len(live_attempts):
                raise InvalidStateError(
                    "live rollback receipt does not exactly match its eligible durable attempts"
                )
        try:
            if self._is_live_adapter(adapter) and not live_attempts:
                outcome = RollbackOutcome(
                    receipt_id=receipt.id,
                    destination_id=receipt.destination_id,
                    restored_actions=tuple(value.action.id for value in receipt.outcomes),
                    failed_actions=(),
                    completed_at=self._now(),
                    caveats=("Durable action journal already proves rollback completion.",),
                )
            else:
                outcome = adapter.rollback(
                    receipt.destination_id,
                    rollback_input,
                    now=self._now(),
                )
        except Exception:
            for attempt in live_attempts:
                current = self.action_journal.get_remote_action(attempt.id, owner_id=actor_id)
                if current.state is RemoteActionState.ROLLBACK_PENDING:
                    self._defer_or_stop_rollback_recovery(
                        current,
                        error_code="rollback_interrupted",
                    )
            if not live_attempts:
                raise
            rollback_exhausted = any(
                self.action_journal.get_remote_action(value.id, owner_id=actor_id).state
                is RemoteActionState.NEEDS_HUMAN
                for value in live_attempts
            )
            recovery_status = (
                "rollback_needs_human"
                if rollback_exhausted
                else "rollback_reconciliation_required"
            )
            projection = {
                **receipt_value,
                "status": recovery_status,
                "rollback": {
                    "receipt_id": receipt_id,
                    "destination_id": receipt.destination_id,
                    "restored_actions": [],
                    "failed_actions": [attempt.action_id for attempt in live_attempts],
                    "completed_at": self._now().isoformat(),
                    "caveats": [
                        "Automatic rollback recovery stopped for human review."
                        if rollback_exhausted
                        else "Remote rollback outcome is unknown; reconcile before retrying."
                    ],
                },
            }
            self._record(
                kind="receipts",
                aggregate_id=receipt_id,
                aggregate_type="action_receipt",
                event_type=f"receipt.{recovery_status}",
                projection=projection,
                payload={"unknown": len(live_attempts)},
                actor_id=actor_id,
                trace_id=receipt.trace_id,
            )
            return projection
        if live_attempts:
            restored_ids = set(outcome.restored_actions)
            unknown_codes = {"transport_interrupted", "platform_unavailable"}
            unknown_ids = {
                caveat.split(":", 1)[0]
                for caveat in outcome.caveats
                if any(code in caveat for code in unknown_codes)
            }
            for attempt in live_attempts:
                current = self.action_journal.get_remote_action(attempt.id, owner_id=actor_id)
                if current.state is not RemoteActionState.ROLLBACK_PENDING:
                    continue
                if attempt.action_id in restored_ids:
                    target = RemoteActionState.ROLLED_BACK
                    error_code = None
                elif attempt.action_id in unknown_ids:
                    self._defer_or_stop_rollback_recovery(
                        current,
                        error_code="rollback_outcome_unknown",
                    )
                    continue
                else:
                    target = RemoteActionState.NEEDS_HUMAN
                    error_code = "rollback_verification_failed"
                self.action_journal.transition_remote_action(
                    current.id,
                    expected_state=RemoteActionState.ROLLBACK_PENDING,
                    new_state=target,
                    updated_at=self._now(),
                    error_code=error_code,
                )
        if outcome.failed_actions:
            rollback_status = (
                "rollback_needs_human"
                if live_attempts
                and any(
                    self.action_journal.get_remote_action(value.id, owner_id=actor_id).state
                    is RemoteActionState.NEEDS_HUMAN
                    for value in live_attempts
                )
                else "rollback_reconciliation_required"
                if live_attempts
                and any(
                    self.action_journal.get_remote_action(value.id, owner_id=actor_id).state
                    is RemoteActionState.ROLLBACK_UNKNOWN
                    for value in live_attempts
                )
                else "rollback_partial"
                if outcome.restored_actions
                else "rollback_failed"
            )
        else:
            rollback_status = "rolled_back"
        projection = {
            **receipt_value,
            "rollback": to_primitive(outcome),
            "status": rollback_status,
        }
        self._record(
            kind="receipts",
            aggregate_id=receipt_id,
            aggregate_type="action_receipt",
            event_type=f"receipt.{rollback_status}",
            projection=projection,
            payload={"restored": len(outcome.restored_actions), "failed": len(outcome.failed_actions)},
            actor_id=actor_id,
            trace_id=receipt.trace_id,
        )
        self._persist_adapter_state(receipt_platform, receipt.destination_id, actor_id, receipt.trace_id)
        return projection

    def drift_watch(
        self,
        *,
        passport_id: str,
        platform: str,
        account_id: str,
        actor_id: str = "drift-watch",
    ) -> dict[str, Any]:
        passport = self.effective_passport(passport_id)
        if actor_id not in {passport.owner_id, "scheduler"}:
            raise PermissionError("only the Passport owner can inspect its feed drift")
        adapter = self._adapter(platform)
        now = self._now()
        observation = adapter.observe(account_id, now=now, sample_size=24)
        evaluation = evaluate_feed(passport, observation.sample)
        plan = adapter.compile(passport, observation, now=now) if evaluation.stop_reason is StopReason.CONTINUE else None
        alert_id = self._id("drift")
        projection = {
            "id": alert_id,
            "passport_id": passport_id,
            "platform": platform,
            "account_id": account_id,
            "status": "decision_required" if plan and plan.actions else "aligned",
            "confidence": observation.confidence,
            "evaluation": to_primitive(evaluation),
            "proposed_plan": to_primitive(plan) if plan is not None else None,
            "observed_at": now.isoformat(),
        }
        self._record(
            kind="drift_alerts",
            aggregate_id=alert_id,
            aggregate_type="drift_alert",
            event_type="drift.checked",
            projection=projection,
            payload={"stop_reason": evaluation.stop_reason.value},
            actor_id=actor_id,
        )
        return projection

    def create_drift_monitor(
        self,
        *,
        passport_id: str,
        platform: str,
        account_id: str,
        actor_id: str,
        interval_minutes: int,
        expires_at: datetime,
        mode: str,
        allowed_actions: frozenset[ActionType],
        max_actions_per_run: int,
        minimum_confidence: float = 0.8,
    ) -> dict[str, Any]:
        passport = self.get_passport(passport_id)
        if passport.owner_id != actor_id:
            raise PermissionError("only the Passport owner can create a drift monitor")
        if interval_minutes < 15 or interval_minutes > 1440:
            raise ValueError("drift interval must be between 15 minutes and one day")
        if expires_at <= self._now() or expires_at > self._now() + timedelta(days=30):
            raise ValueError("drift monitor expiry must be within the next 30 days")
        if mode not in {"alert_only", "bounded_auto"}:
            raise ValueError("drift mode must be alert_only or bounded_auto")
        if max_actions_per_run < 1 or max_actions_per_run > 20:
            raise ValueError("drift correction budget must be between one and twenty actions")
        if not 0.5 <= minimum_confidence <= 1.0:
            raise ValueError("minimum confidence must be between 0.5 and 1")
        public_actions = {
            ActionType.LIKE,
            ActionType.COMMENT,
            ActionType.POST,
            ActionType.REPOST,
            ActionType.SEND_MESSAGE,
        }
        if allowed_actions & public_actions:
            raise ValueError("drift monitors cannot authorize public engagement")
        adapter = self._adapter(platform)
        capability = adapter.capabilities(account_id)
        if mode == "bounded_auto":
            certified_closed_loop = (
                capability.level.value == "closed_loop" and capability.certified_at is not None
            )
            if capability.level.value != "lab" and not certified_closed_loop:
                raise ValueError("bounded automatic correction requires Lab or live closed-loop certification")
            if not allowed_actions or not allowed_actions <= capability.execute:
                raise ValueError("every automatic action must be executable by the certified adapter")
        monitor_id = self._id("monitor")
        next_run = self._now() + timedelta(minutes=interval_minutes)
        projection = {
            "id": monitor_id,
            "passport_id": passport_id,
            "platform": platform,
            "account_id": account_id,
            "owner_id": actor_id,
            "mode": mode,
            "interval_minutes": interval_minutes,
            "expires_at": expires_at.isoformat(),
            "allowed_actions": sorted(item.value for item in allowed_actions),
            "max_actions_per_run": max_actions_per_run,
            "minimum_confidence": minimum_confidence,
            "status": "active",
            "created_at": self._now().isoformat(),
            "next_run_at": next_run.isoformat(),
            "last_checked_at": None,
            "last_alert_id": None,
            "last_receipt_id": None,
            "run_count": 0,
        }
        self._record(
            kind="drift_monitors",
            aggregate_id=monitor_id,
            aggregate_type="drift_monitor",
            event_type="drift_monitor.created",
            projection=projection,
            payload={
                "mode": mode,
                "allowed_actions": projection["allowed_actions"],
                "max_actions_per_run": max_actions_per_run,
                "expires_at": projection["expires_at"],
            },
            actor_id=actor_id,
        )
        self.store.schedule_once(
            f"drift-{monitor_id}-{next_run.isoformat()}",
            "drift.monitor",
            next_run,
            {"monitor_id": monitor_id},
        )
        return projection

    def stop_drift_monitor(self, monitor_id: str, *, actor_id: str) -> dict[str, Any]:
        monitor = self._projection("drift_monitors", monitor_id)
        if monitor["owner_id"] != actor_id:
            raise PermissionError("only the monitor owner can stop it")
        if monitor.get("status") == "stopped":
            return monitor
        monitor["status"] = "stopped"
        monitor["stopped_at"] = self._now().isoformat()
        monitor["next_run_at"] = None
        self._record(
            kind="drift_monitors",
            aggregate_id=monitor_id,
            aggregate_type="drift_monitor",
            event_type="drift_monitor.stopped",
            projection=monitor,
            payload={"stopped_at": monitor["stopped_at"]},
            actor_id=actor_id,
        )
        return monitor

    def creator_continuity(self, creator_id: str, destination_platform: str) -> dict[str, Any]:
        matches = [
            item
            for item in CREATOR_DIRECTORY
            if creator_id == item["canonical_id"] or creator_id in item["verified_links"].values()
        ]
        if not matches:
            return {
                "source": creator_id,
                "destination_platform": destination_platform,
                "status": "unresolved",
                "confidence": 0.0,
                "match": None,
                "requires_human_confirmation": True,
            }
        match = matches[0]
        destination = match["verified_links"].get(destination_platform)
        return {
            "source": creator_id,
            "canonical_id": match["canonical_id"],
            "display_name": match["display_name"],
            "destination_platform": destination_platform,
            "status": "verified" if destination else "not_found",
            "confidence": 1.0 if destination else 0.0,
            "match": destination,
            "requires_human_confirmation": False,
        }

    def preserve_creator_match(
        self,
        *,
        passport_id: str,
        creator_id: str,
        destination_platform: str,
        actor_id: str,
    ) -> dict[str, Any]:
        passport = self.get_passport(passport_id)
        if passport.owner_id != actor_id:
            raise PermissionError("only the Passport owner can preserve creator continuity")
        match = self.creator_continuity(creator_id, destination_platform)
        if match["status"] != "verified" or not match.get("match"):
            raise InvalidStateError("creator identity must be verified before it can be preserved")
        for _, _, existing in self.store.list_projections("creator_links"):
            if (
                existing.get("passport_id") == passport_id
                and existing.get("canonical_id") == match["canonical_id"]
                and existing.get("destination_platform") == destination_platform
                and existing.get("status") == "preserved"
            ):
                return existing
        link_id = self._id("creator-link")
        projection = {
            "id": link_id,
            "passport_id": passport_id,
            "passport_version": passport.version,
            "owner_id": actor_id,
            "canonical_id": match["canonical_id"],
            "display_name": match["display_name"],
            "destination_platform": destination_platform,
            "destination_identity": match["match"],
            "confidence": match["confidence"],
            "status": "preserved",
            "preserved_at": self._now().isoformat(),
        }
        self._record(
            kind="creator_links",
            aggregate_id=link_id,
            aggregate_type="creator_continuity",
            event_type="creator.preserved",
            projection=projection,
            payload={
                "passport_id": passport_id,
                "canonical_id": match["canonical_id"],
                "destination_platform": destination_platform,
            },
            actor_id=actor_id,
        )
        return projection

    def projection_list(self, kind: str) -> tuple[dict[str, Any], ...]:
        return tuple(value for _, _, value in self.store.list_projections(kind))

    def projection_get(self, kind: str, identifier: str) -> dict[str, Any]:
        return self._projection(kind, identifier)

    @staticmethod
    def _passport_fingerprint(passport: FeedPassport) -> str:
        fingerprint_value = to_primitive(passport)
        fingerprint_value.pop("updated_at", None)
        payload = json.dumps(
            fingerprint_value,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _materialize_share_slice(
        passport: FeedPassport,
        *,
        selected_fields: Mapping[str, Any],
        expires_at: datetime,
        consent_id: str,
    ) -> ShareablePassportSlice:
        topic_names = tuple(str(value) for value in selected_fields.get("topic_names", ()))
        creator_ids = tuple(str(value) for value in selected_fields.get("creator_ids", ()))
        return ShareablePassportSlice(
            owner_id=passport.owner_id,
            passport_id=passport.id,
            passport_version=passport.version,
            topic_targets={
                key: passport.topic_targets[key]
                for key in topic_names
                if key in passport.topic_targets
            },
            creator_preferences={
                key: passport.creator_preferences[key]
                for key in creator_ids
                if key in passport.creator_preferences
            },
            serendipity=(
                passport.serendipity
                if bool(selected_fields.get("include_serendipity"))
                else None
            ),
            expires_at=expires_at,
            consent_id=consent_id,
            format_preferences=(
                passport.format_preferences
                if bool(selected_fields.get("include_formats"))
                else {}
            ),
            hard_exclusions=(
                passport.hard_exclusions
                if bool(selected_fields.get("include_exclusions"))
                else frozenset()
            ),
        )

    @staticmethod
    def _combined_selected_fields(values: list[dict[str, Any]]) -> dict[str, Any]:
        metadata = [dict(value.get("selected_fields", {})) for value in values]
        return {
            "topic_names": sorted(
                {
                    str(name)
                    for selected in metadata
                    for name in selected.get("topic_names", ())
                }
            ),
            "creator_ids": sorted(
                {
                    str(identifier)
                    for selected in metadata
                    for identifier in selected.get("creator_ids", ())
                }
            ),
            "include_serendipity": any(
                bool(selected.get("include_serendipity")) for selected in metadata
            ),
            "include_formats": any(bool(selected.get("include_formats")) for selected in metadata),
            "include_exclusions": any(
                bool(selected.get("include_exclusions")) for selected in metadata
            ),
        }

    def _validate_continuous_targets(self, values: list[dict[str, Any]]) -> None:
        if len(values) != 2:
            raise InvalidStateError("continuous companion sync requires exactly two consent slices")
        if len({str(value.get("consent_id", "")) for value in values}) != 2:
            raise InvalidStateError("continuous companion sync requires two independent consents")
        if len({str(value.get("owner_id", "")) for value in values}) != 2:
            raise InvalidStateError("continuous companion sync requires two distinct owners")
        owners = {str(value.get("owner_id", "")) for value in values}
        pair_ids = {str(value.get("pair_id", "")) for value in values}
        if len(pair_ids) != 1 or "" in pair_ids:
            raise InvalidStateError("continuous companion consents must bind the same pair")
        for value in values:
            owner_id = str(value.get("owner_id", ""))
            expected_counterparties = owners - {owner_id}
            if len(expected_counterparties) != 1 or value.get("counterparty_owner_id") not in expected_counterparties:
                raise InvalidStateError("continuous companion consent counterparty is not reciprocal")
            if set(str(item) for item in value.get("participant_owner_ids", ())) != owners:
                raise InvalidStateError("continuous companion consent participant binding is invalid")
        if len({str(value.get("passport_id", "")) for value in values}) != 2:
            raise InvalidStateError("continuous companion sync requires two distinct source Passports")
        for value in values:
            if value.get("scope") != "continuous" or not value.get("refresh_on_revision"):
                raise InvalidStateError("continuous companion consent scope is missing")
            source = self.get_passport(str(value["passport_id"]))
            if source.owner_id != value.get("owner_id"):
                raise PermissionError("continuous consent owner no longer matches its source Passport")
            targets = tuple(dict.fromkeys(str(item) for item in value.get("target_passport_ids", ())))
            if not targets:
                raise InvalidStateError("continuous consent has no opted-in target Passport")
            for target_id in targets:
                target = self.get_passport(target_id)
                if target.owner_id != value.get("owner_id"):
                    raise PermissionError(
                        "continuous consent targets a Passport owned by another participant"
                    )

    def _refresh_continuous_share(
        self,
        value: dict[str, Any],
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        now = self._now()
        if value.get("scope") != "continuous" or not value.get("refresh_on_revision"):
            raise InvalidStateError("share does not grant continuous revision refresh")
        if value.get("status") != "active" or parse_datetime(str(value["expires_at"])) <= now:
            raise InvalidStateError("continuous share consent is not active")
        passport = self.get_passport(str(value["passport_id"]))
        if passport.owner_id != value.get("owner_id"):
            raise PermissionError("continuous share owner no longer matches its source Passport")
        selected_fields = dict(value.get("selected_fields", {}))
        if not selected_fields:
            raise InvalidStateError("continuous share is missing selected-field metadata")
        refreshed_slice = self._materialize_share_slice(
            passport,
            selected_fields=selected_fields,
            expires_at=parse_datetime(str(value["expires_at"])),
            consent_id=str(value["consent_id"]),
        )
        if int(value["passport_version"]) == passport.version:
            return value
        refreshed = {
            **value,
            **to_primitive(refreshed_slice),
            "last_refreshed_at": now.isoformat(),
        }
        self._record(
            kind="shares",
            aggregate_id=str(value["id"]),
            aggregate_type="shareable_passport_slice",
            event_type="share.refreshed",
            projection=refreshed,
            payload={
                "passport_id": passport.id,
                "passport_version": passport.version,
                "selected_fields": selected_fields,
            },
            actor_id=actor_id,
        )
        return refreshed

    def _load_continuous_consents(
        self,
        companion: Mapping[str, Any],
        *,
        at: datetime,
        refresh: bool,
    ) -> list[dict[str, Any]]:
        slice_ids = tuple(str(value) for value in companion.get("slice_ids", ()))
        if len(slice_ids) != 2:
            raise InvalidStateError("continuous companion is missing one of its two consents")
        values = [self._projection("shares", slice_id) for slice_id in slice_ids]
        if any(
            value.get("status") != "active" or parse_datetime(str(value["expires_at"])) <= at
            for value in values
        ):
            raise InvalidStateError("continuous companion consent is missing, expired, or revoked")
        self._validate_continuous_targets(values)
        expected_consents = {str(value) for value in companion.get("consent_ids", ())}
        actual_consents = {str(value["consent_id"]) for value in values}
        if expected_consents and actual_consents != expected_consents:
            raise InvalidStateError("continuous companion consent receipts do not match")
        expected_sources = {str(value) for value in companion.get("source_passport_ids", ())}
        actual_sources = {str(value["passport_id"]) for value in values}
        if expected_sources and actual_sources != expected_sources:
            raise InvalidStateError("continuous companion source Passports do not match")
        if refresh:
            values = [self._refresh_continuous_share(value, actor_id="companion-sync") for value in values]
        return values

    def _disable_continuous_companion(
        self,
        value: dict[str, Any],
        *,
        status: str,
        reason: str,
    ) -> None:
        if value.get("status") != "active":
            return
        disabled = {
            **value,
            "status": status,
            "invalidated_at": self._now().isoformat(),
            "invalidation_reason": reason,
        }
        self._record(
            kind="companions",
            aggregate_id=str(value["id"]),
            aggregate_type="companion_blend",
            event_type=f"companion.{status}",
            projection=disabled,
            payload={"reason": reason},
            actor_id="companion-sync",
        )

    def _recompute_continuous_companion(self, companion_id: str) -> dict[str, Any] | None:
        value = self._projection("companions", companion_id)
        now = self._now()
        if value.get("scope") != "continuous" or value.get("status") != "active":
            return None
        if parse_datetime(str(value["expires_at"])) <= now:
            return None
        try:
            values = self._load_continuous_consents(value, at=now, refresh=True)
            current_versions = {
                str(item["passport_id"]): int(item["passport_version"])
                for item in values
            }
            if current_versions == {
                str(key): int(version)
                for key, version in dict(value.get("source_passport_versions", {})).items()
            }:
                return value
            slices = tuple(slice_from_dict(item) for item in values)
            blend = blend_slices(
                blend_id=str(value["id"]),
                name=str(value["name"]),
                slices=slices,
                weights={
                    str(key): float(weight)
                    for key, weight in dict(value.get("requested_weights", {})).items()
                },
                strategy=str(value["strategy"]),
                created_at=now,
                expires_at=parse_datetime(str(value["expires_at"])),
            )
        except (InvalidStateError, NotFoundError, PermissionError, KeyError) as exc:
            self._disable_continuous_companion(
                value,
                status="consent_invalidated",
                reason=type(exc).__name__,
            )
            return None
        except ValueError as exc:
            self._disable_continuous_companion(
                value,
                status="sync_failed",
                reason=type(exc).__name__,
            )
            return None

        blend_value = to_primitive(blend)
        synced = {
            **value,
            "participant_ids": blend_value["participant_ids"],
            "topic_targets": blend_value["topic_targets"],
            "creator_preferences": blend_value["creator_preferences"],
            "serendipity": blend_value["serendipity"],
            "consent_ids": blend_value["consent_ids"],
            "format_preferences": blend_value["format_preferences"],
            "hard_exclusions": blend_value["hard_exclusions"],
            "source_passport_versions": current_versions,
            "participant_selected_fields": {
                str(item["passport_id"]): dict(item["selected_fields"])
                for item in values
            },
            "selected_fields": self._combined_selected_fields(values),
            "sync_revision": int(value.get("sync_revision", 0)) + 1,
            "last_synced_at": now.isoformat(),
        }
        self._record(
            kind="companions",
            aggregate_id=str(value["id"]),
            aggregate_type="companion_blend",
            event_type="companion.synced",
            projection=synced,
            payload={
                "sync_revision": synced["sync_revision"],
                "source_passport_versions": current_versions,
            },
            actor_id="companion-sync",
        )
        return synced

    def _refresh_continuous_companions_for_passport(self, passport_id: str) -> None:
        for companion_id, _, value in self.store.list_projections("companions"):
            if (
                value.get("scope") == "continuous"
                and value.get("status") == "active"
                and passport_id in value.get("source_passport_ids", ())
            ):
                self._recompute_continuous_companion(companion_id)

    def _continuous_companion_for_effective(
        self,
        value: dict[str, Any],
        *,
        at: datetime,
    ) -> dict[str, Any] | None:
        if value.get("status") != "active" or parse_datetime(str(value["expires_at"])) <= at:
            return None
        try:
            consents = self._load_continuous_consents(value, at=at, refresh=False)
        except (InvalidStateError, NotFoundError, PermissionError, KeyError) as exc:
            self._disable_continuous_companion(
                value,
                status="consent_invalidated",
                reason=type(exc).__name__,
            )
            return None
        current_versions = {
            str(item["passport_id"]): self.get_passport(str(item["passport_id"])).version
            for item in consents
        }
        stored_versions = {
            str(key): int(version)
            for key, version in dict(value.get("source_passport_versions", {})).items()
        }
        if current_versions != stored_versions:
            return self._recompute_continuous_companion(str(value["id"]))
        return value

    @staticmethod
    def _apply_continuous_companion(base: FeedPassport, companion: Mapping[str, Any]) -> FeedPassport:
        selected = dict(companion.get("selected_fields", {}))
        topics = dict(base.topic_targets)
        blend_topics = dict(companion.get("topic_targets", {}))
        for name in selected.get("topic_names", ()):
            if name in blend_topics:
                topics[str(name)] = float(blend_topics[name])
        creators = dict(base.creator_preferences)
        blend_creators = dict(companion.get("creator_preferences", {}))
        for identifier in selected.get("creator_ids", ()):
            if identifier in blend_creators:
                creators[str(identifier)] = float(blend_creators[identifier])
        formats = dict(base.format_preferences)
        if selected.get("include_formats"):
            formats.update(
                {
                    str(name): float(weight)
                    for name, weight in dict(companion.get("format_preferences", {})).items()
                }
            )
        exclusions = set(base.hard_exclusions)
        if selected.get("include_exclusions"):
            exclusions.update(str(value) for value in companion.get("hard_exclusions", ()))
        synced_at = parse_datetime(str(companion["last_synced_at"]))
        return replace(
            base,
            version=base.version + max(1, int(companion.get("sync_revision", 1))),
            updated_at=max(base.updated_at, synced_at),
            topic_targets=topics,
            creator_preferences=creators,
            format_preferences=formats,
            hard_exclusions=frozenset(exclusions),
            serendipity=(
                float(companion["serendipity"])
                if selected.get("include_serendipity")
                else base.serendipity
            ),
        )

    def _invalidate_companions_for_slice(self, slice_id: str, *, reason: str, actor_id: str) -> None:
        for companion_id, _, value in self.store.list_projections("companions"):
            if value.get("status") != "active" or slice_id not in value.get("slice_ids", ()):
                continue
            value["status"] = "consent_invalidated"
            value["invalidated_at"] = self._now().isoformat()
            value["invalidation_reason"] = reason
            self._record(
                kind="companions",
                aggregate_id=companion_id,
                aggregate_type="companion_blend",
                event_type="companion.consent_invalidated",
                projection=value,
                payload={"slice_id": slice_id, "reason": reason},
                actor_id=actor_id,
            )

    def _rollback_overlay_activations(
        self,
        value: dict[str, Any],
        *,
        actor_id: str,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for activation in value.get("activations", ()):
            if activation.get("status") == "rolled_back":
                continue
            receipt_id = activation.get("receipt_id")
            platform = activation.get("platform")
            if not receipt_id or not platform:
                continue
            try:
                rolled_back = self.rollback_receipt(
                    str(receipt_id),
                    actor_id=actor_id,
                    platform=str(platform),
                )
                activation["status"] = rolled_back["status"]
                if rolled_back["status"] == "rolled_back":
                    activation["rolled_back_at"] = self._now().isoformat()
                else:
                    activation["error"] = "RollbackIncomplete"
                results.append({"receipt_id": receipt_id, "status": rolled_back["status"]})
            except (KeyError, ValueError, InvalidStateError, PermissionError) as exc:
                activation["status"] = "rollback_failed"
                activation["error"] = type(exc).__name__
                results.append(
                    {
                        "receipt_id": receipt_id,
                        "status": "rollback_failed",
                        "error": type(exc).__name__,
                    }
                )
        return results

    def _persist_adapter_state(self, platform: str, account_id: str, actor_id: str, trace_id: str) -> None:
        adapter = self._adapter(platform)
        if not hasattr(adapter, "export_state"):
            return
        projection = adapter.export_state(account_id)
        aggregate_id = f"{platform}:{account_id}"
        stored = {
            "platform": platform,
            "account_id": account_id,
            "state": projection,
        }
        existing = self.store.get_projection("adapter_accounts", aggregate_id)
        if existing is not None and existing[1] == to_primitive(stored):
            return
        self._record(
            kind="adapter_accounts",
            aggregate_id=aggregate_id,
            aggregate_type="adapter_account",
            event_type="adapter.state_changed",
            projection=stored,
            payload={"platform": platform, "account_id": account_id},
            actor_id=actor_id,
            trace_id=trace_id,
        )

    def _restore_adapter_state(self) -> None:
        for _, _, value in self.store.list_projections("adapter_accounts"):
            adapter = self.adapters.get(str(value["platform"]))
            if adapter is not None and hasattr(adapter, "import_state"):
                try:
                    adapter.import_state(dict(value["state"]))
                except KeyError:
                    continue

    def _known_account_ids(self, platform: str) -> tuple[str, ...]:
        adapter = self.adapters[platform]
        if hasattr(adapter, "_accounts"):
            return tuple(sorted(adapter._accounts))
        if hasattr(adapter, "_observations"):
            return tuple(sorted(adapter._observations))
        return tuple(
            value["account_id"]
            for _, _, value in self.store.list_projections("adapter_accounts")
            if value["platform"] == platform
        )

    @staticmethod
    def _public_demo_account_ids(adapter: PlatformAdapter) -> tuple[str, ...]:
        """Return only explicitly declared synthetic accounts safe for public listing."""

        try:
            from feed_passport.adapters.platforms import declared_demo_account_ids
        except ImportError:
            return ()
        return declared_demo_account_ids(adapter)

    @staticmethod
    def _observation_provenance(
        observation: Any,
    ) -> tuple[PreferenceEvidence, ...]:
        """Attach provenance only when an observation exactly matches a declared fixture."""

        try:
            from feed_passport.adapters.platforms import synthetic_demo_provenance
        except ImportError:
            return ()
        return synthetic_demo_provenance(observation)

    def _record(
        self,
        *,
        kind: str,
        aggregate_id: str,
        aggregate_type: str,
        event_type: str,
        projection: Any,
        payload: Any,
        actor_id: str,
        trace_id: str | None = None,
        expected_projection_version: int | None = None,
    ) -> int:
        if expected_projection_version is None:
            current = self.store.get_projection(kind, aggregate_id)
            expected_version = current[0] if current else 0
        else:
            expected_version = expected_projection_version
        return self.store.append_event(
            aggregate_id=aggregate_id,
            aggregate_type=aggregate_type,
            expected_version=expected_version,
            event_type=event_type,
            payload=payload,
            actor_id=actor_id,
            trace_id=trace_id or self._id("trace"),
            occurred_at=self._now(),
            projection_kind=kind,
            projection=projection,
        )

    def _projection(self, kind: str, identifier: str) -> dict[str, Any]:
        value = self.store.get_projection(kind, identifier)
        if value is None:
            raise NotFoundError(f"{kind}:{identifier}")
        return dict(value[1])

    def _adapter(self, platform: str) -> PlatformAdapter:
        try:
            return self.adapters[platform]
        except KeyError as exc:
            raise NotFoundError(f"platform:{platform}") from exc

    @staticmethod
    def _is_live_adapter(adapter: Any) -> bool:
        return all(
            callable(getattr(adapter, name, None))
            for name in (
                "prepare_remote_action",
                "apply_prepared_action",
                "reconcile_remote_action",
            )
        )

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("application clock must be timezone-aware")
        return value

    def _id(self, prefix: str) -> str:
        return f"{prefix}-{self.id_factory()}"

    @staticmethod
    def _source_concentration(items: tuple[Any, ...]) -> float:
        if not items:
            return 1.0
        sources: list[str] = []
        for item in items:
            value = getattr(item, "source", None)
            source = str(value).strip() if value is not None else ""
            if source.casefold() in _UNKNOWN_CONTENT_SOURCE_MARKERS:
                # A stricter inferred cap would invent source diversity the capture
                # did not establish. Preserve uncertainty as the least restrictive
                # valid policy value until the user supplies source identities.
                return 1.0
            sources.append(source)
        counts = Counter(sources)
        return max(counts.values()) / len(items)
