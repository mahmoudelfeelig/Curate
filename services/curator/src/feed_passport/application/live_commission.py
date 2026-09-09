from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol

from feed_passport.domain import ActionType, CapabilityLevel
from feed_passport.domain.connections import ConnectionStatus
from feed_passport.infrastructure.serialization import to_primitive
from feed_passport.infrastructure.sqlite_store import ConcurrencyConflict
from feed_passport.ports.live_platform import (
    PUBLIC_ENGAGEMENT_ACTIONS,
    ValidatedLiveCertification,
)

from .curator import CuratorApplication, InvalidStateError, NotFoundError


LIVE_COMMISSION_PLATFORMS = frozenset({"youtube", "bluesky"})
LIVE_COMMISSION_PROJECTION_KIND = "agent_live_commissions"


class LivePriorityMode(StrEnum):
    BALANCED = "balanced"
    PROTECTIVE_CONTROLS_FIRST = "protective_controls_first"
    CREATOR_CONTINUITY_FIRST = "creator_continuity_first"


class LiveCommissionConsentBroker(Protocol):
    def consume(
        self,
        token: str,
        *,
        operation: str,
        resource_id: str,
        actor_id: str,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class LiveCommissionCandidate:
    """Server-bound candidate with a deliberately redacted model view."""

    commission_id: str
    platform: str
    priority_mode: LivePriorityMode
    demand_shape: Mapping[str, str]
    action_family_counts: Mapping[str, int]
    max_total_actions: int


class LiveCommissionService:
    """Prepare and execute one exact, separately approved live commission.

    The model is never an executor. It can select action families from a plan
    already compiled by deterministic platform code. Exact targets remain in
    the owner-private projection and are bound into the later consent scope.
    """

    projection_kind = LIVE_COMMISSION_PROJECTION_KIND

    def __init__(
        self,
        application: CuratorApplication,
        *,
        consent_broker: LiveCommissionConsentBroker | None = None,
    ) -> None:
        self.application = application
        self.consent_broker = consent_broker

    def prepare_candidate(
        self,
        *,
        actor_id: str,
        priority_mode: LivePriorityMode | str,
        passport_id: str,
        platform: str,
        destination_connection_id: str,
        max_total_actions: int,
        allowed_action_types: frozenset[ActionType] | None = None,
    ) -> LiveCommissionCandidate:
        if not actor_id.strip():
            raise ValueError("actor_id is required")
        try:
            normalized_priority = LivePriorityMode(priority_mode)
        except ValueError as error:
            raise ValueError("unsupported live commission priority mode") from error
        if platform not in LIVE_COMMISSION_PLATFORMS:
            raise ValueError("live agent commissions currently support only YouTube and Bluesky")
        if not 1 <= int(max_total_actions) <= 20:
            raise ValueError("live commission action budget must be between one and twenty")
        if allowed_action_types is not None and allowed_action_types & PUBLIC_ENGAGEMENT_ACTIONS:
            raise PermissionError("live commissions cannot authorize public engagement")

        connection, _, certification, manifest = self._current_live_context(
            actor_id=actor_id,
            platform=platform,
            connection_id=destination_connection_id,
        )
        base_passport = self.application.get_passport(passport_id)
        if base_passport.owner_id != actor_id:
            raise PermissionError("only the Passport owner can prepare a live commission")

        migration = self.application.prepare_migration(
            passport_id=passport_id,
            platform=platform,
            destination_account_id=destination_connection_id,
            actor_id=actor_id,
        )
        passport = self.application.effective_passport(passport_id)
        if (
            passport.version != int(migration["passport_version"])
            or self.application._passport_fingerprint(passport)
            != migration["effective_passport_fingerprint"]
        ):
            raise InvalidStateError(
                "effective Passport changed while the live candidate was being prepared"
            )
        plan_actions = list(migration["plan"].get("actions", ()))
        certified_types = frozenset(manifest.execute) & certification.execute
        if allowed_action_types is not None:
            certified_types &= allowed_action_types
        candidate_actions = [
            dict(item)
            for item in plan_actions
            if ActionType(str(item["action_type"])) in certified_types
            and ActionType(str(item["action_type"])) not in PUBLIC_ENGAGEMENT_ACTIONS
        ]
        action_counts = Counter(str(item["action_type"]) for item in candidate_actions)
        commission_id = self._id("live-commission")
        now = self._now()
        certification_binding = self._certification_binding(certification)
        compiled_plan_fingerprint = self._fingerprint(migration["plan"])
        projection = {
            "id": commission_id,
            "status": "planning",
            "owner_id": actor_id,
            "priority_mode": normalized_priority.value,
            "passport_id": passport_id,
            "passport_version": int(migration["passport_version"]),
            "effective_passport_fingerprint": migration["effective_passport_fingerprint"],
            "platform": platform,
            "destination_connection_id": destination_connection_id,
            "connection_version": connection.version,
            "migration_id": migration["id"],
            "candidate_plan_fingerprint": compiled_plan_fingerprint,
            "sealed_plan_fingerprint": None,
            "candidate_action_types": sorted(action_counts),
            "candidate_action_counts": dict(sorted(action_counts.items())),
            "max_total_actions": int(max_total_actions),
            "certification": certification_binding,
            "approval_scope": None,
            "planner_evidence": None,
            "stop_reason": None,
            "receipt_id": None,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

        # Mark the migration first. A crash can leave a non-executable orphan,
        # but can never leave a generic approval path to the broader plan.
        migration = dict(migration)
        migration.update(
            {
                "agent_live_commission_id": commission_id,
                "execution_authority": "agent_live_commission_only",
            }
        )
        self._record_migration_marker(migration, actor_id=actor_id)
        self._record(
            commission_id,
            projection,
            event_type="agent_live_commission.candidate_prepared",
            actor_id=actor_id,
            payload={
                "platform": platform,
                "candidate_action_count": len(candidate_actions),
                "candidate_action_types": sorted(action_counts),
            },
        )
        return LiveCommissionCandidate(
            commission_id=commission_id,
            platform=platform,
            priority_mode=normalized_priority,
            demand_shape=self._redacted_demand_shape(passport),
            action_family_counts=dict(sorted(action_counts.items())),
            max_total_actions=int(max_total_actions),
        )

    def finalize_candidate(
        self,
        commission_id: str,
        *,
        actor_id: str,
        prioritized_action_types: tuple[ActionType, ...],
        planner_evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        commission_version, commission = self._get_versioned(
            commission_id,
            actor_id=actor_id,
        )
        if commission["status"] == "sealing":
            # An abrupt worker exit can occur on either side of the separate
            # migration seal write. The durable claim already contains the
            # exact deterministic result, so retrying resumes that claim and
            # never accepts a replacement model ordering.
            return self._resume_candidate_finalization(
                commission_id,
                actor_id=actor_id,
            )
        if commission["status"] != "planning":
            raise InvalidStateError(
                f"live commission cannot finish planning from {commission['status']}"
            )
        available = frozenset(
            ActionType(item) for item in commission.get("candidate_action_types", ())
        )
        prioritized = tuple(ActionType(item) for item in prioritized_action_types)
        prioritized_set = frozenset(prioritized)
        if len(prioritized) != len(prioritized_set) or prioritized_set != available:
            names = ", ".join(item.value for item in prioritized)
            raise InvalidStateError(
                "the model priority must be an exact unique permutation of every "
                f"precompiled action family; received={names or '<empty>'}"
            )
        if prioritized_set & PUBLIC_ENGAGEMENT_ACTIONS:
            raise PermissionError("live commissions cannot authorize public engagement")

        stored_migration = self.application.store.get_projection(
            "migrations", str(commission["migration_id"])
        )
        if stored_migration is None:
            raise NotFoundError(f"migrations:{commission['migration_id']}")
        _, migration_value = stored_migration
        migration = dict(migration_value)
        self._require_migration_marker(migration, commission)
        if self._fingerprint(migration["plan"]) != commission["candidate_plan_fingerprint"]:
            raise InvalidStateError("the compiled migration changed while the model was planning")
        actions_by_type = {
            action_type: [
                dict(item)
                for item in migration["plan"].get("actions", ())
                if ActionType(str(item["action_type"])) is action_type
            ]
            for action_type in prioritized
        }
        selected_actions = [
            action
            for action_type in prioritized
            for action in actions_by_type[action_type]
        ][: int(commission["max_total_actions"])]
        admitted_types = list(
            dict.fromkeys(str(item["action_type"]) for item in selected_actions)
        )
        executable_plan = {
            **dict(migration["plan"]),
            "actions": selected_actions,
        }
        sealed_plan_fingerprint = self._fingerprint(executable_plan)
        approval_scope = {
            "schema": "feed-passport-agent-live-commission/v1",
            "commission_id": commission_id,
            "owner_id": actor_id,
            "passport_id": commission["passport_id"],
            "passport_version": commission["passport_version"],
            "effective_passport_fingerprint": commission[
                "effective_passport_fingerprint"
            ],
            "platform": commission["platform"],
            "destination_connection_id": commission["destination_connection_id"],
            "connection_version": commission["connection_version"],
            "migration_id": commission["migration_id"],
            "compiled_plan_fingerprint": sealed_plan_fingerprint,
            "executable_plan": executable_plan,
            "allowed_action_types": admitted_types,
            "max_total_actions": len(selected_actions),
            "priority_mode": commission["priority_mode"],
            "prioritized_action_types": [item.value for item in prioritized],
            "certification": dict(commission["certification"]),
        }
        now = self._now()
        if selected_actions:
            status = "awaiting_approval"
            stop_reason = None
        elif not migration["plan"].get("actions"):
            status = "needs_human"
            stop_reason = "no_compiled_actions"
        elif not available:
            status = "needs_human"
            stop_reason = "no_certified_live_actions"
        else:
            status = "needs_human"
            stop_reason = "no_actions_within_locked_budget"
        evidence = to_primitive(dict(planner_evidence))
        evidence.update(
            {
                "prioritized_action_types": [item.value for item in prioritized],
                "admitted_action_types": admitted_types,
                "deterministic_validation": "passed",
            }
        )
        final_projection = dict(commission)
        final_projection.update(
            {
                "status": status,
                "stop_reason": stop_reason,
                "selection_summary": self._selection_summary(
                    priority_mode=LivePriorityMode(str(commission["priority_mode"])),
                    prioritized=prioritized,
                    selected_action_count=len(selected_actions),
                ),
                "selected_action_count": len(selected_actions),
                "selected_action_types": admitted_types,
                "sealed_plan_fingerprint": sealed_plan_fingerprint,
                "approval_scope": approval_scope,
                "planner_evidence": evidence,
                "updated_at": now.isoformat(),
            }
        )
        self._claim_candidate_finalization(
            commission_id,
            final_projection,
            actor_id=actor_id,
            expected_projection_version=commission_version,
        )
        return self._resume_candidate_finalization(
            commission_id,
            actor_id=actor_id,
        )

    def planning_failed(self, commission_id: str, *, actor_id: str) -> None:
        commission_version, commission = self._get_versioned(
            commission_id,
            actor_id=actor_id,
        )
        if commission.get("status") != "planning":
            return
        commission.update(
            {
                "status": "planning_failed",
                "stop_reason": "model_protocol_failed",
                "updated_at": self._now().isoformat(),
            }
        )
        try:
            self._record(
                commission_id,
                commission,
                event_type="agent_live_commission.planning_failed",
                actor_id=actor_id,
                payload={"status": "planning_failed"},
                expected_projection_version=commission_version,
            )
        except ConcurrencyConflict:
            return

    def execute(
        self,
        commission_id: str,
        *,
        actor_id: str,
        approval_token: str,
    ) -> dict[str, Any]:
        commission = self.get(commission_id, actor_id=actor_id)
        try:
            scope = self._revalidate_execution_binding(commission, actor_id=actor_id)
        except (InvalidStateError, PermissionError, ValueError):
            self._mark_stale(commission, actor_id=actor_id)
            raise
        if self.consent_broker is None:
            raise InvalidStateError(
                "live commission execution requires a configured consent broker"
            )
        consumed = self.consent_broker.consume(
            approval_token,
            operation="execute_agent_live_commission",
            resource_id=commission_id,
            actor_id=actor_id,
        )
        # Re-read after one-time consumption. A concurrent scope change burns
        # the grant safely and cannot carry its authority to a different plan.
        commission = self.get(commission_id, actor_id=actor_id)
        try:
            scope = self._revalidate_execution_binding(commission, actor_id=actor_id)
            if int(scope["max_total_actions"]) != int(
                consumed["max_total_actions"]
            ):
                raise InvalidStateError(
                    "approval action budget differs from the exact commission"
                )
            if consumed.get("fingerprint") != self._fingerprint(scope):
                raise InvalidStateError("approval scope differs from the exact commission")
        except (InvalidStateError, PermissionError, ValueError, KeyError):
            self._mark_stale(commission, actor_id=actor_id)
            raise

        migration = self.application.execute_migration(
            str(scope["migration_id"]),
            approved_by=actor_id,
            max_total_actions=int(scope["max_total_actions"]),
            allowed_action_types=frozenset(
                ActionType(item) for item in scope["allowed_action_types"]
            ),
            execution_authority=f"agent_live_commission:{commission_id}",
            live_consent_payload=consumed,
        )
        status = str(migration["status"])
        now = self._now()
        commission.update(
            {
                "status": status,
                "receipt_id": migration.get("receipt_id"),
                "migration_status": status,
                "updated_at": now.isoformat(),
                "executed_at": now.isoformat(),
            }
        )
        self._record(
            commission_id,
            commission,
            event_type=f"agent_live_commission.{status}",
            actor_id=actor_id,
            payload={
                "migration_id": migration["id"],
                "migration_status": status,
                "receipt_id": migration.get("receipt_id"),
            },
        )
        return {**commission, "migration": migration}

    def reconcile(self, commission_id: str, *, actor_id: str) -> dict[str, Any]:
        commission_version, commission = self._get_versioned(
            commission_id,
            actor_id=actor_id,
        )
        migration = self._revalidate_recovery_binding(
            commission,
            actor_id=actor_id,
        )
        committed = self.application.recover_committed_migration(
            str(commission["migration_id"]),
            actor_id=actor_id,
        )
        if committed is not None:
            return self._checkpoint_recovered_commission(
                commission_id,
                commission,
                committed,
                actor_id=actor_id,
                expected_projection_version=commission_version,
            )
        if migration.get("status") not in {
            "reconciliation_required",
            "failed_recoverable",
            "executing",
        }:
            raise InvalidStateError(
                "live commission has no durable post-consent execution evidence"
            )
        attempts = self.application.action_journal.list_remote_actions(
            owner_id=actor_id,
            migration_id=str(commission["migration_id"]),
            connection_id=str(migration["destination_account_id"]),
        )
        if not attempts:
            raise InvalidStateError(
                "live commission has no durable post-consent execution evidence"
            )
        migration = self.application.reconcile_migration(
            str(commission["migration_id"]), actor_id=actor_id
        )
        return self._checkpoint_recovered_commission(
            commission_id,
            commission,
            migration,
            actor_id=actor_id,
            expected_projection_version=commission_version,
        )

    def get(self, commission_id: str, *, actor_id: str | None = None) -> dict[str, Any]:
        return self._get_versioned(commission_id, actor_id=actor_id)[1]

    def _get_versioned(
        self,
        commission_id: str,
        *,
        actor_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        value = self.application.store.get_projection(self.projection_kind, commission_id)
        if value is None:
            raise NotFoundError(f"{self.projection_kind}:{commission_id}")
        commission = dict(value[1])
        if actor_id is not None and commission.get("owner_id") != actor_id:
            raise PermissionError("only the commission owner can inspect it")
        return value[0], commission

    def list(self, *, actor_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(
            dict(value)
            for _, _, value in self.application.store.list_projections(self.projection_kind)
            if value.get("owner_id") == actor_id
        )

    def approval_scope(self, commission_id: str, *, actor_id: str) -> Mapping[str, Any]:
        commission = self.get(commission_id, actor_id=actor_id)
        scope = commission.get("approval_scope")
        if not isinstance(scope, Mapping):
            raise InvalidStateError("live commission approval scope is missing")
        return scope

    def _revalidate_execution_binding(
        self,
        commission: Mapping[str, Any],
        *,
        actor_id: str,
    ) -> Mapping[str, Any]:
        if commission.get("status") not in {"awaiting_approval", "failed_recoverable"}:
            raise InvalidStateError(
                f"live commission cannot execute from {commission.get('status')}"
            )
        scope = commission.get("approval_scope")
        if not isinstance(scope, Mapping):
            raise InvalidStateError("live commission approval scope is missing")
        if scope.get("owner_id") != actor_id or commission.get("owner_id") != actor_id:
            raise PermissionError("only the commission owner can execute it")

        migration = self.application.projection_get("migrations", str(scope["migration_id"]))
        self._require_migration_marker(migration, commission)
        if self._fingerprint(migration["plan"]) != scope.get("compiled_plan_fingerprint"):
            raise InvalidStateError("the exact migration plan changed after approval")
        if (
            migration.get("passport_id") != scope.get("passport_id")
            or int(migration.get("passport_version", 0)) != int(scope["passport_version"])
            or migration.get("effective_passport_fingerprint")
            != scope.get("effective_passport_fingerprint")
            or migration.get("platform") != scope.get("platform")
            or migration.get("destination_account_id")
            != scope.get("destination_connection_id")
        ):
            raise InvalidStateError("the migration binding changed after approval")

        selected_actions = [
            dict(item)
            for item in migration["plan"].get("actions", ())
            if str(item["action_type"]) in set(scope["allowed_action_types"])
        ][: int(scope["max_total_actions"])]
        executable_plan = scope.get("executable_plan")
        if not isinstance(executable_plan, Mapping) or selected_actions != list(
            executable_plan.get("actions", ())
        ):
            raise InvalidStateError("the exact executable action sequence changed after approval")

        passport = self.application.effective_passport(str(scope["passport_id"]))
        if passport.owner_id != actor_id or passport.version != int(scope["passport_version"]):
            raise InvalidStateError("the Passport revision changed after approval")
        if (
            self.application._passport_fingerprint(passport)
            != scope["effective_passport_fingerprint"]
        ):
            raise InvalidStateError("the effective Passport changed after approval")

        connection, _, certification, manifest = self._current_live_context(
            actor_id=actor_id,
            platform=str(scope["platform"]),
            connection_id=str(scope["destination_connection_id"]),
        )
        if connection.version != int(scope["connection_version"]):
            raise InvalidStateError("the connected account changed after approval")
        if self._certification_binding(certification) != dict(scope["certification"]):
            raise InvalidStateError("the live certification changed after approval")
        selected_types = frozenset(
            ActionType(str(item["action_type"])) for item in selected_actions
        )
        if (
            selected_types & PUBLIC_ENGAGEMENT_ACTIONS
            or not selected_types <= certification.execute
            or not selected_types <= manifest.execute
        ):
            raise InvalidStateError("the approved action sequence is no longer certified")
        return scope

    def _revalidate_recovery_binding(
        self,
        commission: Mapping[str, Any],
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        if commission.get("status") not in {
            "awaiting_approval",
            "reconciliation_required",
            "failed_recoverable",
            "executing",
        }:
            raise InvalidStateError(
                f"live commission cannot reconcile from {commission.get('status')}"
            )
        scope = commission.get("approval_scope")
        if not isinstance(scope, Mapping):
            raise InvalidStateError("live commission approval scope is missing")
        if scope.get("owner_id") != actor_id or commission.get("owner_id") != actor_id:
            raise PermissionError("only the commission owner can reconcile it")
        try:
            migration = self.application.projection_get(
                "migrations",
                str(scope["migration_id"]),
            )
            self._require_migration_marker(migration, commission)
            if (
                scope.get("schema") != "feed-passport-agent-live-commission/v1"
                or scope.get("commission_id") != commission.get("id")
                or scope.get("migration_id") != commission.get("migration_id")
                or scope.get("passport_id") != commission.get("passport_id")
                or int(scope.get("passport_version", 0))
                != int(commission.get("passport_version", 0))
                or scope.get("effective_passport_fingerprint")
                != commission.get("effective_passport_fingerprint")
                or scope.get("platform") != commission.get("platform")
                or scope.get("destination_connection_id")
                != commission.get("destination_connection_id")
                or int(scope.get("connection_version", 0))
                != int(commission.get("connection_version", 0))
                or scope.get("certification") != commission.get("certification")
                or scope.get("compiled_plan_fingerprint")
                != commission.get("sealed_plan_fingerprint")
                or self._fingerprint(migration["plan"])
                != scope.get("compiled_plan_fingerprint")
                or migration.get("passport_id") != scope.get("passport_id")
                or int(migration.get("passport_version", 0))
                != int(scope["passport_version"])
                or migration.get("effective_passport_fingerprint")
                != scope.get("effective_passport_fingerprint")
                or migration.get("platform") != scope.get("platform")
                or migration.get("destination_account_id")
                != scope.get("destination_connection_id")
            ):
                raise InvalidStateError(
                    "the durable live commission recovery binding changed"
                )
            selected_actions = [
                dict(item)
                for item in migration["plan"].get("actions", ())
                if str(item["action_type"]) in set(scope["allowed_action_types"])
            ][: int(scope["max_total_actions"])]
            executable_plan = scope.get("executable_plan")
            selected_types = frozenset(
                ActionType(str(item["action_type"])) for item in selected_actions
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidStateError(
                "the durable live commission recovery binding is invalid"
            ) from exc
        if (
            not isinstance(executable_plan, Mapping)
            or selected_actions != list(executable_plan.get("actions", ()))
            or selected_types & PUBLIC_ENGAGEMENT_ACTIONS
        ):
            raise InvalidStateError(
                "the exact executable action sequence changed before recovery"
            )
        return migration

    def _checkpoint_recovered_commission(
        self,
        commission_id: str,
        commission: Mapping[str, Any],
        migration: Mapping[str, Any],
        *,
        actor_id: str,
        expected_projection_version: int,
    ) -> dict[str, Any]:
        self._require_migration_marker(migration, commission)
        status = str(migration["status"])
        recovered = dict(commission)
        recovered.update(
            {
                "status": status,
                "receipt_id": migration.get("receipt_id"),
                "migration_status": status,
                "updated_at": self._now().isoformat(),
                "recovered_at": self._now().isoformat(),
            }
        )
        if migration.get("receipt_id"):
            recovered["executed_at"] = (
                migration.get("completed_at")
                or migration.get("approved_at")
                or recovered.get("executed_at")
            )
        try:
            self._record(
                commission_id,
                recovered,
                event_type="agent_live_commission.reconciled",
                actor_id=actor_id,
                payload={
                    "migration_id": migration["id"],
                    "migration_status": status,
                    "receipt_id": migration.get("receipt_id"),
                },
                expected_projection_version=expected_projection_version,
            )
        except ConcurrencyConflict as exc:
            concurrent = self.get(commission_id, actor_id=actor_id)
            if (
                concurrent.get("migration_status") == status
                and concurrent.get("receipt_id") == migration.get("receipt_id")
            ):
                return {**concurrent, "migration": dict(migration)}
            raise InvalidStateError(
                "live commission changed during recovery"
            ) from exc
        return {**recovered, "migration": dict(migration)}

    def _current_live_context(
        self,
        *,
        actor_id: str,
        platform: str,
        connection_id: str,
    ) -> tuple[Any, Any, ValidatedLiveCertification, Any]:
        if self.application.connections is None:
            raise InvalidStateError(
                "live commissions require an owner-bound connection registry"
            )
        connection = self.application.connections.get_connection(
            connection_id, owner_id=actor_id
        )
        if connection.platform != platform:
            raise InvalidStateError("the connected account belongs to a different platform")
        if connection.status is not ConnectionStatus.ACTIVE:
            raise InvalidStateError("the connected account is not active")
        adapter = self.application.adapters.get(platform)
        if adapter is None or not self._is_live_adapter(adapter):
            raise InvalidStateError("the selected platform has no certified live transport")
        certification = getattr(adapter, "validated_live_certification", None)
        if not isinstance(certification, ValidatedLiveCertification):
            raise InvalidStateError("the selected live transport has no validated certification")
        if certification.platform != platform:
            raise InvalidStateError("the live certification belongs to a different platform")
        now = self._now()
        if not certification.certified_at <= now < certification.expires_at:
            raise InvalidStateError("the live certification is not currently active")
        manifest = adapter.capabilities(connection_id)
        if (
            manifest.platform != platform
            or manifest.level is not CapabilityLevel.EXECUTABLE
            or not manifest.execute
        ):
            raise InvalidStateError("the connected account has no certified executable controls")
        return connection, adapter, certification, manifest

    @staticmethod
    def _redacted_demand_shape(passport: Any) -> dict[str, str]:
        creator_values = [float(value) for value in passport.creator_preferences.values()]
        return {
            "positive_creator_count_bucket": LiveCommissionService._count_bucket(
                sum(value > 0.5 for value in creator_values)
            ),
            "negative_creator_count_bucket": LiveCommissionService._count_bucket(
                sum(value < -0.5 for value in creator_values)
            ),
            "hard_exclusion_count_bucket": LiveCommissionService._count_bucket(
                len(passport.hard_exclusions)
            ),
        }

    @staticmethod
    def _count_bucket(value: int) -> str:
        if value < 1:
            return "none"
        if value <= 2:
            return "low"
        if value <= 7:
            return "medium"
        return "high"

    @staticmethod
    def _selection_summary(
        *,
        priority_mode: LivePriorityMode,
        prioritized: tuple[ActionType, ...],
        selected_action_count: int,
    ) -> str:
        family_order = ", ".join(item.value for item in prioritized) or "no families"
        return (
            f"The local redacted batch-priority planner used {priority_mode.value} priority "
            f"and ordered certified families as {family_order}. Deterministic code sealed "
            f"{selected_action_count} exact control(s) for separate owner approval."
        )

    @staticmethod
    def _certification_binding(
        certification: ValidatedLiveCertification,
    ) -> dict[str, Any]:
        return {
            "platform": certification.platform,
            "certified_at": certification.certified_at.isoformat(),
            "expires_at": certification.expires_at.isoformat(),
            "code_revision": certification.code_revision,
            "execute": sorted(item.value for item in certification.execute),
            "observe": sorted(certification.observe),
            "verify": sorted(certification.verify),
            "rollback": sorted(item.value for item in certification.rollback),
            "receipt_ref": certification.receipt_ref,
            "evidence_sha256": certification.evidence_sha256,
            "environment": certification.environment,
            "result": certification.result,
        }

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

    @staticmethod
    def _fingerprint(value: Any) -> str:
        body = json.dumps(
            to_primitive(value), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(body).hexdigest()

    @staticmethod
    def _require_migration_marker(
        migration: Mapping[str, Any], commission: Mapping[str, Any]
    ) -> None:
        if (
            migration.get("agent_live_commission_id") != commission.get("id")
            or migration.get("execution_authority") != "agent_live_commission_only"
            or migration.get("id") != commission.get("migration_id")
        ):
            raise InvalidStateError("the migration is not bound to this live commission")

    def _mark_stale(self, commission: Mapping[str, Any], *, actor_id: str) -> None:
        commission_id = str(commission["id"])
        current = self.application.store.get_projection(
            self.projection_kind,
            commission_id,
        )
        if (
            current is None
            or current[1] != to_primitive(commission)
            or current[1].get("status")
            not in {"awaiting_approval", "failed_recoverable"}
        ):
            return
        value = dict(current[1])
        value.update(
            {
                "status": "stale",
                "stop_reason": "live_binding_changed",
                "updated_at": self._now().isoformat(),
            }
        )
        try:
            self._record(
                commission_id,
                value,
                event_type="agent_live_commission.stale",
                actor_id=actor_id,
                payload={"status": "stale", "stop_reason": "live_binding_changed"},
                expected_projection_version=current[0],
            )
        except ConcurrencyConflict:
            return

    def _record_migration_marker(
        self, migration: Mapping[str, Any], *, actor_id: str
    ) -> None:
        migration_id = str(migration["id"])
        current = self.application.store.get_projection("migrations", migration_id)
        if current is None:
            raise NotFoundError(f"migrations:{migration_id}")
        self.application.store.append_event(
            aggregate_id=migration_id,
            aggregate_type="migration",
            expected_version=current[0],
            event_type="migration.bound_to_agent_live_commission",
            payload={"agent_live_commission_id": migration["agent_live_commission_id"]},
            actor_id=actor_id,
            trace_id=f"live-commission-trace:{migration['agent_live_commission_id']}",
            occurred_at=self._now(),
            projection_kind="migrations",
            projection=migration,
        )

    def _record_sealed_migration_plan(
        self,
        migration: Mapping[str, Any],
        *,
        actor_id: str,
        expected_projection_version: int,
        expected_candidate_plan_fingerprint: str,
    ) -> None:
        migration_id = str(migration["id"])
        current = self.application.store.get_projection("migrations", migration_id)
        if current is None:
            raise NotFoundError(f"migrations:{migration_id}")
        if (
            current[0] != expected_projection_version
            or self._fingerprint(current[1].get("plan"))
            != expected_candidate_plan_fingerprint
            or current[1].get("agent_live_commission_plan_fingerprint") is not None
            or current[1].get("agent_live_commission_id")
            != migration.get("agent_live_commission_id")
            or current[1].get("execution_authority") != "agent_live_commission_only"
        ):
            raise InvalidStateError("live commission migration marker changed before sealing")
        self.application.store.append_event(
            aggregate_id=migration_id,
            aggregate_type="migration",
            expected_version=expected_projection_version,
            event_type="migration.agent_live_commission_plan_sealed",
            payload={
                "agent_live_commission_id": migration["agent_live_commission_id"],
                "sealed_plan_fingerprint": migration[
                    "agent_live_commission_plan_fingerprint"
                ],
                "action_count": len(migration["plan"].get("actions", ())),
            },
            actor_id=actor_id,
            trace_id=f"live-commission-trace:{migration['agent_live_commission_id']}",
            occurred_at=self._now(),
            projection_kind="migrations",
            projection=migration,
        )

    def _record(
        self,
        commission_id: str,
        projection: Mapping[str, Any],
        *,
        event_type: str,
        actor_id: str,
        payload: Mapping[str, Any],
        expected_projection_version: int | None = None,
    ) -> None:
        current = self.application.store.get_projection(self.projection_kind, commission_id)
        self.application.store.append_event(
            aggregate_id=commission_id,
            aggregate_type="agent_live_commission",
            expected_version=(
                expected_projection_version
                if expected_projection_version is not None
                else current[0] if current else 0
            ),
            event_type=event_type,
            payload=payload,
            actor_id=actor_id,
            trace_id=f"live-commission-trace:{commission_id}",
            occurred_at=self._now(),
            projection_kind=self.projection_kind,
            projection=projection,
        )

    def _claim_candidate_finalization(
        self,
        commission_id: str,
        candidate: Mapping[str, Any],
        *,
        actor_id: str,
        expected_projection_version: int,
    ) -> int:
        claimed = dict(candidate)
        target_status = str(claimed.get("status", ""))
        if target_status not in {"awaiting_approval", "needs_human"}:
            raise InvalidStateError("live commission sealing target is invalid")
        claimed.update(
            {
                "status": "sealing",
                "sealing_target_status": target_status,
                "updated_at": self._now().isoformat(),
            }
        )
        try:
            self._record(
                commission_id,
                claimed,
                event_type="agent_live_commission.finalization_claimed",
                actor_id=actor_id,
                payload={"status": "sealing"},
                expected_projection_version=expected_projection_version,
            )
        except ConcurrencyConflict as exc:
            raise InvalidStateError(
                "live commission finalization was already claimed"
            ) from exc
        return expected_projection_version + 1

    def _resume_candidate_finalization(
        self,
        commission_id: str,
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        _, claimed = self._get_versioned(commission_id, actor_id=actor_id)
        scope, executable_plan, target_status = self._validate_sealing_claim(
            claimed,
            actor_id=actor_id,
        )
        migration_id = str(claimed["migration_id"])
        current_migration = self.application.store.get_projection(
            "migrations",
            migration_id,
        )
        if current_migration is None:
            raise NotFoundError(f"migrations:{migration_id}")
        migration_version, migration_value = current_migration
        migration = dict(migration_value)
        self._require_migration_marker(migration, claimed)
        sealed_fingerprint = str(claimed["sealed_plan_fingerprint"])
        candidate_fingerprint = str(claimed["candidate_plan_fingerprint"])

        if (
            self._fingerprint(migration.get("plan")) == candidate_fingerprint
            and migration.get("agent_live_commission_plan_fingerprint") is None
        ):
            sealed_migration = dict(migration)
            sealed_migration["plan"] = to_primitive(executable_plan)
            sealed_migration["agent_live_commission_plan_fingerprint"] = (
                sealed_fingerprint
            )
            try:
                self._record_sealed_migration_plan(
                    sealed_migration,
                    actor_id=actor_id,
                    expected_projection_version=migration_version,
                    expected_candidate_plan_fingerprint=candidate_fingerprint,
                )
            except (ConcurrencyConflict, InvalidStateError):
                # A concurrent recovery is acceptable only if it installed the
                # same exact seal from this durable claim.
                current_migration = self.application.store.get_projection(
                    "migrations",
                    migration_id,
                )
                if current_migration is None or not self._matches_sealing_claim(
                    current_migration[1],
                    executable_plan=executable_plan,
                    sealed_fingerprint=sealed_fingerprint,
                ):
                    raise InvalidStateError(
                        "live commission migration changed during sealing recovery"
                    )
        elif not self._matches_sealing_claim(
            migration,
            executable_plan=executable_plan,
            sealed_fingerprint=sealed_fingerprint,
        ):
            raise InvalidStateError(
                "live commission migration does not match its durable sealing claim"
            )

        current_version, current_claim = self._get_versioned(
            commission_id,
            actor_id=actor_id,
        )
        if current_claim.get("status") != "sealing":
            if self._matches_completed_sealing_claim(current_claim, claimed):
                return current_claim
            raise InvalidStateError("live commission sealing claim changed during recovery")
        if self._fingerprint(current_claim) != self._fingerprint(claimed):
            raise InvalidStateError("live commission sealing claim changed during recovery")

        completed = dict(current_claim)
        completed.pop("sealing_target_status", None)
        completed.update(
            {
                "status": target_status,
                "updated_at": self._now().isoformat(),
            }
        )
        try:
            self._record(
                commission_id,
                completed,
                event_type="agent_live_commission.previewed",
                actor_id=actor_id,
                payload={
                    "status": target_status,
                    "selected_action_count": int(
                        completed.get("selected_action_count", 0)
                    ),
                    "selected_action_types": list(
                        completed.get("selected_action_types", ())
                    ),
                    "prioritized_action_types": list(
                        scope.get("prioritized_action_types", ())
                    ),
                },
                expected_projection_version=current_version,
            )
        except ConcurrencyConflict as exc:
            concurrent = self.get(commission_id, actor_id=actor_id)
            if self._matches_completed_sealing_claim(concurrent, claimed):
                return concurrent
            raise InvalidStateError(
                "live commission sealing claim changed during recovery"
            ) from exc
        return completed

    def _validate_sealing_claim(
        self,
        claimed: Mapping[str, Any],
        *,
        actor_id: str,
    ) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:
        if claimed.get("status") != "sealing":
            raise InvalidStateError(
                f"live commission cannot resume sealing from {claimed.get('status')}"
            )
        target_status = str(claimed.get("sealing_target_status", ""))
        scope = claimed.get("approval_scope")
        if target_status not in {"awaiting_approval", "needs_human"} or not isinstance(
            scope,
            Mapping,
        ):
            raise InvalidStateError("live commission durable sealing claim is incomplete")
        executable_plan = scope.get("executable_plan")
        if not isinstance(executable_plan, Mapping):
            raise InvalidStateError("live commission durable executable plan is missing")
        sealed_fingerprint = self._fingerprint(executable_plan)
        if (
            claimed.get("owner_id") != actor_id
            or scope.get("owner_id") != actor_id
            or scope.get("commission_id") != claimed.get("id")
            or scope.get("migration_id") != claimed.get("migration_id")
            or scope.get("passport_id") != claimed.get("passport_id")
            or int(scope.get("passport_version", 0))
            != int(claimed.get("passport_version", 0))
            or scope.get("effective_passport_fingerprint")
            != claimed.get("effective_passport_fingerprint")
            or scope.get("platform") != claimed.get("platform")
            or scope.get("destination_connection_id")
            != claimed.get("destination_connection_id")
            or int(scope.get("connection_version", 0))
            != int(claimed.get("connection_version", 0))
            or scope.get("certification") != claimed.get("certification")
            or scope.get("compiled_plan_fingerprint") != sealed_fingerprint
            or claimed.get("sealed_plan_fingerprint") != sealed_fingerprint
        ):
            raise InvalidStateError("live commission durable sealing binding is invalid")
        actions = list(executable_plan.get("actions", ()))
        selected_types = list(
            dict.fromkeys(str(item.get("action_type", "")) for item in actions)
        )
        try:
            action_types = frozenset(ActionType(value) for value in selected_types)
        except ValueError as exc:
            raise InvalidStateError(
                "live commission durable executable plan has an invalid action type"
            ) from exc
        if (
            action_types & PUBLIC_ENGAGEMENT_ACTIONS
            or selected_types != list(scope.get("allowed_action_types", ()))
            or selected_types != list(claimed.get("selected_action_types", ()))
            or len(actions) != int(scope.get("max_total_actions", -1))
            or len(actions) != int(claimed.get("selected_action_count", -1))
            or len(actions) > int(claimed.get("max_total_actions", -1))
            or list(scope.get("prioritized_action_types", ()))
            != list(claimed.get("planner_evidence", {}).get("prioritized_action_types", ()))
        ):
            raise InvalidStateError("live commission durable sealing scope is invalid")
        return scope, executable_plan, target_status

    def _matches_sealing_claim(
        self,
        migration: Mapping[str, Any],
        *,
        executable_plan: Mapping[str, Any],
        sealed_fingerprint: str,
    ) -> bool:
        return (
            migration.get("agent_live_commission_plan_fingerprint")
            == sealed_fingerprint
            and self._fingerprint(migration.get("plan")) == sealed_fingerprint
            and to_primitive(migration.get("plan")) == to_primitive(executable_plan)
        )

    @staticmethod
    def _matches_completed_sealing_claim(
        completed: Mapping[str, Any],
        claimed: Mapping[str, Any],
    ) -> bool:
        return (
            completed.get("status") == claimed.get("sealing_target_status")
            and completed.get("approval_scope") == claimed.get("approval_scope")
            and completed.get("sealed_plan_fingerprint")
            == claimed.get("sealed_plan_fingerprint")
            and completed.get("planner_evidence") == claimed.get("planner_evidence")
        )

    def _now(self) -> datetime:
        value = self.application.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("live commission clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    def _id(self, prefix: str) -> str:
        return f"{prefix}-{self.application.id_factory()}"
