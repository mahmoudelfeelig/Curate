from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Mapping
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from feed_passport.application import AgentMissionRunner, CuratorApplication
from feed_passport.domain import (
    ActionType,
    AgentMissionAcceptance,
    AgentMissionBudget,
    OverlayMode,
)
from feed_passport.infrastructure.serialization import to_primitive

from .consent import ConsentBroker, ConsentGrant, require_model_alert_only_monitor


class AgentCommandName(StrEnum):
    INSPECT_PASSPORT = "inspect_passport"
    CAPTURE_PASSPORT = "capture_passport"
    EXPORT_PASSPORT = "export_passport"
    IMPORT_PASSPORT = "import_passport"
    CREATE_CHECKPOINT = "create_checkpoint"
    RESTORE_CHECKPOINT = "restore_checkpoint"
    LIST_PLATFORMS = "list_platforms"
    LIST_TEMPLATES = "list_templates"
    REVISE_PASSPORT = "revise_passport"
    CREATE_VISA = "create_visa"
    REVOKE_VISA = "revoke_visa"
    CREATE_VISA_FROM_TEMPLATE = "create_visa_from_template"
    PROCESS_DUE_VISAS = "process_due_visas"
    CREATE_SHARE = "create_share"
    REVOKE_SHARE = "revoke_share"
    CREATE_COMPANION = "create_companion"
    PREVIEW_MIGRATION = "preview_migration"
    APPROVE_MIGRATION = "approve_migration"
    EXECUTE_MIGRATION = "execute_migration"
    APPROVE_ROLLBACK = "approve_rollback"
    ROLLBACK_RECEIPT = "rollback_receipt"
    WATCH_DRIFT = "watch_drift"
    CREATE_DRIFT_MONITOR = "create_drift_monitor"
    STOP_DRIFT_MONITOR = "stop_drift_monitor"
    FIND_CREATOR = "find_creator"
    PRESERVE_CREATOR = "preserve_creator"
    PREVIEW_MISSION = "preview_mission"
    APPROVE_MISSION = "approve_mission"
    EXECUTE_MISSION = "execute_mission"
    CANCEL_MISSION = "cancel_mission"
    APPROVE_MISSION_ROLLBACK = "approve_mission_rollback"
    ROLLBACK_MISSION = "rollback_mission"


class AgentCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: AgentCommandName
    actor_id: str = Field(min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    status: str
    message: str
    requires_confirmation: bool = False
    data: dict[str, Any] | list[Any] | None = None
    trace: list[dict[str, Any]] = Field(default_factory=list)


class CuratorAgentService:
    """Deterministic command boundary shared by HTTP, WebMCP, and AgentCore.

    A Strands model may select these commands, but authority and execution remain
    in application and consent code. This keeps an LLM from becoming the policy
    engine or silently widening scope.
    """

    def __init__(
        self,
        application: CuratorApplication,
        broker: ConsentBroker,
        mission_runner: AgentMissionRunner | None = None,
    ) -> None:
        self.application = application
        self.broker = broker
        self.mission_runner = mission_runner or AgentMissionRunner(application)

    def handle(self, command: AgentCommand) -> AgentReply:
        trace_id = f"agent-trace-{uuid4().hex}"
        args = dict(command.arguments)
        trace: list[dict[str, Any]] = [
            {"stage": "intent", "command": command.command.value, "status": "accepted"}
        ]

        if command.command is AgentCommandName.INSPECT_PASSPORT:
            passport_id = self._required(args, "passport_id")
            base = self.application.get_passport(passport_id)
            effective = self.application.effective_passport(passport_id)
            data = {"base": to_primitive(base), "effective": to_primitive(effective)}
            return self._reply(trace_id, "Passport inspected without changing it.", data, trace)

        if command.command is AgentCommandName.CAPTURE_PASSPORT:
            value = self.application.infer_passport_from_account(
                platform=self._required(args, "platform"),
                account_id=self._required(args, "account_id"),
                owner_id=command.actor_id,
                name=self._required(args, "name"),
                intent=self._required(args, "intent"),
                trace_id=trace_id,
            )
            trace.extend(
                (
                    {"stage": "observe", "status": "completed"},
                    {"stage": "private_state", "event": "passport.captured", "status": "completed"},
                )
            )
            return self._reply(
                trace_id,
                "Authorized source observation captured as a new portable Passport.",
                to_primitive(value),
                trace,
            )

        if command.command is AgentCommandName.EXPORT_PASSPORT:
            passport = self.application.get_passport(self._required(args, "passport_id"))
            if passport.owner_id != command.actor_id:
                raise PermissionError("only the Passport owner can export it")
            data = {
                "format": "feed-passport/v1",
                "passport": to_primitive(passport),
                "privacy": "Preference intent only; no credentials or raw activity history.",
            }
            return self._reply(trace_id, "Portable Passport export prepared.", data, trace)

        if command.command is AgentCommandName.IMPORT_PASSPORT:
            value = self.application.import_passport(
                actor_id=command.actor_id,
                format_name=self._required(args, "format"),
                passport_data=self._mapping(args, "passport"),
            )
            trace.append({"stage": "private_state", "event": "passport.imported", "status": "completed"})
            return self._reply(trace_id, "Portable Passport imported as a new identity.", value, trace)

        if command.command is AgentCommandName.CREATE_CHECKPOINT:
            value = self.application.create_checkpoint(
                self._required(args, "passport_id"),
                actor_id=command.actor_id,
                label=self._required(args, "label"),
            )
            trace.append({"stage": "private_state", "event": "checkpoint.created", "status": "completed"})
            return self._reply(trace_id, "Passport checkpoint created.", value, trace)

        if command.command is AgentCommandName.RESTORE_CHECKPOINT:
            value = self.application.restore_checkpoint(
                self._required(args, "checkpoint_id"),
                actor_id=command.actor_id,
            )
            trace.append({"stage": "private_state", "event": "checkpoint.restored", "status": "completed"})
            return self._reply(trace_id, "Checkpoint restored as a new Passport version.", to_primitive(value), trace)

        if command.command is AgentCommandName.LIST_PLATFORMS:
            return self._reply(
                trace_id,
                "Capability evidence loaded. Unsupported controls remain guided handoffs.",
                list(self.application.list_platforms()),
                trace,
            )

        if command.command is AgentCommandName.LIST_TEMPLATES:
            return self._reply(trace_id, "Temporary visa templates loaded.", list(self.application.list_templates()), trace)

        if command.command is AgentCommandName.REVISE_PASSPORT:
            value = self.application.revise_passport(
                self._required(args, "passport_id"),
                actor_id=command.actor_id,
                changes=self._mapping(args, "changes"),
            )
            trace.append({"stage": "private_state", "event": "passport.revised", "status": "completed"})
            return self._reply(trace_id, f"Passport revised to version {value.version}.", to_primitive(value), trace)

        if command.command is AgentCommandName.CREATE_VISA:
            starts_at = self._datetime(args, "starts_at")
            expires_at = self._datetime(args, "expires_at")
            value = self.application.create_overlay(
                base_passport_id=self._required(args, "passport_id"),
                name=self._required(args, "name"),
                topic_adjustments={str(key): float(item) for key, item in self._mapping(args, "topic_adjustments").items()},
                add_exclusions=frozenset(str(item) for item in args.get("add_exclusions", ())),
                remove_exclusions=frozenset(str(item) for item in args.get("remove_exclusions", ())),
                starts_at=starts_at,
                expires_at=expires_at,
                mode=OverlayMode(str(args.get("mode", OverlayMode.ISOLATED.value))),
                serendipity=self._optional_float(args.get("serendipity")),
                max_outrage=self._optional_float(args.get("max_outrage")),
                actor_id=command.actor_id,
            )
            trace.append({"stage": "private_state", "event": "visa.created", "status": "completed"})
            return self._reply(trace_id, f"Temporary visa created through {expires_at.isoformat()}.", value, trace)

        if command.command is AgentCommandName.REVOKE_VISA:
            value = self.application.revoke_overlay(
                self._required(args, "visa_id"),
                actor_id=command.actor_id,
            )
            trace.append({"stage": "private_state", "event": "visa.revoked", "status": "completed"})
            return self._reply(trace_id, "Temporary visa revoked and linked rollback processed.", value, trace)

        if command.command is AgentCommandName.CREATE_VISA_FROM_TEMPLATE:
            value = self.application.create_overlay_from_template(
                self._required(args, "template_id"),
                base_passport_id=self._required(args, "passport_id"),
                actor_id=command.actor_id,
                starts_at=self._datetime(args, "starts_at") if args.get("starts_at") else None,
            )
            trace.append({"stage": "private_state", "event": "visa.created_from_template", "status": "completed"})
            return self._reply(trace_id, "Temporary visa created from a reviewed template.", value, trace)

        if command.command is AgentCommandName.PROCESS_DUE_VISAS:
            value = list(self.application.process_due_jobs())
            trace.append({"stage": "scheduler", "event": "visas.processed", "status": "completed"})
            return self._reply(trace_id, "Due visa transitions and approved rollbacks were processed.", value, trace)

        if command.command is AgentCommandName.CREATE_SHARE:
            value = self.application.create_share_slice(
                passport_id=self._required(args, "passport_id"),
                topic_names=tuple(str(item) for item in args.get("topic_names", ())),
                creator_ids=tuple(str(item) for item in args.get("creator_ids", ())),
                include_serendipity=bool(args.get("include_serendipity", False)),
                include_formats=bool(args.get("include_formats", False)),
                include_exclusions=bool(args.get("include_exclusions", False)),
                expires_at=self._datetime(args, "expires_at"),
                actor_id=command.actor_id,
            )
            trace.append({"stage": "consent", "event": "share.created", "status": "completed"})
            return self._reply(trace_id, "Only the selected Passport fields were shared.", value, trace)

        if command.command is AgentCommandName.REVOKE_SHARE:
            value = self.application.revoke_share_slice(
                self._required(args, "slice_id"),
                actor_id=command.actor_id,
            )
            trace.append({"stage": "consent", "event": "share.revoked", "status": "completed"})
            return self._reply(trace_id, "Shared Passport slice revoked.", value, trace)

        if command.command is AgentCommandName.CREATE_COMPANION:
            value = self.application.create_companion_blend(
                name=self._required(args, "name"),
                slice_ids=tuple(str(item) for item in args.get("slice_ids", ())),
                weights={str(key): float(item) for key, item in self._mapping(args, "weights").items()},
                strategy=str(args.get("strategy", "bridge")),
                expires_at=self._datetime(args, "expires_at"),
                actor_id=command.actor_id,
            )
            trace.append({"stage": "consent", "event": "companion.created", "status": "completed"})
            return self._reply(trace_id, "Companion Passport created from explicit, expiring slices.", value, trace)

        if command.command is AgentCommandName.PREVIEW_MIGRATION:
            value = self.application.prepare_migration(
                passport_id=self._required(args, "passport_id"),
                platform=self._required(args, "platform"),
                destination_account_id=self._required(args, "destination_account_id"),
                actor_id=command.actor_id,
                overlay_id=str(args["overlay_id"]) if args.get("overlay_id") else None,
            )
            trace.extend(
                (
                    {"stage": "observe", "status": "completed"},
                    {"stage": "translate", "status": "completed"},
                    {"stage": "counterfactual", "status": "completed" if value.get("preview") else "unavailable"},
                    {"stage": "consent", "status": "required"},
                )
            )
            return self._reply(
                trace_id,
                "Translation preview is ready. No destination actions were executed.",
                value,
                trace,
                requires_confirmation=True,
            )

        if command.command is AgentCommandName.APPROVE_MIGRATION:
            grant = self.broker.issue_for_migration(
                self._required(args, "migration_id"),
                actor_id=command.actor_id,
                max_total_actions=int(args["max_total_actions"]) if args.get("max_total_actions") is not None else None,
                ttl=timedelta(seconds=int(args.get("ttl_seconds", 600))),
            )
            trace.append(
                {"stage": "consent", "status": "granted", "expires_at": grant.expires_at.isoformat()}
            )
            return self._reply(
                trace_id,
                "One-time approval issued for the exact previewed action envelope.",
                self._grant_data(grant),
                trace,
            )

        if command.command is AgentCommandName.EXECUTE_MIGRATION:
            migration_id = self._required(args, "migration_id")
            grant = self.broker.consume(
                self._required(args, "approval_token"),
                operation="execute_migration",
                resource_id=migration_id,
                actor_id=command.actor_id,
            )
            value = self.application.execute_migration(
                migration_id,
                approved_by=command.actor_id,
                max_total_actions=int(grant["max_total_actions"]),
            )
            trace.extend(
                (
                    {"stage": "policy", "status": "enforced"},
                    {"stage": "execute", "status": "completed"},
                    {"stage": "verify", "status": "completed"},
                    {"stage": "receipt", "status": "issued", "receipt_id": value["receipt_id"]},
                )
            )
            return self._reply(trace_id, "Approved controls applied, verified, and recorded.", value, trace)

        if command.command is AgentCommandName.APPROVE_ROLLBACK:
            grant = self.broker.issue_for_rollback(
                self._required(args, "receipt_id"),
                actor_id=command.actor_id,
                platform=self._required(args, "platform"),
                ttl=timedelta(seconds=int(args.get("ttl_seconds", 600))),
            )
            trace.append(
                {"stage": "consent", "status": "granted", "expires_at": grant.expires_at.isoformat()}
            )
            return self._reply(trace_id, "One-time rollback approval issued.", self._grant_data(grant), trace)

        if command.command is AgentCommandName.ROLLBACK_RECEIPT:
            receipt_id = self._required(args, "receipt_id")
            self.broker.consume(
                self._required(args, "approval_token"),
                operation="rollback_receipt",
                resource_id=receipt_id,
                actor_id=command.actor_id,
            )
            value = self.application.rollback_receipt(
                receipt_id,
                actor_id=command.actor_id,
                platform=self._required(args, "platform"),
            )
            trace.extend(
                (
                    {"stage": "rollback", "status": "completed"},
                    {"stage": "receipt", "status": "updated"},
                )
            )
            return self._reply(trace_id, "Reversible controls restored and the receipt was updated.", value, trace)

        if command.command is AgentCommandName.PREVIEW_MISSION:
            budget_value = args.get("budget", {})
            if not isinstance(budget_value, Mapping):
                raise ValueError("budget must be an object")
            acceptance_value = args.get("acceptance_thresholds", {})
            if not isinstance(acceptance_value, Mapping):
                raise ValueError("acceptance_thresholds must be an object")
            value = self.mission_runner.preview(
                actor_id=command.actor_id,
                goal=self._required(args, "goal"),
                passport_id=self._required(args, "passport_id"),
                destination_twin=self._required(args, "destination_twin"),
                destination_account_id=self._required(args, "destination_account_id"),
                budget=AgentMissionBudget(
                    total_actions=int(budget_value.get("total_actions", 8)),
                    per_iteration_actions=int(budget_value.get("per_iteration_actions", 3)),
                    max_iterations=int(budget_value.get("max_iterations", 3)),
                ),
                acceptance=AgentMissionAcceptance(**dict(acceptance_value)),
                min_improvement=float(args.get("min_improvement", 0.01)),
            )
            trace.extend(value["trace"])
            return self._reply(
                trace_id,
                "Local-twin mission previewed. No destination controls were changed.",
                value,
                trace,
                requires_confirmation=value["status"] == "awaiting_approval",
            )

        if command.command is AgentCommandName.APPROVE_MISSION:
            grant = self.broker.issue_for_mission(
                self._required(args, "mission_id"),
                actor_id=command.actor_id,
                ttl=timedelta(seconds=int(args.get("ttl_seconds", 600))),
            )
            trace.append(
                {"stage": "consent", "status": "granted", "expires_at": grant.expires_at.isoformat()}
            )
            return self._reply(
                trace_id,
                "One-time approval issued for the bounded local-twin mission.",
                self._grant_data(grant),
                trace,
            )

        if command.command is AgentCommandName.EXECUTE_MISSION:
            mission_id = self._required(args, "mission_id")
            self.broker.consume(
                self._required(args, "approval_token"),
                operation="execute_agent_mission",
                resource_id=mission_id,
                actor_id=command.actor_id,
            )
            value = self.mission_runner.execute(mission_id, actor_id=command.actor_id)
            trace.extend(value["trace"])
            return self._reply(
                trace_id,
                f"Local-twin mission stopped with reason {value['stop_reason']}.",
                value,
                trace,
            )

        if command.command is AgentCommandName.CANCEL_MISSION:
            value = self.mission_runner.cancel(
                self._required(args, "mission_id"),
                actor_id=command.actor_id,
            )
            trace.append({"stage": "stop", "status": "cancelled"})
            return self._reply(trace_id, "Mission cancelled before approval was consumed.", value, trace)

        if command.command is AgentCommandName.APPROVE_MISSION_ROLLBACK:
            grant = self.broker.issue_for_mission_rollback(
                self._required(args, "mission_id"),
                actor_id=command.actor_id,
                ttl=timedelta(seconds=int(args.get("ttl_seconds", 600))),
            )
            trace.append(
                {"stage": "consent", "status": "granted", "expires_at": grant.expires_at.isoformat()}
            )
            return self._reply(
                trace_id,
                "Separate one-time approval issued for reverse-order mission rollback.",
                self._grant_data(grant),
                trace,
            )

        if command.command is AgentCommandName.ROLLBACK_MISSION:
            mission_id = self._required(args, "mission_id")
            self.broker.consume(
                self._required(args, "approval_token"),
                operation="rollback_agent_mission",
                resource_id=mission_id,
                actor_id=command.actor_id,
            )
            value = self.mission_runner.rollback(mission_id, actor_id=command.actor_id)
            trace.extend(value["trace"])
            return self._reply(
                trace_id,
                "Mission receipts rolled back in reverse execution order.",
                value,
                trace,
            )

        if command.command is AgentCommandName.WATCH_DRIFT:
            value = self.application.drift_watch(
                passport_id=self._required(args, "passport_id"),
                platform=self._required(args, "platform"),
                account_id=self._required(args, "account_id"),
                actor_id=command.actor_id,
            )
            trace.append({"stage": "evaluate", "status": "completed"})
            return self._reply(trace_id, "Drift measured; any repair remains a preview.", value, trace)

        if command.command is AgentCommandName.CREATE_DRIFT_MONITOR:
            mode = str(args.get("mode", "alert_only"))
            allowed_actions = tuple(str(item) for item in args.get("allowed_actions", ()))
            require_model_alert_only_monitor(
                mode=mode,
                allowed_actions=allowed_actions,
            )
            value = self.application.create_drift_monitor(
                passport_id=self._required(args, "passport_id"),
                platform=self._required(args, "platform"),
                account_id=self._required(args, "account_id"),
                actor_id=command.actor_id,
                interval_minutes=int(args.get("interval_minutes", 60)),
                expires_at=self._datetime(args, "expires_at"),
                mode=mode,
                allowed_actions=frozenset(ActionType(item) for item in allowed_actions),
                max_actions_per_run=int(args.get("max_actions_per_run", 3)),
                minimum_confidence=float(args.get("minimum_confidence", 0.8)),
            )
            trace.append({"stage": "scheduler", "event": "drift_monitor.created", "status": "completed"})
            return self._reply(trace_id, "Alert-only drift monitor created with an expiry.", value, trace)

        if command.command is AgentCommandName.STOP_DRIFT_MONITOR:
            value = self.application.stop_drift_monitor(
                self._required(args, "monitor_id"),
                actor_id=command.actor_id,
            )
            trace.append({"stage": "scheduler", "event": "drift_monitor.stopped", "status": "completed"})
            return self._reply(trace_id, "Drift monitor stopped immediately.", value, trace)

        if command.command is AgentCommandName.FIND_CREATOR:
            value = self.application.creator_continuity(
                self._required(args, "creator_id"),
                self._required(args, "destination_platform"),
            )
            trace.append({"stage": "identity", "status": value["status"]})
            return self._reply(trace_id, "Creator continuity lookup completed.", value, trace)

        if command.command is AgentCommandName.PRESERVE_CREATOR:
            value = self.application.preserve_creator_match(
                passport_id=self._required(args, "passport_id"),
                creator_id=self._required(args, "creator_id"),
                destination_platform=self._required(args, "destination_platform"),
                actor_id=command.actor_id,
            )
            trace.append({"stage": "identity", "event": "creator.preserved", "status": "completed"})
            return self._reply(trace_id, "Reviewed directory creator match preserved in the Passport.", value, trace)

        raise ValueError(f"unsupported agent command: {command.command}")

    @staticmethod
    def _reply(
        trace_id: str,
        message: str,
        data: Any,
        trace: list[dict[str, Any]],
        *,
        requires_confirmation: bool = False,
    ) -> AgentReply:
        return AgentReply(
            trace_id=trace_id,
            status="ok",
            message=message,
            requires_confirmation=requires_confirmation,
            data=to_primitive(data),
            trace=trace,
        )

    @staticmethod
    def _grant_data(grant: ConsentGrant) -> dict[str, Any]:
        return {
            "approval_token": grant.token,
            "operation": grant.operation,
            "resource_id": grant.resource_id,
            "max_total_actions": grant.max_total_actions,
            "issued_at": grant.issued_at.isoformat(),
            "expires_at": grant.expires_at.isoformat(),
            "summary": dict(grant.summary),
        }

    @staticmethod
    def _required(arguments: Mapping[str, Any], name: str) -> str:
        value = arguments.get(name)
        if value is None or not str(value).strip():
            raise ValueError(f"{name} is required")
        return str(value)

    @staticmethod
    def _mapping(arguments: Mapping[str, Any], name: str) -> Mapping[str, Any]:
        value = arguments.get(name)
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} must be an object")
        return value

    @staticmethod
    def _datetime(arguments: Mapping[str, Any], name: str) -> datetime:
        value = CuratorAgentService._required(arguments, name)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"{name} must include a timezone")
        return parsed

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        return None if value is None else float(value)
