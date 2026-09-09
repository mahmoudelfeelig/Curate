from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from strands import Agent, tool
from strands.hooks import BeforeToolCallEvent, HookRegistry
from strands.models import Model

from feed_passport.application.live_commission import (
    LiveCommissionService,
    LivePriorityMode,
)
from feed_passport.domain import ActionType

from .mission_planner import PlannerActionType
from .model_provider import ModelExecutionProfile


class LiveCommissionPlannerError(RuntimeError):
    """A model failed the redacted, proposal-only live commission protocol."""


class LiveCommissionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    prioritized_action_types: list[PlannerActionType] = Field(max_length=17)

    @field_validator("prioritized_action_types")
    @classmethod
    def require_unique_actions(
        cls, value: list[PlannerActionType]
    ) -> list[PlannerActionType]:
        if len(value) != len(set(value)):
            raise ValueError("prioritized action types must be unique")
        return value


LIVE_COMMISSION_SYSTEM_PROMPT = """
You are the Feed Passport local redacted batch-priority planner. Deterministic server code has already
compiled an exact private-control plan for an owner-authorized YouTube or Bluesky connected account. You
receive only a typed owner-selected priority mode, coarse demand buckets, and certified action-family
counts. You never receive or control owner text, account identifiers, target identifiers, credentials, raw
account observations, exact actions, consent, execution, reconciliation, receipts, or rollback.

Your only authority is to order every available action family exactly once. Ordering matters when the
server's locked action budget is smaller than total demand. Do not invent, omit, duplicate, approve, or
execute controls. Do not author interpretations, rationales, targets, budgets, capability claims, or stop
conditions.

Protocol: call inspect_redacted_demand exactly once, then inspect_certified_action_families exactly once,
then call submit_live_family_priority exactly once. Deterministic code applies the order to the precompiled
private plan and a human must review and approve the exact resulting sequence before any write.
""".strip()


@dataclass(slots=True)
class LiveCommissionProtocolHooks:
    rejected_calls: list[dict[str, Any]] = field(default_factory=list)

    def register_hooks(self, registry: HookRegistry, **_: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool_call)

    def before_tool_call(self, event: BeforeToolCallEvent) -> None:
        name = str(event.tool_use.get("name", ""))
        inputs = dict(event.tool_use.get("input") or {})
        allowed_fields = {
            "inspect_redacted_demand": frozenset(),
            "inspect_certified_action_families": frozenset(),
            "submit_live_family_priority": frozenset({"prioritized_action_types"}),
        }
        unexpected = sorted(set(inputs) - allowed_fields.get(name, frozenset()))
        if name not in allowed_fields or unexpected:
            self.rejected_calls.append(
                {
                    "tool": name,
                    "reason": "unknown_tool" if name not in allowed_fields else "unexpected_fields",
                    "field_names": unexpected,
                }
            )
            event.cancel_tool = "The live planner call exceeded its proposal-only schema."


class LiveCommissionPlanner:
    """Run one request-bound planner over redacted precompiled action families."""

    def __init__(
        self,
        commission_service: LiveCommissionService,
        *,
        model_factory: Callable[[], Model],
        provider: str,
        model_id: str,
        timeout_seconds: float,
        endpoint_scope: str = "loopback_only",
        execution_profile: ModelExecutionProfile | None = None,
    ) -> None:
        if execution_profile is None:
            if endpoint_scope not in {"loopback_only", "scripted_no_network"}:
                raise ValueError("external live planning requires an explicit execution profile")
            execution_profile = ModelExecutionProfile.local(
                provider=provider,
                model_id=model_id,
                endpoint_scope=endpoint_scope,
            )
        elif (
            provider.strip() != execution_profile.provider
            or model_id.strip() != execution_profile.model_id
            or endpoint_scope != execution_profile.endpoint_scope
        ):
            raise ValueError("planner fields must match the explicit execution profile")
        if execution_profile.external_model_calls:
            raise ValueError("live commission priority planning is local-only")
        self.commission_service = commission_service
        self.model_factory = model_factory
        self.provider = execution_profile.provider
        self.model_id = execution_profile.model_id
        self.timeout_seconds = timeout_seconds
        self.endpoint_scope = execution_profile.endpoint_scope
        self.execution_profile = execution_profile

    async def preview(
        self,
        *,
        actor_id: str,
        priority_mode: LivePriorityMode | str,
        passport_id: str,
        platform: str,
        destination_connection_id: str,
        max_total_actions: int,
        allowed_action_types: frozenset[ActionType] | None = None,
    ) -> dict[str, Any]:
        candidate = self.commission_service.prepare_candidate(
            actor_id=actor_id,
            priority_mode=priority_mode,
            passport_id=passport_id,
            platform=platform,
            destination_connection_id=destination_connection_id,
            max_total_actions=max_total_actions,
            allowed_action_types=allowed_action_types,
        )
        available = frozenset(ActionType(name) for name in candidate.action_family_counts)
        planner_safe = frozenset(ActionType(item.value) for item in PlannerActionType)
        if not available <= planner_safe:
            self.commission_service.planning_failed(
                candidate.commission_id, actor_id=actor_id
            )
            raise LiveCommissionPlannerError(
                "the precompiled plan contains an action family outside the planner schema"
            )

        events: list[dict[str, str]] = []
        proposal_box: list[LiveCommissionProposal] = []

        @tool
        def inspect_redacted_demand() -> dict[str, Any]:
            """Inspect the typed priority and coarse private-demand buckets."""

            events.append(
                {"name": "inspect_redacted_demand", "status": "completed"}
            )
            return {
                "schema": "feed-passport-live-demand/v1",
                "priority_mode": candidate.priority_mode.value,
                "demand_shape": dict(candidate.demand_shape),
            }

        @tool
        def inspect_certified_action_families() -> dict[str, Any]:
            """Inspect only certified action-family names and aggregate counts."""

            events.append(
                {"name": "inspect_certified_action_families", "status": "completed"}
            )
            return {
                "schema": "feed-passport-live-families/v1",
                "platform_family": candidate.platform,
                "families": [
                    {"action_type": name, "count": count}
                    for name, count in candidate.action_family_counts.items()
                ],
                "locked_max_total_actions": candidate.max_total_actions,
                "exact_targets_withheld": True,
                "authority": "priority_only",
            }

        @tool
        def submit_live_family_priority(
            prioritized_action_types: list[PlannerActionType],
        ) -> dict[str, Any]:
            """Order every available action family without receiving exact targets.

            Args:
                prioritized_action_types: Unique permutation of every available family.
            """

            events.append(
                {"name": "submit_live_family_priority", "status": "attempted"}
            )
            if [item["name"] for item in events[:-1]] != [
                "inspect_redacted_demand",
                "inspect_certified_action_families",
            ]:
                raise LiveCommissionPlannerError(
                    "the planner must complete both redacted inspections before submitting"
                )
            if proposal_box:
                raise LiveCommissionPlannerError("the planner may submit exactly one proposal")
            proposal = LiveCommissionProposal(
                prioritized_action_types=prioritized_action_types,
            )
            prioritized = tuple(
                ActionType(item.value) for item in proposal.prioritized_action_types
            )
            if len(prioritized) != len(available) or frozenset(prioritized) != available:
                raise LiveCommissionPlannerError(
                    "the model must prioritize every available action family exactly once"
                )
            proposal_box.append(proposal)
            events[-1]["status"] = "accepted"
            return {
                "status": "accepted_for_exact_deterministic_sealing",
                "prioritized_action_types": [item.value for item in prioritized],
                "consent": "still_required",
                "executed": False,
            }

        model: Any | None = None
        hooks = LiveCommissionProtocolHooks()
        started = time.perf_counter()
        primary_failure = False
        try:
            model = self.model_factory()
            agent = Agent(
                model=model,
                tools=[
                    inspect_redacted_demand,
                    inspect_certified_action_families,
                    submit_live_family_priority,
                ],
                hooks=[hooks],
                system_prompt=LIVE_COMMISSION_SYSTEM_PROMPT,
                callback_handler=None,
                name="feed-passport-live-commission-planner",
                description="Local priority-only planner over a redacted certified live candidate.",
                trace_attributes={
                    "service.name": "feed-passport-live-commission-planner",
                    "product.local_only": not self.execution_profile.external_model_calls,
                    "product.priority_only": True,
                    "product.public_engagement": False,
                    "product.redacted_live_context": True,
                    "product.endpoint_scope": self.endpoint_scope,
                },
            )
            async with asyncio.timeout(self.timeout_seconds):
                result = await agent.invoke_async(
                    "Inspect the redacted candidate and submit one bounded proposal.",
                    limits={"turns": 3, "output_tokens": 1600, "total_tokens": 12000},
                )
        except TimeoutError as exc:
            primary_failure = True
            self.commission_service.planning_failed(
                candidate.commission_id, actor_id=actor_id
            )
            raise LiveCommissionPlannerError(
                "the model timed out before a safe live proposal"
            ) from exc
        except Exception as exc:
            primary_failure = True
            self.commission_service.planning_failed(
                candidate.commission_id, actor_id=actor_id
            )
            raise LiveCommissionPlannerError(
                f"the model failed the live proposal protocol: {type(exc).__name__}"
            ) from exc
        finally:
            if model is not None:
                try:
                    await self._close_model(model)
                except Exception as exc:
                    self.commission_service.planning_failed(
                        candidate.commission_id, actor_id=actor_id
                    )
                    if not primary_failure:
                        raise LiveCommissionPlannerError(
                            f"the local model could not close safely: {type(exc).__name__}"
                        ) from exc

        expected = [
            "inspect_redacted_demand",
            "inspect_certified_action_families",
            "submit_live_family_priority",
        ]
        if hooks.rejected_calls or [item["name"] for item in events] != expected:
            self.commission_service.planning_failed(
                candidate.commission_id, actor_id=actor_id
            )
            raise LiveCommissionPlannerError(
                "the model did not complete the exact redacted inspection and proposal sequence"
            )
        if len(proposal_box) != 1 or events[-1]["status"] != "accepted":
            self.commission_service.planning_failed(
                candidate.commission_id, actor_id=actor_id
            )
            raise LiveCommissionPlannerError("the model did not produce one valid proposal")

        try:
            proposal = proposal_box[0]
            prioritized = tuple(
                ActionType(item.value) for item in proposal.prioritized_action_types
            )
            usage = dict(result.metrics.accumulated_usage)
            evidence = {
                "runtime": "strands",
                "provider": self.provider,
                "model_id": self.model_id,
                "endpoint_scope": self.endpoint_scope,
                "external_model_calls": self.execution_profile.external_model_calls,
                "paid_model_calls": self.execution_profile.paid_model_calls,
                "authority": "priority_only",
                "input_context": "typed_priority_coarse_demand_and_action_family_counts_only",
                "stop_reason": result.stop_reason,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "cycles": result.metrics.cycle_count,
                "usage": {
                    "input_tokens": int(usage.get("inputTokens", 0)),
                    "output_tokens": int(usage.get("outputTokens", 0)),
                    "total_tokens": int(usage.get("totalTokens", 0)),
                },
                "tools": events,
                "priority": proposal.model_dump(mode="json"),
                "locked_by_server": [
                    "actor_and_owner",
                    "passport_id_revision_and_effective_fingerprint",
                    "platform_and_connection",
                    "connection_version",
                    "exact_targets_and_action_parameters",
                    "action_budget",
                    "certification_identity_expiry_and_code_revision",
                    "consent",
                    "execution",
                    "reconciliation",
                    "receipt_and_rollback",
                ],
            }
            return self.commission_service.finalize_candidate(
                candidate.commission_id,
                actor_id=actor_id,
                prioritized_action_types=prioritized,
                planner_evidence=evidence,
            )
        except Exception as exc:
            # Result decoding and deterministic sealing are part of the same
            # candidate lifecycle as model invocation. A malformed result or a
            # sealing failure must never leave a candidate stuck in planning.
            self.commission_service.planning_failed(
                candidate.commission_id, actor_id=actor_id
            )
            if isinstance(exc, LiveCommissionPlannerError):
                raise
            raise LiveCommissionPlannerError(
                f"the live proposal could not be sealed safely: {type(exc).__name__}"
            ) from exc

    @staticmethod
    async def _close_model(model: Model) -> None:
        close_model = getattr(model, "aclose", None)
        if callable(close_model):
            await close_model()
            return
        client = getattr(model, "client", None)
        close_client = getattr(client, "aclose", None)
        if callable(close_client):
            await close_client()
