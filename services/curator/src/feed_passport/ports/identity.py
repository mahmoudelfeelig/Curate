from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping, Protocol


class PrincipalKind(StrEnum):
    USER = "user"
    SERVICE = "service"


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Identity proven by a trusted boundary, never by request body fields."""

    subject: str
    issuer: str
    authenticated_at: datetime
    expires_at: datetime | None = None
    kind: PrincipalKind = PrincipalKind.USER
    tenant_id: str | None = None
    claims: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.subject.strip() or not self.issuer.strip():
            raise ValueError("principal subject and issuer are required")
        if self.authenticated_at.tzinfo is None or self.authenticated_at.utcoffset() is None:
            raise ValueError("authenticated_at must be timezone-aware")
        if self.expires_at is not None:
            if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
                raise ValueError("expires_at must be timezone-aware")
            if self.expires_at <= self.authenticated_at:
                raise ValueError("principal expiry must be after authentication")
        object.__setattr__(self, "claims", MappingProxyType(dict(self.claims)))

    @property
    def actor_id(self) -> str:
        return self.subject

    def require_user(self) -> None:
        if self.kind is not PrincipalKind.USER:
            raise PermissionError("this operation requires an authenticated user principal")


class BearerTokenVerifier(Protocol):
    """Verifies a bearer token and returns a server-derived principal."""

    def verify(self, token: str, *, now: datetime) -> AuthenticatedPrincipal: ...
