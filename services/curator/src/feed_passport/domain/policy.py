from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .models import ActionEnvelope, ActionStatus, ActionType, PlatformCapabilityManifest, ProposedAction


PUBLIC_ENGAGEMENT_ACTIONS = frozenset(
    {
        ActionType.LIKE,
        ActionType.COMMENT,
        ActionType.POST,
        ActionType.REPOST,
        ActionType.SEND_MESSAGE,
    }
)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    code: str
    explanation: str
    requires_handoff: bool = False


class PolicyGuard:
    def __init__(self, forbidden_actions: frozenset[ActionType] = PUBLIC_ENGAGEMENT_ACTIONS) -> None:
        self._forbidden_actions = forbidden_actions

    def check(
        self,
        *,
        action: ProposedAction,
        envelope: ActionEnvelope,
        capability: PlatformCapabilityManifest,
        prior_actions: Iterable[tuple[ActionType, ActionStatus]],
        now: datetime,
    ) -> PolicyDecision:
        if now.tzinfo is None or now.utcoffset() is None:
            return PolicyDecision(False, "invalid_clock", "Policy checks require a timezone-aware clock.")
        if now >= envelope.expires_at:
            return PolicyDecision(False, "approval_expired", "The approved action envelope has expired.")
        if action.destination_id not in envelope.destination_ids:
            return PolicyDecision(False, "destination_not_approved", "The destination is outside the approved scope.")
        if action.action_type in self._forbidden_actions:
            return PolicyDecision(False, "public_engagement_forbidden", "Public engagement automation is prohibited.")
        if action.action_type not in envelope.allowed_actions:
            return PolicyDecision(False, "action_not_approved", "The action type is outside the approved scope.")

        completed = [
            action_type
            for action_type, status in prior_actions
            if status in {ActionStatus.EXECUTED, ActionStatus.GUIDED}
        ]
        if len(completed) >= envelope.max_total_actions:
            return PolicyDecision(False, "total_budget_exhausted", "The total approved action budget is exhausted.")
        counts = Counter(completed)
        action_limit = envelope.max_per_type.get(action.action_type, envelope.max_total_actions)
        if counts[action.action_type] >= action_limit:
            return PolicyDecision(False, "type_budget_exhausted", "The approved budget for this action type is exhausted.")

        if action.action_type in capability.requires_user_handoff:
            return PolicyDecision(
                True,
                "guided_handoff",
                "The action is approved but must be completed in the platform's native interface.",
                requires_handoff=True,
            )
        if action.action_type not in capability.execute:
            return PolicyDecision(False, "capability_unavailable", "The platform cannot execute this action through an authorized interface.")
        if action.reversible and action.action_type not in capability.rollback:
            return PolicyDecision(False, "rollback_mismatch", "The action was marked reversible but the adapter cannot roll it back.")

        return PolicyDecision(True, "allowed", "The action is within the approved and certified scope.")
