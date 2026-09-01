from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping, Protocol


class RemoteActionState(StrEnum):
    PENDING = "pending"
    DISPATCHING = "dispatching"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_FINAL = "failed_final"
    UNKNOWN = "unknown"
    RECONCILING = "reconciling"
    ROLLBACK_PENDING = "rollback_pending"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_UNKNOWN = "rollback_unknown"
    NEEDS_HUMAN = "needs_human"


_ALLOWED_TRANSITIONS: Mapping[RemoteActionState, frozenset[RemoteActionState]] = {
    RemoteActionState.PENDING: frozenset(
        {
            RemoteActionState.DISPATCHING,
            RemoteActionState.FAILED_FINAL,
            RemoteActionState.NEEDS_HUMAN,
        }
    ),
    RemoteActionState.DISPATCHING: frozenset(
        {
            RemoteActionState.SUCCEEDED,
            RemoteActionState.FAILED_RETRYABLE,
            RemoteActionState.FAILED_FINAL,
            RemoteActionState.UNKNOWN,
            RemoteActionState.NEEDS_HUMAN,
        }
    ),
    RemoteActionState.FAILED_RETRYABLE: frozenset(
        {RemoteActionState.DISPATCHING, RemoteActionState.NEEDS_HUMAN}
    ),
    RemoteActionState.UNKNOWN: frozenset(
        {RemoteActionState.RECONCILING, RemoteActionState.NEEDS_HUMAN}
    ),
    RemoteActionState.RECONCILING: frozenset(
        {
            RemoteActionState.SUCCEEDED,
            RemoteActionState.FAILED_RETRYABLE,
            RemoteActionState.FAILED_FINAL,
            RemoteActionState.UNKNOWN,
            RemoteActionState.NEEDS_HUMAN,
        }
    ),
    RemoteActionState.SUCCEEDED: frozenset(
        {RemoteActionState.ROLLBACK_PENDING, RemoteActionState.NEEDS_HUMAN}
    ),
    RemoteActionState.ROLLBACK_PENDING: frozenset(
        {
            RemoteActionState.ROLLED_BACK,
            RemoteActionState.ROLLBACK_UNKNOWN,
            RemoteActionState.NEEDS_HUMAN,
        }
    ),
    RemoteActionState.ROLLBACK_UNKNOWN: frozenset(
        {
            RemoteActionState.ROLLBACK_PENDING,
            RemoteActionState.ROLLED_BACK,
            RemoteActionState.NEEDS_HUMAN,
        }
    ),
    RemoteActionState.FAILED_FINAL: frozenset(),
    RemoteActionState.ROLLED_BACK: frozenset(),
    RemoteActionState.NEEDS_HUMAN: frozenset(),
}


def validate_remote_action_transition(
    current: RemoteActionState,
    target: RemoteActionState,
) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"remote action cannot transition from {current.value} to {target.value}")


@dataclass(frozen=True, slots=True)
class RemoteActionAttempt:
    id: str
    owner_id: str
    connection_id: str
    platform: str
    migration_id: str
    action_id: str
    operation: str
    idempotency_key: str
    request_fingerprint: str
    state: RemoteActionState
    action_payload: Mapping[str, Any] = field(default_factory=dict)
    attempt_count: int = 0
    platform_reference: str | None = None
    before_state: Mapping[str, Any] | None = None
    after_state: Mapping[str, Any] | None = None
    error_code: str | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    next_attempt_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        required = (
            self.id,
            self.owner_id,
            self.connection_id,
            self.platform,
            self.migration_id,
            self.action_id,
            self.operation,
            self.idempotency_key,
            self.request_fingerprint,
        )
        if not all(value.strip() for value in required):
            raise ValueError("remote action identifiers and request fingerprint are required")
        if self.attempt_count < 0:
            raise ValueError("remote action attempt count cannot be negative")
        for field_name in ("lease_expires_at", "next_attempt_at", "created_at", "updated_at"):
            value = getattr(self, field_name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{field_name} must be timezone-aware")
        object.__setattr__(self, "action_payload", MappingProxyType(dict(self.action_payload)))
        if self.before_state is not None:
            object.__setattr__(self, "before_state", MappingProxyType(dict(self.before_state)))
        if self.after_state is not None:
            object.__setattr__(self, "after_state", MappingProxyType(dict(self.after_state)))


class ActionJournal(Protocol):
    def reserve_remote_action(
        self,
        *,
        attempt_id: str,
        owner_id: str,
        connection_id: str,
        platform: str,
        migration_id: str,
        action_id: str,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
        action_payload: Mapping[str, Any],
        created_at: datetime,
    ) -> RemoteActionAttempt: ...

    def get_remote_action(self, attempt_id: str, *, owner_id: str | None = None) -> RemoteActionAttempt: ...

    def transition_remote_action(
        self,
        attempt_id: str,
        *,
        expected_state: RemoteActionState,
        new_state: RemoteActionState,
        updated_at: datetime,
        lease_owner: str | None = None,
        lease_expires_at: datetime | None = None,
        next_attempt_at: datetime | None = None,
        platform_reference: str | None = None,
        before_state: Mapping[str, Any] | None = None,
        after_state: Mapping[str, Any] | None = None,
        error_code: str | None = None,
    ) -> RemoteActionAttempt: ...

    def list_remote_actions(
        self,
        *,
        owner_id: str | None = None,
        migration_id: str | None = None,
        connection_id: str | None = None,
    ) -> tuple[RemoteActionAttempt, ...]: ...

    def list_recoverable_remote_actions(
        self,
        now: datetime,
        *,
        limit: int = 100,
    ) -> tuple[RemoteActionAttempt, ...]: ...
