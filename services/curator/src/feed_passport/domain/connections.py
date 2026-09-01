from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


class ConnectionStatus(StrEnum):
    ACTIVE = "active"
    REAUTH_REQUIRED = "reauth_required"
    REVOKING = "revoking"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class ExternalConnection:
    """Owner-bound pointer to an external account and its credential provider.

    Access and refresh tokens never belong in this object. ``credential_ref`` is
    an opaque reference to a separately protected provider such as AgentCore
    Identity; metadata contains only encrypted-at-rest, non-token connection
    details after the repository has authorized the owner.
    """

    id: str
    owner_id: str
    platform: str
    status: ConnectionStatus
    external_subject: str
    credential_ref: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.id,
                self.owner_id,
                self.platform,
                self.external_subject,
                self.credential_ref,
            )
        ):
            raise ValueError(
                "connection identifiers, owner, platform, subject, and credential reference are required"
            )
        if self.version < 1:
            raise ValueError("connection version must be positive")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        if self.revoked_at is not None:
            _require_aware(self.revoked_at, "revoked_at")
        if self.status is ConnectionStatus.REVOKED and self.revoked_at is None:
            raise ValueError("a revoked connection requires revoked_at")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    @property
    def external_subject_id(self) -> str:
        """Compatibility name used by live transports."""

        return self.external_subject

    @property
    def granted_scopes(self) -> frozenset[str]:
        """Normalized OAuth scopes kept inside encrypted connection metadata."""

        raw = self.metadata.get("granted_scopes", self.metadata.get("scopes", ()))
        if not isinstance(raw, (list, tuple, set, frozenset)):
            return frozenset()
        return frozenset(str(value) for value in raw if str(value).strip())


@dataclass(frozen=True, slots=True)
class OAuthTransactionStart:
    """One-time browser value returned only when an OAuth transaction starts."""

    state: str
    owner_id: str
    platform: str
    redirect_uri: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.state, self.owner_id, self.platform, self.redirect_uri)):
            raise ValueError("OAuth state, owner, platform, and redirect URI are required")
        _require_aware(self.expires_at, "expires_at")


@dataclass(frozen=True, slots=True)
class OAuthTransaction:
    """Consumed, principal-bound OAuth transaction with decrypted PKCE metadata."""

    owner_id: str
    platform: str
    redirect_uri: str
    metadata: Mapping[str, Any]
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.owner_id, self.platform, self.redirect_uri)):
            raise ValueError("OAuth owner, platform, and redirect URI are required")
        for field_name in ("created_at", "expires_at", "consumed_at"):
            _require_aware(getattr(self, field_name), field_name)
        if self.expires_at <= self.created_at:
            raise ValueError("OAuth transaction expiry must be after creation")
        if self.consumed_at < self.created_at:
            raise ValueError("OAuth transaction cannot be consumed before creation")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


# Live transports use this more explicit name; both names identify the same
# owner-authorized, token-free connection record.
AuthorizedConnection = ExternalConnection
