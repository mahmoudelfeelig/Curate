from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class AgentMissionStatus(StrEnum):
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    SUCCEEDED = "completed"
    STOPPED = "needs_human"
    CANCELLED = "cancelled"
    FAILED = "failed_recoverable"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_PARTIAL = "rollback_partial"


class AgentMissionStopReason(StrEnum):
    ACCEPTANCE_REACHED = "acceptance_reached"
    BUDGET_EXHAUSTED = "budget_exhausted"
    MAX_ITERATIONS_REACHED = "max_iterations_reached"
    MINIMUM_IMPROVEMENT_NOT_MET = "minimum_improvement_not_met"
    NO_ACTIONABLE_PLAN = "no_actionable_plan"
    POLICY_BLOCKED = "policy_blocked"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    ADAPTER_FAILURE = "adapter_failure"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class AgentMissionBudget:
    total_actions: int
    per_iteration_actions: int
    max_iterations: int

    def __post_init__(self) -> None:
        if not 1 <= self.total_actions <= 100:
            raise ValueError("mission total action budget must be between one and one hundred")
        if not 1 <= self.per_iteration_actions <= self.total_actions:
            raise ValueError(
                "mission per-iteration budget must be positive and no larger than the total budget"
            )
        if not 1 <= self.max_iterations <= 20:
            raise ValueError("mission max iterations must be between one and twenty")


@dataclass(frozen=True, slots=True)
class AgentMissionAcceptance:
    max_total_variation_distance: float = 0.18
    max_unwanted_rate: float = 0.05
    max_source_concentration: float = 0.40
    min_serendipity_rate: float = 0.05
    max_serendipity_rate: float = 1.0

    def __post_init__(self) -> None:
        for name in (
            "max_total_variation_distance",
            "max_unwanted_rate",
            "max_source_concentration",
            "min_serendipity_rate",
            "max_serendipity_rate",
        ):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between zero and one")
        if self.min_serendipity_rate > self.max_serendipity_rate:
            raise ValueError("minimum serendipity cannot exceed maximum serendipity")

    def accepts(self, evaluation: Mapping[str, Any]) -> bool:
        return (
            float(evaluation["total_variation_distance"]) <= self.max_total_variation_distance
            and float(evaluation["unwanted_rate"]) <= self.max_unwanted_rate
            and float(evaluation["source_concentration"]) <= self.max_source_concentration
            and float(evaluation["serendipity_rate"]) >= self.min_serendipity_rate
            and float(evaluation["serendipity_rate"]) <= self.max_serendipity_rate
        )


def total_variation_improvement(before: Mapping[str, Any], after: Mapping[str, Any]) -> float:
    """Positive when the destination sample moved toward the Passport topic target."""

    return float(before["total_variation_distance"]) - float(after["total_variation_distance"])
