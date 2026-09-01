from __future__ import annotations

import base64
import hashlib
import os
import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

import httpx

from feed_passport.domain.connections import ConnectionStatus, ExternalConnection
from feed_passport.ports.connections import ExternalConnectionRepository
from feed_passport.ports.live_platform import (
    HttpClient,
    LiveAuthenticationError,
    LivePlatformError,
    RemoteOutcomeUnknown,
)
from feed_passport.ports.oauth import (
    OAuthClientAuthentication,
    OAuthCredentialVault,
    OAuthProviderConfig,
    OAuthProviderRegistry,
)


class OAuthFlowError(PermissionError):
    """Sanitized OAuth error suitable for an authenticated API response."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


class AtprotoConnectionGrant(Protocol):
    """Opaque, owner-bound result returned by the credential sidecar."""

    owner_id: str
    external_subject: str

    def credential_ref_for_registry(self, *, owner_id: str) -> str: ...


class AtprotoOAuthBridge(Protocol):
    """Token-free application seam for the official AT Protocol sidecar."""

    def start_authorization(
        self,
        *,
        owner_id: str,
        handle: str,
        now: datetime,
    ) -> dict[str, Any]: ...

    def complete_authorization(
        self,
        *,
        owner_id: str,
        query: str,
        now: datetime,
    ) -> AtprotoConnectionGrant: ...

    def revoke_connection(self, connection: ExternalConnection, *, now: datetime) -> None: ...

    def revoke_grant(self, grant: AtprotoConnectionGrant, *, now: datetime) -> None: ...


class OAuthProviderCatalog(OAuthProviderRegistry):
    def __init__(self, providers: Mapping[str, OAuthProviderConfig]) -> None:
        self._providers = dict(providers)
        for platform, provider in self._providers.items():
            if platform != provider.platform:
                raise ValueError("OAuth provider key must match its platform")

    def get(self, platform: str) -> OAuthProviderConfig:
        try:
            return self._providers[platform]
        except KeyError as exc:
            raise OAuthFlowError(
                "oauth_provider_unconfigured",
                f"OAuth is not configured for {platform}.",
            ) from exc

    def public_status(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "platform": provider.platform,
                "configured": True,
                "scopes": sorted(provider.scopes),
                "redirect_uris": sorted(provider.allowed_redirect_uris),
                "pkce": provider.use_pkce,
            }
            for provider in sorted(self._providers.values(), key=lambda item: item.platform)
        )

    @classmethod
    def from_env(cls) -> OAuthProviderCatalog:
        redirect_uris = _redirect_allowlist_from_env()
        providers: dict[str, OAuthProviderConfig] = {}

        def registration(
            platform: str,
            *,
            authorize_url: str,
            token_url: str,
            identity_url: str,
            identity_kind: str,
            scopes: frozenset[str],
            revoke_url: str | None,
            use_pkce: bool,
            extra_authorize_params: Mapping[str, str],
            default_auth: OAuthClientAuthentication,
            resource_server: str | None = None,
        ) -> None:
            prefix = f"FEED_PASSPORT_{platform.upper()}_OAUTH_"
            client_id = os.getenv(f"{prefix}CLIENT_ID", "").strip()
            if not client_id:
                return
            user_agent = os.getenv(f"{prefix}USER_AGENT", "").strip() or None
            if platform == "reddit" and user_agent is None:
                raise ValueError(
                    "Reddit OAuth requires FEED_PASSPORT_REDDIT_OAUTH_USER_AGENT "
                    "with the approved app and operator identity"
                )
            secret = os.getenv(f"{prefix}CLIENT_SECRET")
            auth_value = os.getenv(f"{prefix}CLIENT_AUTH", default_auth.value).strip()
            try:
                client_auth = OAuthClientAuthentication(auth_value)
            except ValueError as exc:
                raise ValueError(f"invalid {platform} OAuth client authentication mode") from exc
            providers[platform] = OAuthProviderConfig(
                platform=platform,
                client_id=client_id,
                client_secret=secret,
                authorize_url=authorize_url,
                token_url=token_url,
                revoke_url=revoke_url,
                identity_url=identity_url,
                identity_kind=identity_kind,
                scopes=scopes,
                allowed_redirect_uris=redirect_uris,
                use_pkce=use_pkce,
                extra_authorize_params=extra_authorize_params,
                client_authentication=client_auth,
                resource_server=resource_server,
                user_agent=user_agent,
            )

        registration(
            "youtube",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            revoke_url="https://oauth2.googleapis.com/revoke",
            identity_url="https://www.googleapis.com/youtube/v3/channels?part=id&mine=true",
            identity_kind="youtube_channel",
            scopes=frozenset({"https://www.googleapis.com/auth/youtube"}),
            use_pkce=True,
            extra_authorize_params={
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent",
            },
            default_auth=OAuthClientAuthentication.CLIENT_SECRET_POST,
        )
        registration(
            "x",
            authorize_url="https://x.com/i/oauth2/authorize",
            token_url="https://api.x.com/2/oauth2/token",
            revoke_url="https://api.x.com/2/oauth2/revoke",
            identity_url="https://api.x.com/2/users/me",
            identity_kind="x_user",
            scopes=frozenset(
                {
                    "users.read",
                    "follows.read",
                    "follows.write",
                    "mute.read",
                    "mute.write",
                    "offline.access",
                }
            ),
            use_pkce=True,
            extra_authorize_params={},
            default_auth=OAuthClientAuthentication.CLIENT_SECRET_BASIC,
        )
        registration(
            "reddit",
            authorize_url="https://www.reddit.com/api/v1/authorize",
            token_url="https://www.reddit.com/api/v1/access_token",
            revoke_url="https://www.reddit.com/api/v1/revoke_token",
            identity_url="https://oauth.reddit.com/api/v1/me",
            identity_kind="reddit_user",
            scopes=frozenset({"identity", "mysubreddits", "subscribe"}),
            use_pkce=False,
            extra_authorize_params={"duration": "permanent"},
            default_auth=OAuthClientAuthentication.CLIENT_SECRET_BASIC,
        )
        return cls(providers)


class OAuthConnectionService:
    """Principal-bound OAuth authorization-code orchestration.

    The callback is an authenticated backend POST. A browser redirect page must
    forward ``code`` and ``state`` with the signed-in user's bearer token; this
    avoids treating OAuth state as an application login credential.
    """

    def __init__(
        self,
        *,
        connections: ExternalConnectionRepository,
        credentials: OAuthCredentialVault,
        providers: OAuthProviderRegistry,
        http_client: HttpClient,
    ) -> None:
        self.connections = connections
        self.credentials = credentials
        self.providers = providers
        self._http = http_client

    def start(
        self,
        *,
        owner_id: str,
        platform: str,
        redirect_uri: str,
        now: datetime,
    ) -> dict[str, Any]:
        canonical_now = _aware(now)
        provider = self.providers.get(platform)
        if redirect_uri not in provider.allowed_redirect_uris:
            raise OAuthFlowError(
                "redirect_uri_denied",
                "The OAuth redirect URI is not registered for this deployment.",
            )
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        transaction = self.connections.begin_oauth_transaction(
            owner_id=owner_id,
            platform=platform,
            redirect_uri=redirect_uri,
            metadata={
                "code_verifier": verifier if provider.use_pkce else None,
                "requested_scopes": sorted(provider.scopes),
                "client_id_fingerprint": hashlib.sha256(
                    provider.client_id.encode("utf-8")
                ).hexdigest(),
            },
            now=canonical_now,
            ttl=timedelta(minutes=10),
        )
        query: dict[str, str] = {
            "response_type": "code",
            "client_id": provider.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(sorted(provider.scopes)),
            "state": transaction.state,
            **dict(provider.extra_authorize_params),
        }
        if provider.use_pkce:
            query.update(
                {
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        return {
            "platform": platform,
            "authorization_url": f"{provider.authorize_url}?{urlencode(query)}",
            "expires_at": transaction.expires_at.isoformat(),
            "redirect_uri": redirect_uri,
            "scopes": sorted(provider.scopes),
        }

    def callback(
        self,
        *,
        owner_id: str,
        platform: str,
        state: str,
        code: str,
        now: datetime,
    ) -> ExternalConnection:
        canonical_now = _aware(now)
        if not code.strip() or "\r" in code or "\n" in code:
            raise OAuthFlowError("authorization_code_invalid", "The OAuth callback code is invalid.")
        provider = self.providers.get(platform)
        transaction = self.connections.consume_oauth_transaction(
            state,
            owner_id=owner_id,
            platform=platform,
            now=canonical_now,
        )
        fingerprint = transaction.metadata.get("client_id_fingerprint")
        expected_fingerprint = hashlib.sha256(provider.client_id.encode("utf-8")).hexdigest()
        if fingerprint != expected_fingerprint:
            raise OAuthFlowError(
                "oauth_client_changed",
                "The OAuth client registration changed; start authorization again.",
            )
        token_response = self._exchange_code(
            provider,
            code=code,
            redirect_uri=transaction.redirect_uri,
            verifier=transaction.metadata.get("code_verifier"),
        )
        credential_ref: str | None = None
        existing: ExternalConnection | None = None
        revoke_orphan_grant = True
        try:
            granted_scopes = self._granted_scopes(token_response, provider.scopes)
            external_subject = self._external_subject(provider, token_response)
            existing = self._matching_connection(
                owner_id=owner_id,
                platform=platform,
                external_subject=external_subject,
            )
            revoke_orphan_grant = existing is None or existing.status is ConnectionStatus.REVOKED
            credential_ref = self.credentials.put(
                owner_id=owner_id,
                platform=platform,
                token_response=token_response,
                granted_scopes=granted_scopes,
                now=canonical_now,
                resource_server=provider.resource_server,
            )
            metadata = {
                "granted_scopes": sorted(granted_scopes),
                "authorization_method": "oauth2_authorization_code",
                "authorized_at": canonical_now.isoformat(),
            }
            if existing is not None and existing.status in {
                ConnectionStatus.ACTIVE,
                ConnectionStatus.REAUTH_REQUIRED,
            }:
                try:
                    rotated = self.connections.update_connection(
                        existing.id,
                        owner_id=owner_id,
                        expected_version=existing.version,
                        now=canonical_now,
                        status=ConnectionStatus.ACTIVE,
                        credential_ref=credential_ref,
                        metadata={**dict(existing.metadata), **metadata},
                        expected_external_subject=external_subject,
                    )
                except Exception:
                    concurrent = self._matching_connection(
                        owner_id=owner_id,
                        platform=platform,
                        external_subject=external_subject,
                    )
                    if (
                        concurrent is not None
                        and concurrent.status is ConnectionStatus.ACTIVE
                        and secrets.compare_digest(concurrent.credential_ref, credential_ref)
                    ):
                        return concurrent
                    self.credentials.discard(credential_ref, owner_id=owner_id)
                    credential_ref = None
                    raise
                if not secrets.compare_digest(existing.credential_ref, credential_ref):
                    self.credentials.discard(existing.credential_ref, owner_id=owner_id)
                return rotated
            if existing is not None and existing.status is ConnectionStatus.REVOKING:
                self.credentials.discard(credential_ref, owner_id=owner_id)
                credential_ref = None
                raise OAuthFlowError(
                    "oauth_connection_conflict",
                    "This account is still completing revocation; retry after it finishes.",
                )
            return self.connections.register_connection(
                owner_id=owner_id,
                platform=platform,
                external_subject=external_subject,
                credential_ref=credential_ref,
                metadata=metadata,
                now=canonical_now,
            )
        except Exception:
            if credential_ref is not None:
                self.credentials.discard(credential_ref, owner_id=owner_id)
            if revoke_orphan_grant:
                self._best_effort_revoke_token(provider, token_response)
            raise

    def _matching_connection(
        self,
        *,
        owner_id: str,
        platform: str,
        external_subject: str,
    ) -> ExternalConnection | None:
        matching = tuple(
            value
            for value in self.connections.list_connections(
                owner_id=owner_id,
                platform=platform,
            )
            if secrets.compare_digest(value.external_subject, external_subject)
        )
        for status in (
            ConnectionStatus.ACTIVE,
            ConnectionStatus.REAUTH_REQUIRED,
            ConnectionStatus.REVOKING,
        ):
            current = next((value for value in matching if value.status is status), None)
            if current is not None:
                return current
        return matching[-1] if matching else None

    def revoke(
        self,
        connection_id: str,
        *,
        owner_id: str,
        expected_version: int,
        now: datetime,
    ) -> ExternalConnection:
        canonical_now = _aware(now)
        connection = self.connections.get_connection(connection_id, owner_id=owner_id)
        if connection.version != expected_version:
            raise OAuthFlowError(
                "connection_changed",
                "The connected account changed; refresh before revoking it.",
            )
        if connection.status is ConnectionStatus.REVOKED:
            self.credentials.discard(connection.credential_ref, owner_id=owner_id)
            return connection
        pending = connection
        if connection.status is not ConnectionStatus.REVOKING:
            pending = self.connections.update_connection(
                connection_id,
                owner_id=owner_id,
                expected_version=expected_version,
                now=canonical_now,
                status=ConnectionStatus.REVOKING,
            )
        self.credentials.revoke(pending, now=canonical_now)
        revoked = self.connections.revoke_connection(
            connection_id,
            owner_id=owner_id,
            expected_version=pending.version,
            now=canonical_now,
        )
        return revoked

    def _exchange_code(
        self,
        provider: OAuthProviderConfig,
        *,
        code: str,
        redirect_uri: str,
        verifier: Any,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": provider.client_id,
        }
        if provider.use_pkce:
            if not isinstance(verifier, str) or len(verifier) < 43:
                raise OAuthFlowError("pkce_state_invalid", "The OAuth PKCE transaction is invalid.")
            data["code_verifier"] = verifier
        headers = _provider_headers(provider)
        _attach_client_auth(provider, data, headers)
        try:
            response = self._http.request(
                "POST",
                provider.token_url,
                headers=headers,
                data=data,
            )
        except (httpx.TimeoutException, httpx.NetworkError, TimeoutError, OSError) as exc:
            raise OAuthFlowError(
                "token_exchange_interrupted",
                "The OAuth token exchange was interrupted; start authorization again.",
            ) from exc
        if response.status_code >= 400:
            raise OAuthFlowError(
                "token_exchange_rejected",
                "The platform rejected the OAuth token exchange; start authorization again.",
            )
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise OAuthFlowError(
                "token_response_invalid",
                "The platform returned an invalid OAuth token response.",
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
            raise OAuthFlowError(
                "token_response_invalid",
                "The platform returned an invalid OAuth token response.",
            )
        return payload

    def _external_subject(
        self,
        provider: OAuthProviderConfig,
        token_response: Mapping[str, Any],
    ) -> str:
        token = token_response.get("access_token")
        if not isinstance(token, str) or not token:
            raise OAuthFlowError("token_response_invalid", "The OAuth token response is invalid.")
        try:
            response = self._http.request(
                "GET",
                provider.identity_url,
                headers={**_provider_headers(provider), "Authorization": f"Bearer {token}"},
            )
        except (httpx.TimeoutException, httpx.NetworkError, TimeoutError, OSError) as exc:
            raise OAuthFlowError(
                "identity_lookup_interrupted",
                "The platform account identity could not be confirmed.",
            ) from exc
        if response.status_code >= 400:
            raise OAuthFlowError(
                "identity_lookup_rejected",
                "The platform account identity could not be confirmed.",
            )
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise OAuthFlowError(
                "identity_response_invalid",
                "The platform returned an invalid account identity response.",
            ) from exc
        subject: Any = None
        if provider.identity_kind == "youtube_channel" and isinstance(payload, dict):
            items = payload.get("items")
            if isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict):
                subject = items[0].get("id")
        elif provider.identity_kind == "x_user" and isinstance(payload, dict):
            data = payload.get("data")
            subject = data.get("id") if isinstance(data, dict) else None
        elif provider.identity_kind == "reddit_user" and isinstance(payload, dict):
            subject = payload.get("name") or payload.get("id")
        if not isinstance(subject, str) or not subject.strip():
            raise OAuthFlowError(
                "identity_response_invalid",
                "The platform did not return one confirmed account identity.",
            )
        return subject

    def _best_effort_revoke_token(
        self,
        provider: OAuthProviderConfig,
        token_response: Mapping[str, Any],
    ) -> None:
        if provider.revoke_url is None:
            return
        token = token_response.get("refresh_token") or token_response.get("access_token")
        if not isinstance(token, str) or not token:
            return
        data: dict[str, Any] = {"token": token, "client_id": provider.client_id}
        headers = _provider_headers(provider)
        try:
            _attach_client_auth(provider, data, headers)
            self._http.request("POST", provider.revoke_url, headers=headers, data=data)
        except Exception:
            # Cleanup must never replace the sanitized callback failure. The
            # authorization code is already consumed and no credential is
            # retained locally, so a failed provider cleanup is non-replayable.
            return

    @staticmethod
    def _granted_scopes(
        token_response: Mapping[str, Any],
        requested: frozenset[str],
    ) -> frozenset[str]:
        raw = token_response.get("scope")
        if isinstance(raw, str):
            values = frozenset(value for value in raw.split() if value)
        elif isinstance(raw, (list, tuple, set, frozenset)):
            values = frozenset(str(value) for value in raw if str(value).strip())
        else:
            values = requested
        if not values:
            raise OAuthFlowError("oauth_scope_missing", "The platform granted no OAuth scopes.")
        return values


class AtprotoOAuthConnectionService:
    """Owner-bound Bluesky OAuth orchestration through the official sidecar.

    The application receives only an account DID and an opaque sidecar
    reference. OAuth tokens, refresh tokens, DPoP proofs, and private keys never
    cross this seam and the raw callback query is never persisted.
    """

    platform = "bluesky"
    scopes = frozenset({"atproto", "transition:generic"})

    def __init__(
        self,
        *,
        connections: ExternalConnectionRepository,
        sidecar: AtprotoOAuthBridge,
        callback_uri: str,
    ) -> None:
        if not callable(getattr(sidecar, "start_authorization", None)):
            raise TypeError("an AT Protocol OAuth sidecar bridge is required")
        self.connections = connections
        self.sidecar = sidecar
        self.callback_uri = _validated_app_callback_uri(callback_uri)

    def public_status(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "configured": True,
            "scopes": sorted(self.scopes),
            "redirect_uris": [self.callback_uri],
            "pkce": True,
            "credential_boundary": "official_atproto_sidecar",
        }

    def start(
        self,
        *,
        owner_id: str,
        platform: str,
        handle: str,
        now: datetime,
    ) -> dict[str, Any]:
        self._require_platform(platform)
        canonical_now = _aware(now)
        try:
            started = self.sidecar.start_authorization(
                owner_id=owner_id,
                handle=handle,
                now=canonical_now,
            )
        except LivePlatformError as exc:
            raise _atproto_oauth_error(exc, operation="start") from exc
        expires_at = started.get("expires_at")
        if not isinstance(expires_at, datetime):
            raise OAuthFlowError(
                "atproto_oauth_protocol_invalid",
                "The AT Protocol credential sidecar returned an invalid authorization result.",
            )
        return {
            "platform": self.platform,
            "authorization_url": started["authorization_url"],
            "expires_at": _aware(expires_at).isoformat(),
            "redirect_uri": self.callback_uri,
            "scopes": sorted(self.scopes),
        }

    def callback(
        self,
        *,
        owner_id: str,
        platform: str,
        query: str,
        now: datetime,
    ) -> ExternalConnection:
        self._require_platform(platform)
        canonical_now = _aware(now)
        try:
            grant = self.sidecar.complete_authorization(
                owner_id=owner_id,
                query=query,
                now=canonical_now,
            )
            credential_ref = grant.credential_ref_for_registry(owner_id=owner_id)
        except LivePlatformError as exc:
            raise _atproto_oauth_error(exc, operation="callback") from exc

        metadata = {
            "granted_scopes": sorted(self.scopes),
            "authorization_method": "atproto_oauth_sidecar",
            "authorized_at": canonical_now.isoformat(),
            "credential_boundary": "official_atproto_oauth_client",
        }
        existing = self._matching_connection(
            owner_id=owner_id,
            external_subject=grant.external_subject,
        )
        if existing is not None:
            if existing.status is ConnectionStatus.ACTIVE:
                if secrets.compare_digest(existing.credential_ref, credential_ref):
                    return existing
                try:
                    return self.connections.update_connection(
                        existing.id,
                        owner_id=owner_id,
                        expected_version=existing.version,
                        now=canonical_now,
                        status=ConnectionStatus.ACTIVE,
                        credential_ref=credential_ref,
                        metadata={**dict(existing.metadata), **metadata},
                    )
                except Exception:
                    concurrent = self._matching_connection(
                        owner_id=owner_id,
                        external_subject=grant.external_subject,
                    )
                    if (
                        concurrent is not None
                        and concurrent.status is ConnectionStatus.ACTIVE
                        and secrets.compare_digest(concurrent.credential_ref, credential_ref)
                    ):
                        return concurrent
                    try:
                        self.sidecar.revoke_grant(grant, now=canonical_now)
                    except LivePlatformError:
                        pass
                    raise
            if existing.status is not ConnectionStatus.REVOKED:
                try:
                    self.sidecar.revoke_grant(grant, now=canonical_now)
                except LivePlatformError:
                    pass
                raise OAuthFlowError(
                    "atproto_connection_conflict",
                    "This Bluesky account already has a different local connection record.",
                )

        try:
            return self.connections.register_connection(
                owner_id=owner_id,
                platform=self.platform,
                external_subject=grant.external_subject,
                credential_ref=credential_ref,
                metadata=metadata,
                now=canonical_now,
            )
        except Exception:
            # A concurrent callback may have won the local registration. Return
            # that exact owner-bound record without revoking the shared sidecar
            # session; otherwise remove the orphaned sidecar binding best-effort.
            concurrent = self._matching_connection(
                owner_id=owner_id,
                external_subject=grant.external_subject,
            )
            if (
                concurrent is not None
                and concurrent.status is ConnectionStatus.ACTIVE
                and secrets.compare_digest(concurrent.credential_ref, credential_ref)
            ):
                return concurrent
            try:
                self.sidecar.revoke_grant(grant, now=canonical_now)
            except LivePlatformError:
                pass
            raise

    def revoke(
        self,
        connection_id: str,
        *,
        owner_id: str,
        expected_version: int,
        now: datetime,
    ) -> ExternalConnection:
        canonical_now = _aware(now)
        connection = self.connections.get_connection(connection_id, owner_id=owner_id)
        self._require_platform(connection.platform)
        if connection.version != expected_version:
            raise OAuthFlowError(
                "connection_changed",
                "The connected account changed; refresh before revoking it.",
            )
        if connection.status is ConnectionStatus.REVOKED:
            return connection
        pending = connection
        if connection.status is not ConnectionStatus.REVOKING:
            pending = self.connections.update_connection(
                connection_id,
                owner_id=owner_id,
                expected_version=expected_version,
                now=canonical_now,
                status=ConnectionStatus.REVOKING,
            )
        try:
            self.sidecar.revoke_connection(pending, now=canonical_now)
        except LivePlatformError as exc:
            raise _atproto_oauth_error(exc, operation="revoke") from exc
        return self.connections.revoke_connection(
            connection_id,
            owner_id=owner_id,
            expected_version=pending.version,
            now=canonical_now,
        )

    def _matching_connection(
        self,
        *,
        owner_id: str,
        external_subject: str,
    ) -> ExternalConnection | None:
        matching = tuple(
            value
            for value in self.connections.list_connections(
                owner_id=owner_id,
                platform=self.platform,
            )
            if value.external_subject == external_subject
        )
        active = next(
            (value for value in matching if value.status is ConnectionStatus.ACTIVE),
            None,
        )
        if active is not None:
            return active
        revoking = next(
            (value for value in matching if value.status is ConnectionStatus.REVOKING),
            None,
        )
        if revoking is not None:
            return revoking
        return matching[-1] if matching else None

    def _require_platform(self, platform: str) -> None:
        if platform != self.platform:
            raise OAuthFlowError(
                "oauth_provider_unconfigured",
                f"OAuth is not configured for {platform}.",
            )


def _atproto_oauth_error(exc: LivePlatformError, *, operation: str) -> OAuthFlowError:
    if isinstance(exc, RemoteOutcomeUnknown) or exc.outcome_unknown:
        return OAuthFlowError(
            "atproto_oauth_outcome_unknown",
            "The AT Protocol sidecar outcome could not be confirmed; refresh connections before retrying.",
        )
    if isinstance(exc, LiveAuthenticationError):
        return OAuthFlowError(
            "atproto_connection_unavailable",
            "The owner-bound Bluesky authorization is unavailable; connect the account again.",
        )
    if exc.retryable or (exc.http_status is not None and exc.http_status >= 500):
        return OAuthFlowError(
            "atproto_sidecar_unavailable",
            "The AT Protocol credential sidecar is temporarily unavailable.",
        )
    return OAuthFlowError(
        f"atproto_oauth_{operation}_rejected",
        f"The AT Protocol credential sidecar rejected the OAuth {operation} request.",
    )


def _validated_app_callback_uri(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(character in value for character in "\r\n"):
        raise ValueError("AT Protocol app callback URI is required")
    try:
        parsed = urlsplit(value.strip())
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("AT Protocol app callback URI is invalid") from exc
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        parsed.scheme not in ({"http", "https"} if loopback else {"https"})
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/oauth/callback"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "AT Protocol app callback URI must be HTTPS (or loopback HTTP) and end /oauth/callback"
        )
    return value.strip()


def _redirect_allowlist_from_env() -> frozenset[str]:
    raw = os.getenv(
        "FEED_PASSPORT_OAUTH_REDIRECT_URIS",
        "http://127.0.0.1:5173/oauth/callback",
    )
    values = frozenset(value.strip() for value in raw.split(",") if value.strip())
    if not values:
        raise ValueError("FEED_PASSPORT_OAUTH_REDIRECT_URIS must not be empty")
    return values


def _attach_client_auth(
    provider: OAuthProviderConfig,
    data: dict[str, Any],
    headers: dict[str, str],
) -> None:
    if provider.client_authentication is OAuthClientAuthentication.NONE:
        return
    secret = provider.client_secret
    if not isinstance(secret, str) or not secret:
        raise OAuthFlowError(
            "oauth_client_unavailable",
            "The OAuth client registration is incomplete.",
        )
    if provider.client_authentication is OAuthClientAuthentication.CLIENT_SECRET_POST:
        data["client_secret"] = secret
        return
    encoded = base64.b64encode(f"{provider.client_id}:{secret}".encode("utf-8")).decode("ascii")
    headers["Authorization"] = f"Basic {encoded}"


def _provider_headers(provider: OAuthProviderConfig) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if provider.user_agent is not None:
        headers["User-Agent"] = provider.user_agent
    return headers


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("OAuth time must be timezone-aware")
    return value.astimezone(timezone.utc)
