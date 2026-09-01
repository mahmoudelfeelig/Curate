from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import jwt
from jwt import PyJWKClient

from feed_passport.ports.identity import AuthenticatedPrincipal, BearerTokenVerifier, PrincipalKind


class AuthenticationError(RuntimeError):
    """A request could not be bound to an authenticated application principal."""


class AuthorizationError(RuntimeError):
    """An authenticated request attempted to claim a different principal."""


@dataclass(frozen=True, slots=True)
class OIDCBearerTokenVerifier:
    """Validate an OIDC bearer token against an exact issuer, client and JWKS endpoint.

    The verifier accepts either an ``aud`` value or an OAuth access-token ``client_id``
    claim because Cognito access tokens commonly use the latter. Signature, issuer and
    expiry validation still happen before either claim is trusted.
    """

    issuer: str
    audience: str
    jwks_url: str
    algorithms: tuple[str, ...] = ("RS256", "ES256")
    required_scopes: frozenset[str] = frozenset({"feed-passport/invoke"})
    leeway_seconds: int = 30
    _jwks_client: PyJWKClient | None = None

    def __post_init__(self) -> None:
        if not self.issuer.startswith("https://"):
            raise ValueError("OIDC issuer must use HTTPS")
        if not self.jwks_url.startswith("https://"):
            raise ValueError("OIDC JWKS URL must use HTTPS")
        if not self.audience.strip():
            raise ValueError("OIDC audience or client id is required")
        if not self.algorithms or not set(self.algorithms) <= {"RS256", "ES256"}:
            raise ValueError("OIDC algorithms must be an explicit RS256/ES256 allowlist")
        if not self.required_scopes or any(not value.strip() for value in self.required_scopes):
            raise ValueError("OIDC requires an explicit non-empty scope allowlist")
        if not 0 <= self.leeway_seconds <= 120:
            raise ValueError("OIDC clock leeway must be between zero and 120 seconds")
        if self._jwks_client is None:
            object.__setattr__(
                self,
                "_jwks_client",
                PyJWKClient(self.jwks_url, cache_keys=True, lifespan=300),
            )
        object.__setattr__(self, "required_scopes", frozenset(self.required_scopes))

    @classmethod
    def from_env(cls) -> OIDCBearerTokenVerifier:
        issuer = os.getenv("FEED_PASSPORT_OIDC_ISSUER", "").strip().rstrip("/")
        audience = os.getenv("FEED_PASSPORT_OIDC_AUDIENCE", "").strip()
        jwks_url = os.getenv("FEED_PASSPORT_OIDC_JWKS_URL", "").strip()
        required_scopes = frozenset(
            value
            for value in os.getenv(
                "FEED_PASSPORT_OIDC_REQUIRED_SCOPES",
                "feed-passport/invoke",
            ).split()
            if value
        )
        if not issuer or not audience or not jwks_url:
            raise ValueError(
                "OIDC mode requires FEED_PASSPORT_OIDC_ISSUER, "
                "FEED_PASSPORT_OIDC_AUDIENCE, and FEED_PASSPORT_OIDC_JWKS_URL"
            )
        return cls(
            issuer=issuer,
            audience=audience,
            jwks_url=jwks_url,
            required_scopes=required_scopes,
        )

    def verify(self, token: str, *, now: datetime) -> AuthenticatedPrincipal:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("authentication time must be timezone-aware")
        if not 20 <= len(token) <= 16_384 or any(character.isspace() for character in token):
            raise AuthenticationError("bearer token has an invalid shape")
        try:
            assert self._jwks_client is not None
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(self.algorithms),
                issuer=self.issuer,
                options={
                    "require": ["sub", "iss", "exp"],
                    "verify_aud": False,
                },
                leeway=self.leeway_seconds,
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError("bearer token validation failed") from exc
        except Exception as exc:
            raise AuthenticationError("OIDC signing keys are unavailable") from exc

        subject = str(claims.get("sub", "")).strip()
        issuer = str(claims.get("iss", "")).strip().rstrip("/")
        if not subject or issuer != self.issuer:
            raise AuthenticationError("bearer token principal is invalid")
        audience_claim = claims.get("aud")
        audiences = (
            {str(item) for item in audience_claim}
            if isinstance(audience_claim, list)
            else {str(audience_claim)} if audience_claim is not None else set()
        )
        client_id = str(claims.get("client_id", ""))
        if self.audience not in audiences and client_id != self.audience:
            raise AuthenticationError("bearer token was issued for a different client")

        token_use = claims.get("token_use")
        if token_use is not None and token_use != "access":
            raise AuthenticationError("an OAuth access token is required")
        raw_scopes = claims.get("scope", claims.get("scp", ()))
        if isinstance(raw_scopes, str):
            granted_scopes = frozenset(value for value in raw_scopes.split() if value)
        elif isinstance(raw_scopes, (list, tuple, set, frozenset)):
            granted_scopes = frozenset(str(value) for value in raw_scopes if str(value).strip())
        else:
            granted_scopes = frozenset()
        if not self.required_scopes <= granted_scopes:
            raise AuthenticationError("bearer token is missing the required application scope")

        expires_at = _claim_datetime(claims["exp"], "exp")
        authenticated_at_value = claims.get("auth_time", claims.get("iat"))
        authenticated_at = (
            _claim_datetime(authenticated_at_value, "auth_time")
            if authenticated_at_value is not None
            else now
        )
        if expires_at <= now:
            raise AuthenticationError("bearer token has expired")
        tenant_id = claims.get("tenant_id") or claims.get("custom:tenant_id")
        return AuthenticatedPrincipal(
            subject=subject,
            issuer=issuer,
            authenticated_at=authenticated_at,
            expires_at=expires_at,
            kind=PrincipalKind.USER,
            tenant_id=str(tenant_id) if tenant_id else None,
            claims=_safe_claims(claims),
        )


def parse_bearer_header(value: str | None) -> str:
    if value is None:
        raise AuthenticationError("a bearer token is required")
    scheme, separator, token = value.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError("Authorization must contain one bearer token")
    if " " in token.strip():
        raise AuthenticationError("Authorization must contain one bearer token")
    return token.strip()


def require_claimed_actor(
    principal: AuthenticatedPrincipal,
    payload: Any,
    *,
    query_actor_id: str | None = None,
) -> None:
    """Reject body/query actor assertions that do not match the verified subject."""

    principal.require_user()
    claimed: list[str] = []
    if isinstance(payload, Mapping):
        for field_name in ("actor_id", "owner_id"):
            value = payload.get(field_name)
            if value is not None:
                claimed.append(str(value))
    if query_actor_id is not None:
        claimed.append(str(query_actor_id))
    if any(value != principal.actor_id for value in claimed):
        raise AuthorizationError("request principal does not match the authenticated subject")
    if any(value == "scheduler" for value in claimed):
        raise AuthorizationError("the scheduler service identity cannot be asserted over HTTP")


def _safe_claims(claims: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "sub",
        "iss",
        "aud",
        "client_id",
        "token_use",
        "scope",
        "scp",
        "auth_time",
        "iat",
        "exp",
        "tenant_id",
        "custom:tenant_id",
    }
    return {str(key): value for key, value in claims.items() if key in allowed}


def _claim_datetime(value: Any, field_name: str) -> datetime:
    try:
        result = datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (OverflowError, TypeError, ValueError) as exc:
        raise AuthenticationError(f"bearer token {field_name} claim is invalid") from exc
    return result


__all__ = [
    "AuthenticationError",
    "AuthorizationError",
    "BearerTokenVerifier",
    "OIDCBearerTokenVerifier",
    "parse_bearer_header",
    "require_claimed_actor",
]
