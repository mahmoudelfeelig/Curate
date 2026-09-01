from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from feed_passport.domain.connections import ExternalConnection

from .credentials import CredentialProvider


class OAuthClientAuthentication(StrEnum):
    NONE = "none"
    CLIENT_SECRET_BASIC = "client_secret_basic"
    CLIENT_SECRET_POST = "client_secret_post"


@dataclass(frozen=True, slots=True, repr=False)
class OAuthProviderConfig:
    """Server-owned OAuth client registration and provider endpoints.

    The object is intentionally non-representable because it may carry a
    client secret. It must never be included in API responses or model input.
    """

    platform: str
    client_id: str
    authorize_url: str
    token_url: str
    scopes: frozenset[str]
    allowed_redirect_uris: frozenset[str]
    identity_url: str
    identity_kind: str
    client_authentication: OAuthClientAuthentication
    client_secret: str | None = field(default=None, repr=False)
    revoke_url: str | None = None
    use_pkce: bool = True
    extra_authorize_params: Mapping[str, str] = field(default_factory=dict)
    resource_server: str | None = None
    user_agent: str | None = None

    def __post_init__(self) -> None:
        required = (
            self.platform,
            self.client_id,
            self.authorize_url,
            self.token_url,
            self.identity_url,
            self.identity_kind,
        )
        if not all(value.strip() for value in required):
            raise ValueError("OAuth provider identifiers and endpoints are required")
        for endpoint in (
            self.authorize_url,
            self.token_url,
            self.identity_url,
            self.revoke_url,
            self.resource_server,
        ):
            if endpoint is not None and not endpoint.startswith("https://"):
                raise ValueError("OAuth provider and resource endpoints must use HTTPS")
        if not self.scopes or not self.allowed_redirect_uris:
            raise ValueError("OAuth scopes and an exact redirect allowlist are required")
        for redirect_uri in self.allowed_redirect_uris:
            if not (
                redirect_uri.startswith("https://")
                or redirect_uri.startswith("http://127.0.0.1:")
                or redirect_uri.startswith("http://localhost:")
            ):
                raise ValueError("OAuth redirects require HTTPS except loopback development URLs")
        if self.client_authentication is not OAuthClientAuthentication.NONE and not self.client_secret:
            raise ValueError("confidential OAuth clients require a client secret")
        if self.user_agent is not None:
            normalized_user_agent = self.user_agent.strip()
            if not 10 <= len(normalized_user_agent) <= 256 or any(
                ord(character) < 32 or ord(character) > 126
                for character in normalized_user_agent
            ):
                raise ValueError("OAuth provider user agents must be 10-256 printable ASCII characters")
            object.__setattr__(self, "user_agent", normalized_user_agent)
        object.__setattr__(self, "scopes", frozenset(self.scopes))
        object.__setattr__(self, "allowed_redirect_uris", frozenset(self.allowed_redirect_uris))
        object.__setattr__(
            self,
            "extra_authorize_params",
            MappingProxyType(dict(self.extra_authorize_params)),
        )

    def __repr__(self) -> str:
        return f"OAuthProviderConfig(platform={self.platform!r}, client_secret=<redacted>)"


class OAuthProviderRegistry(Protocol):
    def get(self, platform: str) -> OAuthProviderConfig: ...


class OAuthCredentialVault(CredentialProvider, Protocol):
    """Credential broker used by OAuth orchestration and live transports.

    Implementations own bearer and refresh tokens. Callers receive only an
    opaque reference or a short-lived, non-serializable credential lease.
    """

    def put(
        self,
        *,
        owner_id: str,
        platform: str,
        token_response: Mapping[str, Any],
        granted_scopes: frozenset[str],
        now: datetime,
        resource_server: str | None = None,
    ) -> str: ...

    def revoke(
        self,
        connection: ExternalConnection,
        *,
        now: datetime,
    ) -> None: ...

    def discard(self, credential_ref: str, *, owner_id: str) -> None: ...

    def retry_pending_retirements(
        self,
        *,
        now: datetime,
        owner_id: str | None = None,
        platform: str | None = None,
        connection_id: str | None = None,
        limit: int = 100,
    ) -> int: ...
