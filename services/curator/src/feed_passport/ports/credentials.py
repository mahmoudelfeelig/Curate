from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Protocol


class ConnectedAccount(Protocol):
    """Secret-free shape required by platform transports.

    ``domain.connections.AuthorizedConnection`` implements this structural
    contract.  Keeping the port structural lets the transport package remain
    independently testable and, importantly, keeps OAuth tokens out of the
    connection record.
    """

    id: str
    owner_id: str
    platform: str
    external_subject_id: str
    granted_scopes: frozenset[str]
    credential_ref: str
    status: str


class CredentialError(RuntimeError):
    """Base error for a credential broker boundary."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


class CredentialUnavailable(CredentialError):
    pass


class CredentialScopeDenied(CredentialError):
    pass


ProofFactory = Callable[[str, str, str], str]


class OAuthCredentialLease:
    """A short-lived, deliberately non-serializable OAuth credential.

    The raw token is private and is revealed only while constructing transport
    headers.  ``repr``, ``str``, copying, pickling, and ordinary JSON/dataclass
    serialization cannot expose it.  A proof factory supports DPoP providers
    such as AT Protocol without placing the proof key in this object or in any
    model-facing state.
    """

    __slots__ = (
        "_access_token",
        "_proof_factory",
        "credential_ref",
        "expires_at",
        "resource_server",
        "scheme",
        "scopes",
    )

    def __init__(
        self,
        *,
        access_token: str,
        credential_ref: str,
        scopes: frozenset[str],
        expires_at: datetime,
        resource_server: str | None = None,
        scheme: str = "Bearer",
        proof_factory: ProofFactory | None = None,
    ) -> None:
        token = access_token.strip()
        if not token or "\r" in token or "\n" in token:
            raise ValueError("OAuth access token is invalid")
        if not credential_ref.strip():
            raise ValueError("credential_ref is required")
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("credential expiry must be timezone-aware")
        normalized_scheme = scheme.strip()
        if normalized_scheme not in {"Bearer", "DPoP"}:
            raise ValueError("OAuth authorization scheme must be Bearer or DPoP")
        if normalized_scheme == "DPoP" and proof_factory is None:
            raise ValueError("DPoP credentials require a proof factory")
        if resource_server is not None and not resource_server.startswith("https://"):
            raise ValueError("OAuth resource server must use HTTPS")
        self._access_token = token
        self._proof_factory = proof_factory
        self.credential_ref = credential_ref
        self.scopes = frozenset(scopes)
        self.expires_at = expires_at
        self.resource_server = resource_server.rstrip("/") if resource_server else None
        self.scheme = normalized_scheme

    def authorization_headers(self, *, method: str, url: str) -> Mapping[str, str]:
        if not url.startswith("https://"):
            raise ValueError("OAuth credentials may only be attached to HTTPS requests")
        headers = {"Authorization": f"{self.scheme} {self._access_token}"}
        if self._proof_factory is not None:
            headers["DPoP"] = self._proof_factory(method.upper(), url, self._access_token)
        return headers

    def __repr__(self) -> str:
        return (
            "OAuthCredentialLease(access_token=<redacted>, "
            f"credential_ref={self.credential_ref!r}, scopes={self.scopes!r}, "
            f"expires_at={self.expires_at!r}, resource_server={self.resource_server!r}, "
            f"scheme={self.scheme!r})"
        )

    __str__ = __repr__

    def __copy__(self) -> OAuthCredentialLease:
        raise TypeError("OAuth credential leases cannot be copied")

    def __deepcopy__(self, _memo: object) -> OAuthCredentialLease:
        raise TypeError("OAuth credential leases cannot be copied")

    def __reduce_ex__(self, _protocol: int) -> object:
        raise TypeError("OAuth credential leases cannot be serialized")


class CredentialProvider(Protocol):
    """Resolve a short-lived token from an opaque, server-owned reference."""

    def lease(
        self,
        connection: ConnectedAccount,
        *,
        required_scopes: frozenset[str],
        now: datetime,
        force_refresh: bool = False,
    ) -> OAuthCredentialLease: ...
