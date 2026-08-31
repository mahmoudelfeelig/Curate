from __future__ import annotations

from datetime import datetime, timezone
from hmac import compare_digest
from typing import Any, Mapping

from feed_passport.domain import (
    ActionType,
    AgentMissionAcceptance,
    AgentMissionBudget,
    AgentMissionStatus,
    AgentMissionStopReason,
    CapabilityLevel,
    evaluate_feed,
    total_variation_improvement,
)
from feed_passport.infrastructure.serialization import to_primitive

from .curator import CuratorApplication, InvalidStateError, NotFoundError


class AgentMissionRunner:
    """Runs an approved, bounded, deterministic curation loop on local twins.

    Goal text is retained for the operator and trace, but it never supplies an
    identity or widens authority. The Passport, previewed action families,
    thresholds, and budgets are the executable mission contract.
    """

    projection_kind = "agent_missions"

    def __init__(self, application: CuratorApplication) -> None:
        self.application = application

    def preview(
        self,
        *,
        actor_id: str,
        goal: str,
        passport_id: str,
        destination_twin: str,
        destination_account_id: str,
        budget: AgentMissionBudget,
        acceptance: AgentMissionAcceptance,
        min_improvement: float,
        allowed_action_types: frozenset[ActionType] | None = None,
        goal_interpretation: str | None = None,
        planner_evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not actor_id.strip():
            raise ValueError("actor_id is required")
        if not goal.strip() or len(goal.strip()) > 1200:
            raise ValueError("mission goal must contain between one and 1200 characters")
        if not 0.0 <= min_improvement <= 1.0:
            raise ValueError("mission minimum improvement must be between zero and one")
        self._require_local_twin(destination_twin, destination_account_id)

        passport = self.application.get_passport(passport_id)
        if passport.owner_id != actor_id:
            raise PermissionError("only the Passport owner can preview a mission")

        migration = self.application.prepare_migration(
            passport_id=passport_id,
            platform=destination_twin,
            destination_account_id=destination_account_id,
            actor_id=actor_id,
        )
        plan = dict(migration["plan"])
        compiled_actions = list(plan.get("actions", ()))
        compiled_action_types = {ActionType(str(item["action_type"])) for item in compiled_actions}
        allowed_types = compiled_action_types
        if allowed_action_types is not None:
            allowed_types = compiled_action_types & allowed_action_types
        actions = [
            item for item in compiled_actions if ActionType(str(item["action_type"])) in allowed_types
        ]
        allowed_action_names = sorted(item.value for item in allowed_types)
        now = self._now()
        mission_id = self._id("mission")
        initial_evaluation = dict(migration["before"])
        initial_status = AgentMissionStatus.AWAITING_APPROVAL
        stop_reason: AgentMissionStopReason | None = None
        if acceptance.accepts(initial_evaluation):
            initial_status = AgentMissionStatus.SUCCEEDED
            stop_reason = AgentMissionStopReason.ACCEPTANCE_REACHED
        elif not actions:
            initial_status = AgentMissionStatus.STOPPED
            stop_reason = AgentMissionStopReason.NO_ACTIONABLE_PLAN

        approval_scope = {
            "mission_id": mission_id,
            "actor_id": actor_id,
            "passport_id": passport.id,
            "passport_version": passport.version,
            "destination_twin": destination_twin,
            "destination_account_id": destination_account_id,
            "budget": to_primitive(budget),
            "acceptance_thresholds": to_primitive(acceptance),
            "min_improvement": min_improvement,
            "allowed_action_types": allowed_action_names,
            "initial_plan": {**plan, "actions": actions},
        }
        trace = [
            self._trace(0, "observe", "completed", migration_id=migration["id"]),
            self._trace(0, "evaluate", "completed", evaluation=initial_evaluation),
            self._trace(
                0,
                "plan",
                "completed",
                action_count=len(actions),
                allowed_action_types=allowed_action_names,
            ),
            self._trace(
                0,
                "policy",
                "scoped",
                total_action_budget=budget.total_actions,
                per_iteration_action_budget=budget.per_iteration_actions,
            ),
        ]
        if initial_status is AgentMissionStatus.AWAITING_APPROVAL:
            trace.append(self._trace(0, "consent", "required"))
        else:
            trace.append(self._trace(0, "stop", "completed", reason=stop_reason.value))

        projection = {
            "id": mission_id,
            "status": initial_status.value,
            "stop_reason": stop_reason.value if stop_reason else None,
            "owner_id": actor_id,
            "goal": goal.strip(),
            "goal_interpretation": goal_interpretation
            or (
                "Operator-visible mission label only; executable authority comes from the Passport, "
                "the previewed action families, thresholds, and budgets."
            ),
            "passport_id": passport.id,
            "passport_version": passport.version,
            "destination_twin": destination_twin,
            "destination_account_id": destination_account_id,
            "budget": to_primitive(budget),
            "acceptance_thresholds": to_primitive(acceptance),
            "min_improvement": min_improvement,
            "remaining_action_budget": budget.total_actions,
            "allowed_action_types": allowed_action_names,
            "approval_scope": approval_scope,
            "initial_migration_id": migration["id"],
            "current_migration_id": migration["id"],
            "current_evaluation": initial_evaluation,
            "initial_evaluation": initial_evaluation,
            "counterfactual_evaluation": (
                migration.get("preview") if len(actions) == len(compiled_actions) else None
            ),
            "pending_plan": self._plan_summary(plan, actions=actions),
            "action_envelope": actions,
            "iterations": [],
            "receipt_ids": [],
            "trace": trace,
            "planner_evidence": self._planner_evidence(
                planner_evidence,
                admitted_action_types=allowed_action_names,
            ),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "approved_at": None,
            "approved_by": None,
            "completed_at": now.isoformat() if stop_reason else None,
            "cancelled_at": None,
            "rollback": None,
        }
        self._record(
            mission_id,
            projection,
            event_type="agent_mission.previewed",
            actor_id=actor_id,
            payload={
                "status": projection["status"],
                "destination_twin": destination_twin,
                "action_count": len(actions),
                "total_action_budget": budget.total_actions,
            },
        )
        return projection

    @staticmethod
    def _planner_evidence(
        value: Mapping[str, Any] | None,
        *,
        admitted_action_types: list[str],
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        evidence = to_primitive(dict(value))
        proposal = dict(evidence.get("proposal", {}))
        requested = sorted(str(item) for item in proposal.get("requested_action_types", ()))
        admitted = sorted(str(item) for item in admitted_action_types)
        evidence["requested_action_types"] = requested
        evidence["admitted_action_types"] = admitted
        evidence["rejected_action_types"] = sorted(set(requested) - set(admitted))
        evidence["deterministic_validation"] = "passed"
        return evidence

    def get(self, mission_id: str, *, actor_id: str | None = None) -> dict[str, Any]:
        mission = self._projection(mission_id)
        if actor_id is not None and mission["owner_id"] != actor_id:
            raise PermissionError("only the mission owner can inspect it")
        return mission

    def list(self, *, actor_id: str | None = None) -> tuple[dict[str, Any], ...]:
        values = tuple(value for _, _, value in self.application.store.list_projections(self.projection_kind))
        if actor_id is None:
            return values
        return tuple(value for value in values if value.get("owner_id") == actor_id)

    def execute(self, mission_id: str, *, actor_id: str) -> dict[str, Any]:
        mission = self.get(mission_id, actor_id=actor_id)
        if mission["status"] != AgentMissionStatus.AWAITING_APPROVAL.value:
            raise InvalidStateError(f"mission cannot execute from {mission['status']}")
        passport = self.application.get_passport(str(mission["passport_id"]))
        if passport.version != int(mission["passport_version"]):
            raise InvalidStateError("Passport changed after mission preview; prepare a new mission")
        self._require_local_twin(
            str(mission["destination_twin"]),
            str(mission["destination_account_id"]),
        )

        now = self._now()
        mission["rollback_baseline"] = {
            "captured_at": now.isoformat(),
            "fingerprint": self._local_twin_state_fingerprint(
                str(mission["destination_twin"]),
                str(mission["destination_account_id"]),
            ),
        }
        mission["status"] = AgentMissionStatus.RUNNING.value
        mission["approved_by"] = actor_id
        mission["approved_at"] = now.isoformat()
        mission["updated_at"] = now.isoformat()
        mission["trace"].append(self._trace(0, "consent", "consumed", actor_id=actor_id))
        self._record(
            mission_id,
            mission,
            event_type="agent_mission.execution_started",
            actor_id=actor_id,
            payload={"remaining_action_budget": mission["remaining_action_budget"]},
        )

        budget = AgentMissionBudget(**mission["budget"])
        acceptance = AgentMissionAcceptance(**mission["acceptance_thresholds"])
        allowed_action_types = frozenset(ActionType(item) for item in mission["allowed_action_types"])
        min_improvement = float(mission["min_improvement"])

        while len(mission["iterations"]) < budget.max_iterations:
            iteration_number = len(mission["iterations"]) + 1
            migration_id = str(mission["current_migration_id"])
            migration = self.application.projection_get("migrations", migration_id)
            before = dict(migration["before"])
            plan_actions = [
                item
                for item in migration["plan"].get("actions", ())
                if ActionType(str(item["action_type"])) in allowed_action_types
            ]
            remaining = int(mission["remaining_action_budget"])
            allowance = min(budget.per_iteration_actions, remaining, len(plan_actions))

            mission["trace"].extend(
                (
                    self._trace(iteration_number, "observe", "completed", migration_id=migration_id),
                    self._trace(iteration_number, "evaluate", "completed", evaluation=before),
                    self._trace(
                        iteration_number,
                        "plan",
                        "completed",
                        proposed_action_count=len(plan_actions),
                        submitted_action_count=allowance,
                    ),
                )
            )
            if allowance < 1:
                reason = (
                    AgentMissionStopReason.BUDGET_EXHAUSTED
                    if remaining < 1
                    else AgentMissionStopReason.NO_ACTIONABLE_PLAN
                )
                return self._stop(mission, actor_id=actor_id, reason=reason)

            mission["trace"].append(
                self._trace(
                    iteration_number,
                    "policy",
                    "enforcing",
                    allowed_action_types=sorted(item.value for item in allowed_action_types),
                    submitted_action_count=allowance,
                )
            )
            started_at = self._now()
            try:
                executed = self.application.execute_migration(
                    migration_id,
                    approved_by=actor_id,
                    max_total_actions=allowance,
                    allowed_action_types=allowed_action_types,
                )
            except (KeyError, NotFoundError) as exc:
                return self._fail(
                    mission,
                    actor_id=actor_id,
                    reason=AgentMissionStopReason.CAPABILITY_UNAVAILABLE,
                    error=exc,
                )
            except (InvalidStateError, PermissionError, ValueError, RuntimeError) as exc:
                return self._fail(
                    mission,
                    actor_id=actor_id,
                    reason=AgentMissionStopReason.ADAPTER_FAILURE,
                    error=exc,
                )

            decisions = list(executed.get("decisions", ()))
            considered_count = len(decisions)
            if considered_count > allowance:
                return self._fail(
                    mission,
                    actor_id=actor_id,
                    reason=AgentMissionStopReason.POLICY_BLOCKED,
                    error=RuntimeError("migration exceeded the mission per-iteration action budget"),
                )
            receipt_id = str(executed["receipt_id"])
            mission["receipt_ids"].append(receipt_id)
            try:
                receipt = self.application.projection_get("receipts", receipt_id)
            except (KeyError, NotFoundError, ValueError) as exc:
                return self._fail(
                    mission,
                    actor_id=actor_id,
                    reason=AgentMissionStopReason.ADAPTER_FAILURE,
                    error=exc,
                )
            executed_count = sum(
                1 for outcome in receipt.get("outcomes", ()) if outcome.get("status") == "executed"
            )
            guided_count = sum(
                1
                for decision in decisions
                if decision.get("allowed") and decision.get("requires_handoff")
            )
            blocked_count = sum(1 for decision in decisions if not decision.get("allowed"))
            after = dict(executed["after"])
            improvement = total_variation_improvement(before, after)
            mission["remaining_action_budget"] = max(0, remaining - considered_count)
            mission["current_evaluation"] = after
            mission["trace"].extend(
                (
                    self._trace(
                        iteration_number,
                        "policy",
                        "completed",
                        considered_action_count=considered_count,
                        blocked_action_count=blocked_count,
                        guided_action_count=guided_count,
                    ),
                    self._trace(
                        iteration_number,
                        "act",
                        "completed",
                        executed_action_count=executed_count,
                        receipt_id=receipt_id,
                    ),
                    self._trace(
                        iteration_number,
                        "execute",
                        "completed",
                        detail=f"Applied {executed_count} policy-approved local controls.",
                        executed_action_count=executed_count,
                    ),
                    self._trace(iteration_number, "reobserve", "completed"),
                    self._trace(
                        iteration_number,
                        "evaluate",
                        "completed",
                        evaluation=after,
                        total_variation_improvement=improvement,
                    ),
                    self._trace(
                        iteration_number,
                        "receipt",
                        "issued",
                        detail=f"Recorded reversible receipt {receipt_id}.",
                        receipt_id=receipt_id,
                    ),
                )
            )
            mission["iterations"].append(
                {
                    "iteration": iteration_number,
                    "number": iteration_number,
                    "migration_id": migration_id,
                    "started_at": started_at.isoformat(),
                    "completed_at": self._now().isoformat(),
                    "proposed_action_count": len(plan_actions),
                    "submitted_action_count": considered_count,
                    "executed_action_count": executed_count,
                    "guided_action_count": guided_count,
                    "blocked_action_count": blocked_count,
                    "receipt_id": receipt_id,
                    "actions": plan_actions[:considered_count],
                    "before": before,
                    "after": after,
                    "total_variation_improvement": improvement,
                    "improvement": improvement,
                    "decision": None,
                    "remaining_action_budget": mission["remaining_action_budget"],
                }
            )

            if acceptance.accepts(after):
                return self._stop(
                    mission,
                    actor_id=actor_id,
                    reason=AgentMissionStopReason.ACCEPTANCE_REACHED,
                    succeeded=True,
                )
            if executed_count < 1:
                reason = (
                    AgentMissionStopReason.CAPABILITY_UNAVAILABLE
                    if guided_count
                    else AgentMissionStopReason.POLICY_BLOCKED
                )
                return self._stop(mission, actor_id=actor_id, reason=reason)
            if int(mission["remaining_action_budget"]) < 1:
                return self._stop(
                    mission,
                    actor_id=actor_id,
                    reason=AgentMissionStopReason.BUDGET_EXHAUSTED,
                )
            if iteration_number >= budget.max_iterations:
                return self._stop(
                    mission,
                    actor_id=actor_id,
                    reason=AgentMissionStopReason.MAX_ITERATIONS_REACHED,
                )
            if improvement < min_improvement:
                return self._stop(
                    mission,
                    actor_id=actor_id,
                    reason=AgentMissionStopReason.MINIMUM_IMPROVEMENT_NOT_MET,
                )

            try:
                next_migration = self.application.prepare_migration(
                    passport_id=str(mission["passport_id"]),
                    platform=str(mission["destination_twin"]),
                    destination_account_id=str(mission["destination_account_id"]),
                    actor_id=actor_id,
                )
            except (
                KeyError,
                NotFoundError,
                InvalidStateError,
                PermissionError,
                ValueError,
                RuntimeError,
            ) as exc:
                return self._fail(
                    mission,
                    actor_id=actor_id,
                    reason=AgentMissionStopReason.ADAPTER_FAILURE,
                    error=exc,
                )
            next_actions = [
                item
                for item in next_migration["plan"].get("actions", ())
                if ActionType(str(item["action_type"])) in allowed_action_types
            ]
            if not next_actions:
                return self._stop(
                    mission,
                    actor_id=actor_id,
                    reason=AgentMissionStopReason.NO_ACTIONABLE_PLAN,
                )
            mission["current_migration_id"] = next_migration["id"]
            mission["pending_plan"] = self._plan_summary(
                next_migration["plan"],
                actions=next_actions,
            )
            mission["counterfactual_evaluation"] = next_migration.get("preview")
            mission["updated_at"] = self._now().isoformat()
            mission["trace"].append(
                self._trace(
                    iteration_number,
                    "adapt",
                    "continuing",
                    next_migration_id=next_migration["id"],
                    next_action_count=len(next_actions),
                )
            )
            mission["iterations"][-1]["decision"] = "adapt"
            self._record(
                mission_id,
                mission,
                event_type="agent_mission.iteration_completed",
                actor_id=actor_id,
                payload={
                    "iteration": iteration_number,
                    "receipt_id": receipt_id,
                    "remaining_action_budget": mission["remaining_action_budget"],
                    "decision": "adapt",
                },
            )

        return self._stop(
            mission,
            actor_id=actor_id,
            reason=AgentMissionStopReason.MAX_ITERATIONS_REACHED,
        )

    def cancel(self, mission_id: str, *, actor_id: str) -> dict[str, Any]:
        mission = self.get(mission_id, actor_id=actor_id)
        if mission["status"] == AgentMissionStatus.CANCELLED.value:
            return mission
        if mission["status"] != AgentMissionStatus.AWAITING_APPROVAL.value:
            raise InvalidStateError("only a mission awaiting approval can be cancelled")
        now = self._now()
        mission["status"] = AgentMissionStatus.CANCELLED.value
        mission["stop_reason"] = AgentMissionStopReason.CANCELLED.value
        mission["cancelled_at"] = now.isoformat()
        mission["completed_at"] = now.isoformat()
        mission["updated_at"] = now.isoformat()
        mission["trace"].append(
            self._trace(0, "stop", "completed", reason=AgentMissionStopReason.CANCELLED.value)
        )
        self._record(
            mission_id,
            mission,
            event_type="agent_mission.cancelled",
            actor_id=actor_id,
            payload={"stop_reason": AgentMissionStopReason.CANCELLED.value},
        )
        return mission

    def rollback(self, mission_id: str, *, actor_id: str) -> dict[str, Any]:
        mission = self.get(mission_id, actor_id=actor_id)
        allowed_statuses = {
            AgentMissionStatus.SUCCEEDED.value,
            AgentMissionStatus.STOPPED.value,
            AgentMissionStatus.FAILED.value,
            AgentMissionStatus.ROLLBACK_PARTIAL.value,
        }
        if mission["status"] == AgentMissionStatus.ROLLED_BACK.value:
            return mission
        if mission["status"] not in allowed_statuses:
            raise InvalidStateError(f"mission cannot roll back from {mission['status']}")
        receipt_ids = [str(item) for item in mission.get("receipt_ids", ())]
        if not receipt_ids:
            raise InvalidStateError("mission has no executed receipts to roll back")

        results: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        mission["trace"].append(
            self._trace(0, "rollback", "started", receipt_count=len(receipt_ids))
        )
        for receipt_id in reversed(receipt_ids):
            try:
                receipt = self.application.rollback_receipt(
                    receipt_id,
                    actor_id=actor_id,
                    platform=str(mission["destination_twin"]),
                )
                results.append(
                    {
                        "receipt_id": receipt_id,
                        "status": receipt.get("status", "rolled_back"),
                        "rollback": receipt.get("rollback"),
                    }
                )
                if receipt.get("status") != "rolled_back":
                    failures.append(
                        {
                            "receipt_id": receipt_id,
                            "error": "RollbackIncomplete",
                            "detail": "one or more reversible actions were not restored",
                        }
                    )
                mission["trace"].append(
                    self._trace(0, "rollback", "completed", receipt_id=receipt_id)
                )
            except (
                KeyError,
                NotFoundError,
                InvalidStateError,
                PermissionError,
                ValueError,
                RuntimeError,
            ) as exc:
                failure = {"receipt_id": receipt_id, "error": type(exc).__name__, "detail": str(exc)}
                failures.append(failure)
                results.append({"receipt_id": receipt_id, "status": "failed", "error": failure})
                mission["trace"].append(
                    self._trace(
                        0,
                        "rollback",
                        "failed",
                        receipt_id=receipt_id,
                        error=type(exc).__name__,
                    )
                )

        verification: dict[str, Any] = {
            "status": "pending",
            "method": "local_twin_control_state_sha256",
            "state_restored": False,
            "expected_fingerprint": None,
            "observed_fingerprint": None,
            "evaluation_status": "pending",
            "evaluation": None,
        }
        try:
            baseline = mission.get("rollback_baseline")
            if not isinstance(baseline, Mapping) or not isinstance(
                baseline.get("fingerprint"), Mapping
            ):
                raise InvalidStateError("mission rollback baseline fingerprint is unavailable")
            expected_fingerprint = self._validate_local_twin_state_fingerprint(
                baseline["fingerprint"]
            )
            observed_fingerprint = self._local_twin_state_fingerprint(
                str(mission["destination_twin"]),
                str(mission["destination_account_id"]),
            )
            state_restored = compare_digest(
                str(expected_fingerprint["digest"]),
                str(observed_fingerprint["digest"]),
            )
            verification.update(
                {
                    "status": "completed" if state_restored else "mismatch",
                    "state_restored": state_restored,
                    "expected_fingerprint": expected_fingerprint,
                    "observed_fingerprint": observed_fingerprint,
                }
            )
            mission["trace"].append(
                self._trace(
                    0,
                    "verify_state",
                    "completed" if state_restored else "failed",
                    phase="rollback_verification",
                    state_restored=state_restored,
                    fingerprint_schema=expected_fingerprint["schema"],
                )
            )
            if not state_restored:
                failure = {
                    "receipt_id": "rollback-state-verification",
                    "error": "StateFingerprintMismatch",
                    "detail": (
                        "local twin control state does not match the pre-mutation fingerprint"
                    ),
                }
                failures.append(failure)
        except (KeyError, InvalidStateError, PermissionError, TypeError, ValueError, RuntimeError) as exc:
            failure = {
                "receipt_id": "rollback-state-verification",
                "error": type(exc).__name__,
                "detail": str(exc),
            }
            failures.append(failure)
            verification.update(
                {
                    "status": "failed",
                    "error": failure,
                }
            )
            mission["trace"].append(
                self._trace(
                    0,
                    "verify_state",
                    "failed",
                    phase="rollback_verification",
                    error=type(exc).__name__,
                )
            )

        try:
            verification_now = self._now()
            adapter = self.application.adapters[str(mission["destination_twin"])]
            observation = adapter.observe(
                str(mission["destination_account_id"]),
                now=verification_now,
                sample_size=24,
            )
            passport = self.application.effective_passport(str(mission["passport_id"]))
            restored_evaluation = to_primitive(evaluate_feed(passport, observation.sample))
            mission["current_evaluation"] = restored_evaluation
            verification.update(
                {
                    "observed_at": verification_now.isoformat(),
                    "evaluation_status": "completed",
                    "evaluation": restored_evaluation,
                }
            )
            mission["trace"].extend(
                (
                    self._trace(
                        0,
                        "reobserve",
                        "completed",
                        phase="rollback_verification",
                    ),
                    self._trace(
                        0,
                        "evaluate",
                        "completed",
                        phase="rollback_verification",
                        evaluation=restored_evaluation,
                    ),
                )
            )
        except (KeyError, NotFoundError, PermissionError, ValueError, RuntimeError) as exc:
            failure = {
                "receipt_id": "rollback-evaluation-verification",
                "error": type(exc).__name__,
                "detail": str(exc),
            }
            failures.append(failure)
            verification.update(
                {
                    "evaluation_status": "failed",
                    "evaluation_error": failure,
                }
            )
            mission["trace"].append(
                self._trace(
                    0,
                    "reobserve",
                    "failed",
                    phase="rollback_verification",
                    error=type(exc).__name__,
                )
            )
        now = self._now()
        mission["status"] = (
            AgentMissionStatus.ROLLBACK_PARTIAL.value
            if failures
            else AgentMissionStatus.ROLLED_BACK.value
        )
        mission["updated_at"] = now.isoformat()
        mission["rollback"] = {
            "status": "partial" if failures else "completed",
            "approved_by": actor_id,
            "completed_at": now.isoformat(),
            "receipt_order": list(reversed(receipt_ids)),
            "results": results,
            "failure_count": len(failures),
            "verification": verification,
        }
        self._record(
            mission_id,
            mission,
            event_type=(
                "agent_mission.rollback_partial"
                if failures
                else "agent_mission.rolled_back"
            ),
            actor_id=actor_id,
            payload={
                "receipt_order": list(reversed(receipt_ids)),
                "failure_count": len(failures),
            },
        )
        return mission

    def rollback_scope(self, mission_id: str) -> dict[str, Any]:
        mission = self._projection(mission_id)
        receipt_ids = [str(item) for item in mission.get("receipt_ids", ())]
        return {
            "mission_id": mission_id,
            "owner_id": mission["owner_id"],
            "destination_twin": mission["destination_twin"],
            "destination_account_id": mission["destination_account_id"],
            "rollback_baseline": mission.get("rollback_baseline"),
            "receipts": [
                self.application.projection_get("receipts", receipt_id)
                for receipt_id in receipt_ids
            ],
        }

    def _stop(
        self,
        mission: dict[str, Any],
        *,
        actor_id: str,
        reason: AgentMissionStopReason,
        succeeded: bool = False,
    ) -> dict[str, Any]:
        now = self._now()
        mission["status"] = (
            AgentMissionStatus.SUCCEEDED.value if succeeded else AgentMissionStatus.STOPPED.value
        )
        mission["stop_reason"] = reason.value
        mission["completed_at"] = now.isoformat()
        mission["updated_at"] = now.isoformat()
        mission["trace"].append(
            self._trace(
                len(mission["iterations"]),
                "adapt",
                "stopped",
                reason=reason.value,
                remaining_action_budget=mission["remaining_action_budget"],
            )
        )
        if mission["iterations"] and not mission["iterations"][-1].get("decision"):
            mission["iterations"][-1]["decision"] = reason.value
        self._record(
            str(mission["id"]),
            mission,
            event_type=(
                "agent_mission.succeeded" if succeeded else "agent_mission.stopped"
            ),
            actor_id=actor_id,
            payload={
                "stop_reason": reason.value,
                "iteration_count": len(mission["iterations"]),
                "remaining_action_budget": mission["remaining_action_budget"],
            },
        )
        return mission

    def _fail(
        self,
        mission: dict[str, Any],
        *,
        actor_id: str,
        reason: AgentMissionStopReason,
        error: Exception,
    ) -> dict[str, Any]:
        now = self._now()
        mission["status"] = AgentMissionStatus.FAILED.value
        mission["stop_reason"] = reason.value
        mission["completed_at"] = now.isoformat()
        mission["updated_at"] = now.isoformat()
        mission["error"] = {"type": type(error).__name__, "detail": str(error)}
        mission["trace"].append(
            self._trace(
                len(mission["iterations"]),
                "stop",
                "failed",
                reason=reason.value,
                error=type(error).__name__,
            )
        )
        if mission["iterations"] and not mission["iterations"][-1].get("decision"):
            mission["iterations"][-1]["decision"] = reason.value
        self._record(
            str(mission["id"]),
            mission,
            event_type="agent_mission.failed",
            actor_id=actor_id,
            payload={"stop_reason": reason.value, "error": type(error).__name__},
        )
        return mission

    def _require_local_twin(self, destination_twin: str, account_id: str) -> None:
        if not destination_twin.startswith("twin:"):
            raise ValueError("agent missions only execute against a registered local twin:<platform> adapter")
        adapter = self.application.adapters.get(destination_twin)
        if adapter is None:
            raise InvalidStateError(
                f"local twin adapter {destination_twin!r} is unavailable; "
                "register it before previewing a mission"
            )
        manifest = adapter.capabilities(account_id)
        if manifest.platform != destination_twin or manifest.level is not CapabilityLevel.LAB:
            raise InvalidStateError(
                "agent missions require a correctly registered deterministic Lab twin adapter"
            )

    def _local_twin_state_fingerprint(
        self,
        destination_twin: str,
        account_id: str,
    ) -> dict[str, str]:
        self._require_local_twin(destination_twin, account_id)
        adapter = self.application.adapters[destination_twin]
        fingerprint_method = getattr(adapter, "state_fingerprint", None)
        if not callable(fingerprint_method):
            raise InvalidStateError("local twin adapter cannot fingerprint its deterministic state")
        return self._validate_local_twin_state_fingerprint(fingerprint_method(account_id))

    @staticmethod
    def _validate_local_twin_state_fingerprint(value: Any) -> dict[str, str]:
        if not isinstance(value, Mapping):
            raise InvalidStateError("local twin state fingerprint is malformed")
        expected_keys = {"kind", "schema", "algorithm", "digest", "scope"}
        if set(value) != expected_keys:
            raise InvalidStateError("local twin state fingerprint contains unexpected fields")
        fingerprint = {key: str(value[key]) for key in sorted(expected_keys)}
        digest = fingerprint["digest"]
        if (
            fingerprint["kind"] != "local_twin_control_state"
            or fingerprint["algorithm"] != "sha256"
            or fingerprint["scope"] != "deterministic_local_twin_only"
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise InvalidStateError("local twin state fingerprint failed validation")
        return fingerprint

    def _record(
        self,
        mission_id: str,
        projection: Mapping[str, Any],
        *,
        event_type: str,
        actor_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        if isinstance(projection, dict):
            self._sync_compatibility_fields(projection)
        current = self.application.store.get_projection(self.projection_kind, mission_id)
        expected_version = current[0] if current else 0
        self.application.store.append_event(
            aggregate_id=mission_id,
            aggregate_type="agent_mission",
            expected_version=expected_version,
            event_type=event_type,
            payload=payload,
            actor_id=actor_id,
            trace_id=f"mission-trace:{mission_id}",
            occurred_at=self._now(),
            projection_kind=self.projection_kind,
            projection=projection,
        )

    def _projection(self, mission_id: str) -> dict[str, Any]:
        value = self.application.store.get_projection(self.projection_kind, mission_id)
        if value is None:
            raise NotFoundError(f"{self.projection_kind}:{mission_id}")
        return dict(value[1])

    def _now(self) -> datetime:
        value = self.application.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("mission clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    def _id(self, prefix: str) -> str:
        return f"{prefix}-{self.application.id_factory()}"

    def _trace(self, iteration: int, stage: str, status: str, **detail: Any) -> dict[str, Any]:
        human_detail = str(detail.pop("detail", self._trace_detail(stage, status)))
        return {
            "iteration": iteration,
            "stage": stage,
            "status": status,
            "at": self._now().isoformat(),
            "detail": human_detail,
            **to_primitive(detail),
        }

    @staticmethod
    def _plan_summary(
        plan: Mapping[str, Any],
        *,
        actions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        action_values = list(plan.get("actions", ())) if actions is None else actions
        return {
            "id": plan["id"],
            "action_count": len(action_values),
            "action_types": sorted({str(item["action_type"]) for item in action_values}),
            "loss_count": len(plan.get("losses", ())),
            "estimated_topic_distance": plan.get("estimated_topic_distance"),
        }

    @staticmethod
    def _trace_detail(stage: str, status: str) -> str:
        values = {
            "observe": "Observed the deterministic seeded destination without any account access.",
            "evaluate": "Measured the fixture feed against the explicit acceptance thresholds.",
            "plan": "Compiled only controls declared by this local platform twin.",
            "policy": "Checked identity, capability, action families, budgets, and reversibility.",
            "consent": "Paused at the one-time human approval boundary.",
            "act": "Applied only controls admitted by deterministic policy.",
            "execute": "Applied only controls admitted by deterministic policy.",
            "reobserve": "Sampled the deterministic destination again after the bounded pass.",
            "adapt": "Compared progress with stop conditions before choosing another pass.",
            "receipt": "Persisted the inverse operations required for local rollback.",
            "rollback": "Restored mission receipts in reverse execution order.",
            "stop": "Stopped at an explicit mission boundary.",
        }
        return values.get(stage, f"{stage.replace('_', ' ').title()} is {status.replace('_', ' ')}.")

    @staticmethod
    def _sync_compatibility_fields(mission: dict[str, Any]) -> None:
        budget = dict(mission["budget"])
        thresholds = dict(mission["acceptance_thresholds"])
        mission["environment"] = "local_platform_control_twin"
        mission["platform"] = mission["destination_twin"]
        mission["account_id"] = mission["destination_account_id"]
        mission["max_iterations"] = budget["max_iterations"]
        mission["max_total_actions"] = budget["total_actions"]
        mission["max_actions_per_iteration"] = budget["per_iteration_actions"]
        mission["remaining_actions"] = mission["remaining_action_budget"]
        mission["acceptance"] = {
            "max_topic_distance": thresholds["max_total_variation_distance"],
            "max_unwanted_rate": thresholds["max_unwanted_rate"],
            "max_source_concentration": thresholds["max_source_concentration"],
            "min_serendipity": thresholds["min_serendipity_rate"],
            "max_serendipity": thresholds["max_serendipity_rate"],
        }
        mission["before"] = mission["initial_evaluation"]
        mission["counterfactual"] = mission.get("counterfactual_evaluation")
        mission["after"] = (
            mission.get("current_evaluation") if mission.get("iterations") else None
        )
        terminal = {
            AgentMissionStatus.SUCCEEDED.value,
            AgentMissionStatus.STOPPED.value,
            AgentMissionStatus.FAILED.value,
            AgentMissionStatus.ROLLBACK_PARTIAL.value,
        }
        mission["rollback_available"] = bool(mission.get("receipt_ids")) and mission.get(
            "status"
        ) in terminal
        if mission.get("status") == AgentMissionStatus.ROLLED_BACK.value:
            mission["rollback_available"] = False
        mission["fidelity_disclaimer"] = (
            "Deterministic local control-surface simulation. It proves agent orchestration, "
            "policy enforcement, measurement, and rollback; it does not reproduce this "
            "platform's private ranking system."
        )
