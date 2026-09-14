from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import json
from typing import Any, Protocol

from feed_passport.domain.models import (
    ActionOutcome,
    ActionReceipt,
    ActionType,
    PlatformCapabilityManifest,
    ProposedAction,
    RollbackOutcome,
)

from .credentials import ConnectedAccount


PUBLIC_ENGAGEMENT_ACTIONS = frozenset(
    {
        ActionType.LIKE,
        ActionType.COMMENT,
        ActionType.POST,
        ActionType.REPOST,
        ActionType.SEND_MESSAGE,
    }
)


class LivePlatformError(RuntimeError):
    """Sanitized error crossing the external-platform trust boundary."""

    def __init__(
        self,
        *,
        platform: str,
        code: str,
        detail: str,
        retryable: bool = False,
        outcome_unknown: bool = False,
        http_status: int | None = None,
    ) -> None:
        self.platform = platform
        self.code = code
        self.retryable = retryable
        self.outcome_unknown = outcome_unknown
        self.http_status = http_status
        super().__init__(detail)


class LiveAuthenticationError(LivePlatformError):
    pass


class LivePermissionError(LivePlatformError):
    pass


class LiveTargetNotFound(LivePlatformError):
    pass


class LiveRateLimited(LivePlatformError):
    def __init__(self, *, retry_after_seconds: int | None = None, **kwargs: Any) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(**kwargs)


class LiveTransientError(LivePlatformError):
    pass


class LiveProtocolError(LivePlatformError):
    pass


class RemoteOutcomeUnknown(LivePlatformError):
    pass


@dataclass(frozen=True, slots=True)
class ValidatedLiveCertification:
    """Capability subset created only after receipt/evidence validation upstream."""

    platform: str
    certified_at: datetime
    expires_at: datetime
    code_revision: str
    execute: frozenset[ActionType]
    observe: frozenset[str]
    verify: frozenset[str]
    rollback: frozenset[ActionType]
    receipt_ref: str
    evidence_sha256: str
    environment: str = "authorized_live"
    result: str = "passed"

    def __post_init__(self) -> None:
        object.__setattr__(self, "execute", frozenset(self.execute))
        object.__setattr__(self, "observe", frozenset(self.observe))
        object.__setattr__(self, "verify", frozenset(self.verify))
        object.__setattr__(self, "rollback", frozenset(self.rollback))
        if self.certified_at.tzinfo is None or self.certified_at.utcoffset() is None:
            raise ValueError("live certification time must be timezone-aware")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("live certification expiry must be timezone-aware")
        if self.expires_at <= self.certified_at:
            raise ValueError("live certification expiry must follow its certification time")
        if len(self.code_revision) != 40 or any(
            character not in "0123456789abcdef" for character in self.code_revision
        ):
            raise ValueError("live certification requires an exact lowercase Git revision")
        if self.environment != "authorized_live" or self.result != "passed":
            raise ValueError("live certification must be a passed authorized-live result")
        if not self.platform.strip() or not self.receipt_ref.strip():
            raise ValueError("live certification platform and receipt are required")
        if len(self.evidence_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.evidence_sha256
        ):
            raise ValueError("live certification requires a lowercase SHA-256 evidence digest")
        if not self.execute:
            raise ValueError("live certification must cover an explicit executable action subset")
        if self.execute & PUBLIC_ENGAGEMENT_ACTIONS:
            raise ValueError("live certification cannot include public engagement")
        if not self.rollback <= self.execute:
            raise ValueError("certified rollback actions must also be certified executable")


@dataclass(frozen=True, slots=True)
class PreparedRemoteAction:
    """Secret-free record that must be persisted before a remote mutation."""

    platform: str
    connection_id: str
    action: ProposedAction
    before_state: Mapping[str, Any]
    desired_state: Mapping[str, Any]
    prepared_at: datetime
    idempotency_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if self.action.destination_id != self.connection_id:
            raise ValueError("prepared action destination must match its connection")
        if self.prepared_at.tzinfo is None or self.prepared_at.utcoffset() is None:
            raise ValueError("prepared action time must be timezone-aware")
        before = dict(self.before_state)
        desired = dict(self.desired_state)
        object.__setattr__(self, "before_state", before)
        object.__setattr__(self, "desired_state", desired)
        canonical = json.dumps(
            {
                "platform": self.platform,
                "connection_id": self.connection_id,
                "action_id": self.action.id,
                "idempotency_key": self.action.idempotency_key,
                "before_state": before,
                "desired_state": desired,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        object.__setattr__(self, "idempotency_fingerprint", sha256(canonical).hexdigest())


class LivePlatformAdapter(Protocol):
    platform: str

    def capabilities(self, account_id: str) -> PlatformCapabilityManifest: ...

    def prepare_remote_action(
        self,
        account_id: str,
        action: ProposedAction,
        *,
        now: datetime,
    ) -> PreparedRemoteAction: ...

    def apply_prepared_action(
        self,
        prepared: PreparedRemoteAction,
        *,
        now: datetime,
    ) -> ActionOutcome: ...

    def reconcile_remote_action(
        self,
        prepared: PreparedRemoteAction,
        *,
        now: datetime,
    ) -> ActionOutcome: ...

    def rollback(
        self,
        account_id: str,
        receipt: ActionReceipt,
        *,
        now: datetime,
    ) -> RollbackOutcome: ...


class HttpResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]
    text: str

    def json(self) -> Any: ...


class HttpClient(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        data: Mapping[str, Any] | None = None,
    ) -> HttpResponse: ...


def require_active_connection(connection: ConnectedAccount, *, platform: str) -> None:
    if connection.platform != platform:
        raise ValueError("connected account belongs to a different platform")
    if connection.status != "active":
        raise LiveAuthenticationError(
            platform=platform,
            code="connection_inactive",
            detail="The connected account is not active.",
        )
    if not connection.id.strip() or not connection.external_subject_id.strip():
        raise ValueError("connected account identifiers are required")
    if not connection.credential_ref.strip():
        raise ValueError("connected account credential reference is required")
