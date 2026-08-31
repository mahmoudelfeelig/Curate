from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from .models import ActionOutcome, ActionReceipt, ActionStatus, ProposedAction


class ActionLedger:
    """In-memory idempotency and receipt builder used by application transactions."""

    def __init__(self) -> None:
        self._outcomes: dict[str, ActionOutcome] = {}

    def has(self, idempotency_key: str) -> bool:
        return idempotency_key in self._outcomes

    def record(self, outcome: ActionOutcome) -> ActionOutcome:
        key = outcome.action.idempotency_key
        previous = self._outcomes.get(key)
        if previous is not None:
            if previous.action != outcome.action:
                raise ValueError("an idempotency key cannot identify different actions")
            return previous
        self._outcomes[key] = outcome
        return outcome

    def mark_rolled_back(self, action: ProposedAction, rolled_back_at: datetime) -> ActionOutcome:
        previous = self._outcomes.get(action.idempotency_key)
        if previous is None:
            raise KeyError("cannot roll back an action that was not recorded")
        if previous.status is ActionStatus.ROLLED_BACK:
            return previous
        if previous.status is not ActionStatus.EXECUTED or not action.reversible:
            raise ValueError("only executed reversible actions can be rolled back")
        rolled_back = replace(
            previous,
            status=ActionStatus.ROLLED_BACK,
            before_state=previous.after_state,
            after_state=previous.before_state,
            executed_at=rolled_back_at,
        )
        self._outcomes[action.idempotency_key] = rolled_back
        return rolled_back

    def issue_receipt(
        self,
        *,
        receipt_id: str,
        passport_id: str,
        passport_version: int,
        destination_id: str,
        issued_at: datetime,
        trace_id: str,
        previous_checkpoint_id: str | None,
        rollback_caveats: tuple[str, ...] = (),
    ) -> ActionReceipt:
        outcomes = tuple(
            outcome
            for outcome in self._outcomes.values()
            if outcome.action.destination_id == destination_id
        )
        return ActionReceipt(
            id=receipt_id,
            passport_id=passport_id,
            passport_version=passport_version,
            destination_id=destination_id,
            outcomes=outcomes,
            issued_at=issued_at,
            trace_id=trace_id,
            previous_checkpoint_id=previous_checkpoint_id,
            rollback_caveats=rollback_caveats,
        )
