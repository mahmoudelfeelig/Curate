from __future__ import annotations

import copy
import ipaddress
import json
import math
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, ClassVar
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from feed_passport.adapters.platforms.bluesky import BLUESKY_PROFILE
from feed_passport.domain.models import AccountObservation, ActionType, FeedSample, ProposedAction
from feed_passport.ports.credentials import ConnectedAccount
from feed_passport.ports.live_platform import (
    HttpClient,
    LiveAuthenticationError,
    LivePermissionError,
    LiveProtocolError,
    LiveRateLimited,
    LiveTargetNotFound,
    LiveTransientError,
    RemoteOutcomeUnknown,
    ValidatedLiveCertification,
)

from .base import CertifiedLivePlatformAdapter


_OWNER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")
_DID_PATTERN = re.compile(r"^did:[a-z0-9]+:[A-Za-z0-9._:%-]{1,300}$")
_OPAQUE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,95}$")
_FORBIDDEN_FIELD_PATTERN = re.compile(
    r"^(?:access_?token|refresh_?token|id_?token|session_?token|authorization|cookie|"
    r"dpop(?:_?(?:key|proof))?|private_?key|private_?jwk|client_?secret|secret|"
    r"raw_?callback|callback_?query|oauth_?callback)$",
    re.IGNORECASE,
)
_RESTORE_PATH = "/v1/oauth/atproto/sessions/restore"
_EXECUTE_PATH = "/v1/oauth/atproto/sessions/execute"
_START_PATH = "/v1/oauth/atproto/start"
_CALLBACK_PATH = "/v1/oauth/atproto/callback"
_REVOKE_PATH = "/v1/oauth/atproto/sessions/revoke"
_PRIVATE_PATHS = frozenset(
    {_START_PATH, _CALLBACK_PATH, _RESTORE_PATH, _EXECUTE_PATH, _REVOKE_PATH}
)
_AUTHENTICATION_CODES = frozenset(
    {
        "atproto_connection_inactive",
        "atproto_connection_not_found",
        "atproto_connection_revoking",
        "session_lease_expired",
        "session_lease_not_found",
        "session_restore_failed",
        "sidecar_authentication_required",
    }
)
_OAUTH_CALLBACK_CODES = frozenset(
    {
        "atproto_subject_already_bound",
        "oauth_callback_in_progress",
        "oauth_callback_rejected",
        "oauth_callback_state_mismatch",
        "oauth_state_expired",
        "oauth_state_unknown",
    }
)


class AtprotoSidecarOperation(StrEnum):
    GET_FOLLOWS = "graph.get_follows"
    GET_MUTES = "graph.get_mutes"
    FOLLOW = "graph.follow"
    DELETE_FOLLOW = "graph.delete_follow"
    MUTE = "graph.mute"
    UNMUTE = "graph.unmute"
    GET_PREFERENCES = "actor.get_preferences"
    PUT_PREFERENCES = "actor.put_preferences"


class AtprotoSidecarConnectionGrant:
    """Token-free callback result whose opaque reference stays server-side."""

    __slots__ = ("_connection_ref", "external_subject", "owner_id")

    def __init__(self, *, owner_id: str, connection_ref: str, external_subject: str) -> None:
        self.owner_id = _require_owner(owner_id)
        self._connection_ref = _require_opaque(connection_ref, "connection reference")
        self.external_subject = _require_did(external_subject)

    def credential_ref_for_registry(self, *, owner_id: str) -> str:
        if _require_owner(owner_id) != self.owner_id:
            raise LiveAuthenticationError(
                platform="bluesky",
                code="atproto_owner_binding_mismatch",
                detail="The AT Protocol connection grant belongs to a different owner.",
            )
        return self._connection_ref

    def __repr__(self) -> str:
        return (
            "AtprotoSidecarConnectionGrant(owner_id="
            f"{self.owner_id!r}, connection_ref=<redacted>, "
            f"external_subject={self.external_subject!r})"
        )

    __str__ = __repr__

    def __copy__(self) -> AtprotoSidecarConnectionGrant:
        raise TypeError("AT Protocol connection grants cannot be copied")

    def __deepcopy__(self, _memo: object) -> AtprotoSidecarConnectionGrant:
        raise TypeError("AT Protocol connection grants cannot be copied")

    def __reduce_ex__(self, _protocol: int) -> object:
        raise TypeError("AT Protocol connection grants cannot be serialized")


class _InternalServiceCredential:
    """Non-serializable credential used only on the private sidecar hop."""

    __slots__ = ("__secret",)

    def __init__(self, secret: str) -> None:
        if not isinstance(secret, str) or not 32 <= len(secret) <= 4096 or "\r" in secret or "\n" in secret:
            raise ValueError("the AT Protocol sidecar requires a high-entropy internal credential")
        self.__secret = secret

    def headers(self, owner_id: str) -> Mapping[str, str]:
        _require_owner(owner_id)
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.__secret}",
            "Content-Type": "application/json",
            "User-Agent": "feed-passport/0.1",
            "X-Feed-Passport-Owner": owner_id,
        }

    def reject_exposure(self, value: Mapping[str, Any]) -> None:
        encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        if self.__secret in encoded:
            raise ValueError("sidecar response contained internal credential material")

    def __repr__(self) -> str:
        return "_InternalServiceCredential(secret=<redacted>)"

    __str__ = __repr__

    def __copy__(self) -> _InternalServiceCredential:
        raise TypeError("internal service credentials cannot be copied")

    def __deepcopy__(self, _memo: object) -> _InternalServiceCredential:
        raise TypeError("internal service credentials cannot be copied")

    def __reduce_ex__(self, _protocol: int) -> object:
        raise TypeError("internal service credentials cannot be serialized")


class AtprotoSidecarLease:
    """Short-lived owner-bound reference to a DPoP session held by the sidecar.

    The lease deliberately exposes neither its opaque references nor the
    internal service credential through representation or serialization.  It
    contains no AT Protocol token, refresh token, DPoP proof, or key.
    """

    __slots__ = (
        "_client_marker",
        "_connection_ref",
        "_credential",
        "_lease_ref",
        "expires_at",
        "external_subject",
        "owner_id",
    )

    def __init__(
        self,
        *,
        client_marker: object,
        credential: _InternalServiceCredential,
        owner_id: str,
        connection_ref: str,
        lease_ref: str,
        external_subject: str,
        expires_at: datetime,
    ) -> None:
        _require_owner(owner_id)
        _require_opaque(connection_ref, "connection reference")
        _require_opaque(lease_ref, "lease reference")
        _require_did(external_subject)
        _require_aware(expires_at, "lease expiry")
        self._client_marker = client_marker
        self._credential = credential
        self._connection_ref = connection_ref
        self._lease_ref = lease_ref
        self.owner_id = owner_id
        self.external_subject = external_subject
        self.expires_at = expires_at

    def __repr__(self) -> str:
        return (
            "AtprotoSidecarLease(owner_id="
            f"{self.owner_id!r}, connection_ref=<redacted>, lease_ref=<redacted>, "
            f"external_subject={self.external_subject!r}, expires_at={self.expires_at!r}, "
            "internal_auth=<redacted>)"
        )

    __str__ = __repr__

    def __copy__(self) -> AtprotoSidecarLease:
        raise TypeError("AT Protocol sidecar leases cannot be copied")

    def __deepcopy__(self, _memo: object) -> AtprotoSidecarLease:
        raise TypeError("AT Protocol sidecar leases cannot be copied")

    def __reduce_ex__(self, _protocol: int) -> object:
        raise TypeError("AT Protocol sidecar leases cannot be serialized")


class AtprotoSidecarClient:
    """Fixed-route client for the credential-isolated official OAuth sidecar."""

    __slots__ = ("_base_url", "_client_marker", "_credential", "_http")

    def __init__(
        self,
        *,
        base_url: str,
        internal_service_secret: str,
        http_client: HttpClient,
    ) -> None:
        self._base_url = _validated_base_url(base_url)
        self._credential = _InternalServiceCredential(internal_service_secret)
        if not callable(getattr(http_client, "request", None)):
            raise TypeError("an injected no-ambient HTTP client is required")
        self._http = http_client
        self._client_marker = object()

    def start_authorization(
        self,
        *,
        owner_id: str,
        handle: str,
        now: datetime,
    ) -> dict[str, Any]:
        _require_aware(now, "current time")
        owner = _require_owner(owner_id)
        canonical_handle = _require_handle(handle)
        payload = self._post(
            _START_PATH,
            owner_id=owner,
            body={"handle": canonical_handle},
            mutation=False,
        )
        if set(payload) != {"authorization_url", "expires_at", "platform"}:
            raise LiveProtocolError(
                platform="bluesky",
                code="atproto_authorization_response_invalid",
                detail="The AT Protocol sidecar returned an invalid authorization response.",
            )
        authorization_url = payload.get("authorization_url")
        try:
            authorization = urlsplit(str(authorization_url))
        except ValueError as exc:
            raise LiveProtocolError(
                platform="bluesky",
                code="atproto_authorization_response_invalid",
                detail="The AT Protocol sidecar returned an invalid authorization response.",
            ) from exc
        expires_at = _parse_timestamp(payload.get("expires_at"), "authorization expiry")
        if (
            payload.get("platform") != "bluesky"
            or not isinstance(authorization_url, str)
            or authorization.scheme != "https"
            or not authorization.hostname
            or authorization.username is not None
            or authorization.password is not None
            or authorization.fragment
            or expires_at <= now
            or expires_at > now + timedelta(minutes=15)
        ):
            raise LiveProtocolError(
                platform="bluesky",
                code="atproto_authorization_response_invalid",
                detail="The AT Protocol sidecar returned an invalid authorization response.",
            )
        return {
            "platform": "bluesky",
            "authorization_url": authorization_url,
            "expires_at": expires_at,
        }

    def complete_authorization(
        self,
        *,
        owner_id: str,
        query: str,
        now: datetime,
    ) -> AtprotoSidecarConnectionGrant:
        _require_aware(now, "current time")
        owner = _require_owner(owner_id)
        if (
            not isinstance(query, str)
            or not 1 <= len(query) <= 16_384
            or any(character in query for character in "\r\n\0#")
        ):
            raise ValueError("AT Protocol OAuth callback query is invalid")
        payload = self._post(
            _CALLBACK_PATH,
            owner_id=owner,
            body={"query": query},
            mutation=True,
        )
        if set(payload) != {"connection_ref", "external_subject", "platform", "status"}:
            raise RemoteOutcomeUnknown(
                platform="bluesky",
                code="atproto_callback_response_invalid",
                detail="The sidecar callback returned no trustworthy connection result.",
                outcome_unknown=True,
            )
        if payload.get("platform") != "bluesky" or payload.get("status") != "active":
            raise RemoteOutcomeUnknown(
                platform="bluesky",
                code="atproto_callback_response_invalid",
                detail="The sidecar callback returned no trustworthy connection result.",
                outcome_unknown=True,
            )
        return AtprotoSidecarConnectionGrant(
            owner_id=owner,
            connection_ref=payload.get("connection_ref"),
            external_subject=payload.get("external_subject"),
        )

    def revoke_connection(
        self,
        connection: ConnectedAccount,
        *,
        now: datetime,
    ) -> None:
        _require_aware(now, "current time")
        owner = _require_owner(connection.owner_id)
        reference = _require_opaque(connection.credential_ref, "connection reference")
        self._revoke_reference(owner_id=owner, connection_ref=reference)

    def revoke_grant(
        self,
        grant: AtprotoSidecarConnectionGrant,
        *,
        now: datetime,
    ) -> None:
        _require_aware(now, "current time")
        if not isinstance(grant, AtprotoSidecarConnectionGrant):
            raise TypeError("AT Protocol sidecar connection grant is required")
        self._revoke_reference(owner_id=grant.owner_id, connection_ref=grant._connection_ref)

    def _revoke_reference(self, *, owner_id: str, connection_ref: str) -> None:
        payload = self._post(
            _REVOKE_PATH,
            owner_id=owner_id,
            body={"connection_ref": connection_ref},
            mutation=True,
        )
        if (
            set(payload) != {"connection_ref", "platform", "revocation_proof", "status"}
            or payload.get("platform") != "bluesky"
            or payload.get("connection_ref") != connection_ref
            or payload.get("status") != "revoked"
            or payload.get("revocation_proof")
            not in {"provider_confirmed", "sidecar_terminal"}
        ):
            raise RemoteOutcomeUnknown(
                platform="bluesky",
                code="atproto_revoke_response_invalid",
                detail="The sidecar revocation returned no trustworthy result.",
                outcome_unknown=True,
            )

    def restore(
        self,
        connection: ConnectedAccount,
        *,
        now: datetime,
    ) -> AtprotoSidecarLease:
        _require_aware(now, "current time")
        _require_owner(connection.owner_id)
        connection_ref = _require_opaque(connection.credential_ref, "connection reference")
        external_subject = _require_did(connection.external_subject_id)
        payload = self._post(
            _RESTORE_PATH,
            owner_id=connection.owner_id,
            body={"connection_ref": connection_ref},
            mutation=False,
        )
        expected_fields = {
            "authorization_scheme",
            "connection_ref",
            "expires_at",
            "external_subject",
            "lease_ref",
            "platform",
        }
        if set(payload) != expected_fields:
            raise LiveProtocolError(
                platform="bluesky",
                code="atproto_restore_response_invalid",
                detail="The AT Protocol sidecar returned an invalid session lease.",
            )
        if payload.get("platform") != "bluesky" or payload.get("authorization_scheme") != "DPoP":
            raise LiveProtocolError(
                platform="bluesky",
                code="atproto_restore_response_invalid",
                detail="The AT Protocol sidecar returned an invalid session lease.",
            )
        returned_connection = _require_opaque(payload.get("connection_ref"), "connection reference")
        returned_subject = _require_did(payload.get("external_subject"))
        lease_ref = _require_opaque(payload.get("lease_ref"), "lease reference")
        expires_at = _parse_timestamp(payload.get("expires_at"), "lease expiry")
        if returned_connection != connection_ref or returned_subject != external_subject:
            raise LiveAuthenticationError(
                platform="bluesky",
                code="atproto_owner_binding_mismatch",
                detail="The sidecar session did not match the owner-bound connected account.",
            )
        if expires_at <= now or expires_at > now + timedelta(minutes=5):
            raise LiveAuthenticationError(
                platform="bluesky",
                code="atproto_sidecar_lease_expired",
                detail="The AT Protocol sidecar returned an invalid short-lived session lease.",
            )
        return AtprotoSidecarLease(
            client_marker=self._client_marker,
            credential=self._credential,
            owner_id=connection.owner_id,
            connection_ref=returned_connection,
            lease_ref=lease_ref,
            external_subject=returned_subject,
            expires_at=expires_at,
        )

    def execute(
        self,
        lease: AtprotoSidecarLease,
        operation: AtprotoSidecarOperation,
        input_data: Mapping[str, Any],
        *,
        now: datetime,
        mutation: bool,
    ) -> Mapping[str, Any]:
        _require_aware(now, "current time")
        if not isinstance(lease, AtprotoSidecarLease) or lease._client_marker is not self._client_marker:
            raise LiveAuthenticationError(
                platform="bluesky",
                code="atproto_sidecar_lease_invalid",
                detail="The AT Protocol sidecar lease is invalid for this client.",
            )
        if not isinstance(operation, AtprotoSidecarOperation):
            raise LivePermissionError(
                platform="bluesky",
                code="atproto_operation_denied",
                detail="Only an allowlisted AT Protocol operation may cross the sidecar boundary.",
            )
        if lease.expires_at <= now:
            raise LiveAuthenticationError(
                platform="bluesky",
                code="atproto_sidecar_lease_expired",
                detail="The AT Protocol sidecar session lease expired.",
            )
        safe_input = _public_json(dict(input_data), field_name="operation input")
        if not isinstance(safe_input, dict):
            raise ValueError("AT Protocol operation input must be an object")
        payload = self._post(
            _EXECUTE_PATH,
            owner_id=lease.owner_id,
            body={
                "lease_ref": lease._lease_ref,
                "operation": operation.value,
                "input": safe_input,
            },
            mutation=mutation,
        )
        if set(payload) != {"operation", "platform", "result"}:
            _raise_invalid_operation_response(mutation)
        if payload.get("platform") != "bluesky" or payload.get("operation") != operation.value:
            _raise_invalid_operation_response(mutation)
        result = payload.get("result")
        if not isinstance(result, dict):
            _raise_invalid_operation_response(mutation)
        return result

    def _post(
        self,
        path: str,
        *,
        owner_id: str,
        body: Mapping[str, Any],
        mutation: bool,
    ) -> dict[str, Any]:
        if path not in _PRIVATE_PATHS:
            raise ValueError("the AT Protocol bridge cannot call arbitrary sidecar routes")
        safe_body = _public_json(dict(body), field_name="sidecar request")
        if not isinstance(safe_body, dict):
            raise ValueError("sidecar request must be an object")
        try:
            response = self._http.request(
                "POST",
                f"{self._base_url}{path}",
                headers=self._credential.headers(owner_id),
                json=safe_body,
            )
        except (httpx.TimeoutException, httpx.NetworkError, TimeoutError, OSError) as exc:
            error_type = RemoteOutcomeUnknown if mutation else LiveTransientError
            raise error_type(
                platform="bluesky",
                code="atproto_sidecar_interrupted",
                detail="The AT Protocol sidecar request ended without a trustworthy result.",
                retryable=not mutation,
                outcome_unknown=mutation,
            ) from exc
        try:
            raw_payload = response.json()
            payload = _public_json(raw_payload, field_name="sidecar response")
            if isinstance(payload, dict):
                self._credential.reject_exposure(payload)
        except (TypeError, ValueError) as exc:
            if response.status_code < 400 and mutation:
                raise RemoteOutcomeUnknown(
                    platform="bluesky",
                    code="atproto_sidecar_response_invalid",
                    detail="The sidecar mutation returned no trustworthy result.",
                    outcome_unknown=True,
                    http_status=response.status_code,
                ) from exc
            raise LiveProtocolError(
                platform="bluesky",
                code="atproto_sidecar_response_invalid",
                detail="The AT Protocol sidecar returned invalid JSON.",
                http_status=response.status_code,
            ) from exc
        if not isinstance(payload, dict):
            if response.status_code < 400 and mutation:
                raise RemoteOutcomeUnknown(
                    platform="bluesky",
                    code="atproto_sidecar_response_invalid",
                    detail="The sidecar mutation returned no trustworthy result.",
                    outcome_unknown=True,
                    http_status=response.status_code,
                )
            raise LiveProtocolError(
                platform="bluesky",
                code="atproto_sidecar_response_invalid",
                detail="The AT Protocol sidecar returned an invalid response.",
                http_status=response.status_code,
            )
        if response.status_code >= 400:
            _raise_sidecar_error(response.status_code, payload, mutation=mutation)
        if response.status_code != 200:
            if mutation:
                raise RemoteOutcomeUnknown(
                    platform="bluesky",
                    code="atproto_sidecar_status_invalid",
                    detail="The sidecar mutation returned no trustworthy HTTP status.",
                    outcome_unknown=True,
                    http_status=response.status_code,
                )
            raise LiveProtocolError(
                platform="bluesky",
                code="atproto_sidecar_status_invalid",
                detail="The AT Protocol sidecar returned an unexpected HTTP status.",
                http_status=response.status_code,
            )
        return payload

    def __repr__(self) -> str:
        return f"AtprotoSidecarClient(base_url={self._base_url!r}, internal_auth=<redacted>)"

    __str__ = __repr__

    def __copy__(self) -> AtprotoSidecarClient:
        raise TypeError("AT Protocol sidecar clients cannot be copied")

    def __deepcopy__(self, _memo: object) -> AtprotoSidecarClient:
        raise TypeError("AT Protocol sidecar clients cannot be copied")

    def __reduce_ex__(self, _protocol: int) -> object:
        raise TypeError("AT Protocol sidecar clients cannot be serialized")


class _NoDirectCredentialProvider:
    def lease(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("AT Protocol credentials must remain inside the official sidecar")


class _NoDirectPlatformHttp:
    def request(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("AT Protocol XRPC must not bypass the official sidecar")


class AtprotoSidecarLiveAdapter(CertifiedLivePlatformAdapter):
    """Journal-compatible Bluesky adapter backed only by curated sidecar operations."""

    PROFILE = BLUESKY_PROFILE
    platform = BLUESKY_PROFILE.platform
    CANDIDATE_ACTIONS = frozenset(
        {
            ActionType.FOLLOW_CREATOR,
            ActionType.UNFOLLOW_CREATOR,
            ActionType.MUTE_CREATOR,
            ActionType.UNMUTE_CREATOR,
            ActionType.MUTE_KEYWORD,
            ActionType.UNMUTE_KEYWORD,
        }
    )
    ACTION_SCOPES: ClassVar[Mapping[ActionType, frozenset[str]]] = {
        action: frozenset() for action in CANDIDATE_ACTIONS
    }
    OBSERVE_SCOPES = frozenset()

    def __init__(
        self,
        *,
        connections: Mapping[str, ConnectedAccount],
        sidecar_client: AtprotoSidecarClient,
        certification: ValidatedLiveCertification | None = None,
        observations: Mapping[str, AccountObservation] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(sidecar_client, AtprotoSidecarClient):
            raise TypeError("an AT Protocol sidecar client is required")
        active: dict[str, ConnectedAccount] = {}
        for connection_id, connection in connections.items():
            if connection.id != connection_id:
                raise ValueError("connected account key must match its id")
            if connection.platform != self.platform:
                raise ValueError("connected account belongs to a different platform")
            _reject_connection_credentials(connection)
            status = str(connection.status)
            if status == "active":
                active[connection_id] = connection
            elif status not in {"reauth_required", "revoking", "revoked"}:
                raise ValueError("connected account status is invalid")
        super().__init__(
            connections=active,
            credential_provider=_NoDirectCredentialProvider(),
            http_client=_NoDirectPlatformHttp(),
            certification=certification,
            observations=observations,
            clock=clock,
        )
        self._sidecar = sidecar_client

    def bind_connection(self, connection: ConnectedAccount) -> None:
        _reject_connection_credentials(connection)
        super().bind_connection(connection)

    def _graph(
        self,
        connection: ConnectedAccount,
        operation: AtprotoSidecarOperation,
        field: str,
        *,
        now: datetime,
        lease: AtprotoSidecarLease | None = None,
    ) -> list[dict[str, Any]]:
        current_lease = lease or self._sidecar.restore(connection, now=now)
        cursor: str | None = None
        seen: set[str] = set()
        output: list[dict[str, Any]] = []
        for _ in range(self.MAX_PAGES):
            input_data: dict[str, Any] = {"limit": 100}
            if cursor is not None:
                input_data["cursor"] = cursor
            payload = self._sidecar.execute(
                current_lease,
                operation,
                input_data,
                now=now,
                mutation=False,
            )
            items = payload.get(field)
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                raise LiveProtocolError(
                    platform=self.platform,
                    code="atproto_graph_response_invalid",
                    detail="The AT Protocol sidecar returned an invalid graph response.",
                )
            for item in items:
                did = item.get("did")
                if not isinstance(did, str) or not _DID_PATTERN.fullmatch(did):
                    raise LiveProtocolError(
                        platform=self.platform,
                        code="atproto_graph_response_invalid",
                        detail="The AT Protocol sidecar returned an invalid graph response.",
                    )
                output.append(dict(item))
            next_cursor = payload.get("cursor")
            if next_cursor is None or next_cursor == "":
                return output
            if (
                not isinstance(next_cursor, str)
                or len(next_cursor) > 2048
                or next_cursor in seen
            ):
                raise LiveProtocolError(
                    platform=self.platform,
                    code="atproto_pagination_invalid",
                    detail="The AT Protocol sidecar returned an invalid pagination cursor.",
                )
            seen.add(next_cursor)
            cursor = next_cursor
        raise LiveProtocolError(
            platform=self.platform,
            code="atproto_pagination_limit",
            detail="The AT Protocol graph exceeded the bounded pagination limit.",
        )

    def _preferences(
        self,
        connection: ConnectedAccount,
        *,
        now: datetime,
        lease: AtprotoSidecarLease | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        current_lease = lease or self._sidecar.restore(connection, now=now)
        payload = self._sidecar.execute(
            current_lease,
            AtprotoSidecarOperation.GET_PREFERENCES,
            {},
            now=now,
            mutation=False,
        )
        preferences = payload.get("preferences")
        digest = payload.get("observed_sha256")
        if (
            not isinstance(preferences, list)
            or len(preferences) > 200
            or not all(isinstance(item, dict) for item in preferences)
            or not isinstance(digest, str)
            or not _DIGEST_PATTERN.fullmatch(digest)
        ):
            raise LiveProtocolError(
                platform=self.platform,
                code="atproto_preferences_response_invalid",
                detail="The AT Protocol sidecar returned invalid preferences.",
            )
        return [copy.deepcopy(item) for item in preferences], digest

    @staticmethod
    def _muted_words(preferences: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for preference in preferences:
            if preference.get("$type") == "app.bsky.actor.defs#mutedWordsPref":
                items = preference.get("items", [])
                if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                    raise LiveProtocolError(
                        platform="bluesky",
                        code="atproto_preferences_response_invalid",
                        detail="The AT Protocol sidecar returned invalid muted-word preferences.",
                    )
                return [dict(item) for item in items]
        return []

    def _observe_controls(
        self,
        connection: ConnectedAccount,
        *,
        now: datetime,
        sample_size: int,
    ) -> AccountObservation:
        del sample_size
        lease = self._sidecar.restore(connection, now=now)
        follows = self._graph(
            connection,
            AtprotoSidecarOperation.GET_FOLLOWS,
            "follows",
            now=now,
            lease=lease,
        )
        mutes = self._graph(
            connection,
            AtprotoSidecarOperation.GET_MUTES,
            "mutes",
            now=now,
            lease=lease,
        )
        preferences, _digest = self._preferences(connection, now=now, lease=lease)
        sample = FeedSample(platform=self.platform, account_id=connection.id, items=(), sampled_at=now)
        return AccountObservation(
            platform=self.platform,
            account_id=connection.id,
            observed_at=now,
            topic_distribution={"unobserved": 1.0},
            followed_creators=frozenset(str(item["did"]) for item in follows),
            muted_creators=frozenset(str(item["did"]) for item in mutes),
            muted_keywords=frozenset(
                str(item["value"])
                for item in self._muted_words(preferences)
                if isinstance(item.get("value"), str)
            ),
            sample=sample,
            confidence=1.0,
        )

    def _read_action_state(
        self,
        connection: ConnectedAccount,
        action: ProposedAction,
        *,
        now: datetime,
    ) -> dict[str, Any]:
        if action.action_type in {ActionType.MUTE_KEYWORD, ActionType.UNMUTE_KEYWORD}:
            keyword = _require_keyword(action.target)
            preferences, digest = self._preferences(connection, now=now)
            item = next(
                (
                    word
                    for word in self._muted_words(preferences)
                    if str(word.get("value", "")).casefold() == keyword.casefold()
                ),
                None,
            )
            return {
                "present": item is not None,
                "target_id": keyword,
                "relationship": "muted_word",
                "remote_ref": str(item.get("id")) if item and item.get("id") else None,
                "preferences": preferences,
                "preferences_sha256": digest,
            }
        target_did = _require_did(action.target.strip())
        muted = action.action_type in {ActionType.MUTE_CREATOR, ActionType.UNMUTE_CREATOR}
        operation = AtprotoSidecarOperation.GET_MUTES if muted else AtprotoSidecarOperation.GET_FOLLOWS
        field = "mutes" if muted else "follows"
        actors = self._graph(connection, operation, field, now=now)
        item = next((candidate for candidate in actors if candidate.get("did") == target_did), None)
        reference: str | None = None
        if item is not None and not muted:
            viewer = item.get("viewer")
            if isinstance(viewer, dict) and isinstance(viewer.get("following"), str):
                reference = str(viewer["following"])
        elif item is not None:
            reference = f"at://{connection.external_subject_id}/app.bsky.graph.mute/{target_did}"
        return {
            "present": item is not None,
            "target_id": target_did,
            "relationship": "mute" if muted else "follow",
            "remote_ref": reference,
        }

    def _desired_state(
        self,
        action: ProposedAction,
        before_state: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return {
            "present": action.action_type
            in {ActionType.FOLLOW_CREATOR, ActionType.MUTE_CREATOR, ActionType.MUTE_KEYWORD},
            "target_id": before_state["target_id"],
            "relationship": before_state["relationship"],
        }

    def _mutate_to_state(
        self,
        connection: ConnectedAccount,
        action: ProposedAction,
        desired_state: Mapping[str, Any],
        *,
        current_state: Mapping[str, Any],
        now: datetime,
    ) -> str | None:
        del action
        relationship = str(desired_state["relationship"])
        target_id = str(desired_state["target_id"])
        present = bool(desired_state["present"])
        lease = self._sidecar.restore(connection, now=now)
        if relationship == "follow":
            if present:
                self._require_active_certification(self._now())
                result = self._sidecar.execute(
                    lease,
                    AtprotoSidecarOperation.FOLLOW,
                    {"actor": _require_did(target_id)},
                    now=now,
                    mutation=True,
                )
                uri = result.get("uri")
                expected_prefix = (
                    f"at://{connection.external_subject_id}/app.bsky.graph.follow/"
                )
                if not isinstance(uri, str) or not uri.startswith(expected_prefix):
                    raise RemoteOutcomeUnknown(
                        platform=self.platform,
                        code="atproto_follow_result_invalid",
                        detail="The follow write returned no trustworthy owned record reference.",
                        outcome_unknown=True,
                    )
                return uri
            uri = current_state.get("remote_ref")
            expected_prefix = f"at://{connection.external_subject_id}/app.bsky.graph.follow/"
            if not isinstance(uri, str) or not uri.startswith(expected_prefix) or len(uri) > 1024:
                raise LiveProtocolError(
                    platform=self.platform,
                    code="atproto_follow_reference_invalid",
                    detail="An owned follow record reference is required for removal.",
                )
            self._require_active_certification(self._now())
            result = self._sidecar.execute(
                lease,
                AtprotoSidecarOperation.DELETE_FOLLOW,
                {"uri": uri},
                now=now,
                mutation=True,
            )
            if result.get("deleted") is not True or result.get("uri") != uri:
                raise RemoteOutcomeUnknown(
                    platform=self.platform,
                    code="atproto_delete_follow_result_invalid",
                    detail="The follow removal returned no trustworthy result.",
                    outcome_unknown=True,
                )
            return uri
        if relationship == "mute":
            operation = AtprotoSidecarOperation.MUTE if present else AtprotoSidecarOperation.UNMUTE
            self._require_active_certification(self._now())
            result = self._sidecar.execute(
                lease,
                operation,
                {"actor": _require_did(target_id)},
                now=now,
                mutation=True,
            )
            if result.get("muted") is not present or result.get("actor") != target_id:
                raise RemoteOutcomeUnknown(
                    platform=self.platform,
                    code="atproto_mute_result_invalid",
                    detail="The actor mute operation returned no trustworthy result.",
                    outcome_unknown=True,
                )
            return f"at://{connection.external_subject_id}/app.bsky.graph.mute/{target_id}"
        if relationship != "muted_word":
            raise ValueError("unsupported AT Protocol relationship")
        preferences = current_state.get("preferences")
        digest = current_state.get("preferences_sha256")
        if (
            not isinstance(preferences, list)
            or not all(isinstance(item, dict) for item in preferences)
            or not isinstance(digest, str)
            or not _DIGEST_PATTERN.fullmatch(digest)
        ):
            raise LiveProtocolError(
                platform=self.platform,
                code="atproto_preferences_baseline_invalid",
                detail="A fresh preference observation is required before changing muted words.",
            )
        updated_preferences = copy.deepcopy(preferences)
        preference = next(
            (
                item
                for item in updated_preferences
                if item.get("$type") == "app.bsky.actor.defs#mutedWordsPref"
            ),
            None,
        )
        if preference is None:
            preference = {"$type": "app.bsky.actor.defs#mutedWordsPref", "items": []}
            updated_preferences.append(preference)
        raw_items = preference.get("items", [])
        if not isinstance(raw_items, list):
            raise LiveProtocolError(
                platform=self.platform,
                code="atproto_preferences_baseline_invalid",
                detail="A fresh preference observation is required before changing muted words.",
            )
        if not all(isinstance(item, dict) for item in raw_items):
            raise LiveProtocolError(
                platform=self.platform,
                code="atproto_preferences_baseline_invalid",
                detail="A fresh preference observation is required before changing muted words.",
            )
        items = [dict(item) for item in raw_items]
        items = [
            item
            for item in items
            if str(item.get("value", "")).casefold() != target_id.casefold()
        ]
        reference: str | None = None
        if present:
            reference = str(uuid4())
            items.append(
                {
                    "$type": "app.bsky.actor.defs#mutedWord",
                    "id": reference,
                    "value": _require_keyword(target_id),
                    "targets": ["content", "tag"],
                    "actorTarget": "all",
                }
            )
        preference["items"] = items
        self._require_active_certification(self._now())
        result = self._sidecar.execute(
            lease,
            AtprotoSidecarOperation.PUT_PREFERENCES,
            {"preferences": updated_preferences, "expected_sha256": digest},
            now=now,
            mutation=True,
        )
        returned_digest = result.get("preferences_sha256")
        if (
            result.get("updated") is not True
            or result.get("external_subject") != connection.external_subject_id
            or not isinstance(returned_digest, str)
            or not _DIGEST_PATTERN.fullmatch(returned_digest)
        ):
            raise RemoteOutcomeUnknown(
                platform=self.platform,
                code="atproto_preferences_result_invalid",
                detail="The preference update returned no trustworthy result.",
                outcome_unknown=True,
            )
        return reference or (
            str(current_state["remote_ref"]) if current_state.get("remote_ref") else None
        )

    @staticmethod
    def _state_matches(observed: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
        return all(
            observed.get(key) == expected.get(key)
            for key in ("present", "target_id", "relationship")
        )


def _validated_base_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(character in value for character in "\r\n"):
        raise ValueError("AT Protocol sidecar base URL is required")
    try:
        parsed = urlsplit(value.strip())
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("AT Protocol sidecar base URL is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("AT Protocol sidecar base URL must be a fixed HTTP(S) origin")
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
        raise ValueError("unencrypted AT Protocol sidecar traffic is allowed only on loopback")
    return value.strip().rstrip("/")


def _is_loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _require_owner(value: object) -> str:
    if not isinstance(value, str) or not _OWNER_PATTERN.fullmatch(value):
        raise ValueError("authenticated AT Protocol connection owner is invalid")
    return value


def _require_opaque(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _OPAQUE_PATTERN.fullmatch(value):
        raise LiveProtocolError(
            platform="bluesky",
            code="atproto_reference_invalid",
            detail=f"The owner-bound AT Protocol {field_name} is invalid.",
        )
    return value


def _require_did(value: object) -> str:
    if not isinstance(value, str) or not _DID_PATTERN.fullmatch(value):
        raise LiveProtocolError(
            platform="bluesky",
            code="atproto_subject_invalid",
            detail="The AT Protocol account identifier is invalid.",
        )
    return value


def _require_keyword(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("muted word must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > 256 or any(ord(character) < 32 for character in normalized):
        raise ValueError("muted word is invalid")
    return normalized


def _require_handle(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("AT Protocol handle is required")
    normalized = value.strip().removeprefix("@").casefold()
    labels = normalized.split(".")
    if (
        not 3 <= len(normalized) <= 253
        or len(labels) < 2
        or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in labels
        )
    ):
        raise ValueError("AT Protocol handle is invalid")
    return normalized


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _parse_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise LiveProtocolError(
            platform="bluesky",
            code="atproto_timestamp_invalid",
            detail=f"The AT Protocol {field_name} is invalid.",
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _require_aware(parsed, field_name)
    except ValueError as exc:
        raise LiveProtocolError(
            platform="bluesky",
            code="atproto_timestamp_invalid",
            detail=f"The AT Protocol {field_name} is invalid.",
        ) from exc
    return parsed


def _public_json(value: Any, *, field_name: str, _depth: int = 0) -> Any:
    if _depth > 32:
        raise ValueError(f"{field_name} is too deeply nested")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} contains a non-finite number")
        return value
    if isinstance(value, list):
        return [_public_json(item, field_name=field_name, _depth=_depth + 1) for item in value]
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or _FORBIDDEN_FIELD_PATTERN.fullmatch(key):
                raise ValueError(f"{field_name} contains forbidden credential material")
            output[key] = _public_json(item, field_name=field_name, _depth=_depth + 1)
        if _depth == 0:
            encoded = json.dumps(output, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
            if len(encoded.encode("utf-8")) > 1_000_000:
                raise ValueError(f"{field_name} is too large")
        return output
    raise ValueError(f"{field_name} contains an unsupported value")


def _raise_invalid_operation_response(mutation: bool) -> None:
    if mutation:
        raise RemoteOutcomeUnknown(
            platform="bluesky",
            code="atproto_operation_response_invalid",
            detail="The sidecar mutation returned no trustworthy operation result.",
            outcome_unknown=True,
        )
    raise LiveProtocolError(
        platform="bluesky",
        code="atproto_operation_response_invalid",
        detail="The AT Protocol sidecar returned an invalid operation result.",
    )


def _raise_sidecar_error(status: int, payload: Mapping[str, Any], *, mutation: bool) -> None:
    raw_code = payload.get("error")
    code = raw_code if isinstance(raw_code, str) and _ERROR_CODE_PATTERN.fullmatch(raw_code) else "error"
    public_code = f"atproto_sidecar_{code}"
    if code in _OAUTH_CALLBACK_CODES:
        raise LiveProtocolError(
            platform="bluesky",
            code=public_code,
            detail="The AT Protocol OAuth callback cannot be completed; start authorization again.",
            http_status=status,
        )
    if code in _AUTHENTICATION_CODES or status == 401:
        raise LiveAuthenticationError(
            platform="bluesky",
            code=public_code,
            detail="The owner-bound AT Protocol sidecar session is unavailable.",
            http_status=status,
        )
    if status == 403:
        raise LivePermissionError(
            platform="bluesky",
            code=public_code,
            detail="The AT Protocol sidecar denied the requested operation.",
            http_status=status,
        )
    if status == 404:
        raise LiveTargetNotFound(
            platform="bluesky",
            code=public_code,
            detail="The owner-bound AT Protocol resource was not found.",
            http_status=status,
        )
    if code == "preferences_changed":
        raise LiveTransientError(
            platform="bluesky",
            code=public_code,
            detail="The AT Protocol state changed; prepare the action again from a fresh observation.",
            retryable=True,
            http_status=status,
        )
    if status == 409:
        raise LiveProtocolError(
            platform="bluesky",
            code=public_code,
            detail="The AT Protocol sidecar rejected a conflicting request.",
            http_status=status,
        )
    if status == 429:
        raise LiveRateLimited(
            platform="bluesky",
            code=public_code,
            detail="The AT Protocol sidecar is rate limited.",
            retryable=True,
            http_status=status,
        )
    if status >= 500:
        error_type = RemoteOutcomeUnknown if mutation else LiveTransientError
        raise error_type(
            platform="bluesky",
            code=public_code,
            detail="The AT Protocol sidecar did not return a trustworthy result.",
            retryable=not mutation,
            outcome_unknown=mutation,
            http_status=status,
        )
    raise LiveProtocolError(
        platform="bluesky",
        code=public_code,
        detail="The AT Protocol sidecar rejected the request.",
        http_status=status,
    )


def _reject_connection_credentials(connection: ConnectedAccount) -> None:
    metadata = getattr(connection, "metadata", {})
    if isinstance(metadata, Mapping):
        _public_json(dict(metadata), field_name="connection metadata")
    records = (getattr(connection, "__dict__", {}), getattr(type(connection), "__dict__", {}))
    for record in records:
        if isinstance(record, Mapping):
            for field_name in record:
                if isinstance(field_name, str) and _FORBIDDEN_FIELD_PATTERN.fullmatch(field_name):
                    raise ValueError("AT Protocol connections must be token-free")


# A descriptive alias for callers that organize adapters by social platform.
BlueskySidecarLiveAdapter = AtprotoSidecarLiveAdapter
