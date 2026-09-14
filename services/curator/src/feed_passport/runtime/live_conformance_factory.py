from __future__ import annotations

import base64
import binascii
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from feed_passport.adapters.platforms.live import (
    RedditLiveAdapter,
    XLiveAdapter,
    YouTubeLiveAdapter,
)
from feed_passport.application.oauth import OAuthFlowError, OAuthProviderCatalog
from feed_passport.domain.connections import ConnectionStatus, ExternalConnection
from feed_passport.domain.models import (
    ActionType,
    CapabilityLevel,
    PlatformCapabilityManifest,
)
from feed_passport.infrastructure.connection_registry import EncryptedConnectionRegistry
from feed_passport.infrastructure.crypto import AesGcmKeyring
from feed_passport.infrastructure.http_client import HttpxNoAmbientClient
from feed_passport.infrastructure.oauth_vault import LocalEncryptedOAuthVault
from feed_passport.infrastructure.sqlite_store import SQLiteStore
from feed_passport.ports.credentials import CredentialError
from feed_passport.ports.live_platform import HttpClient

from .live_conformance import (
    LiveConformanceGateError,
    LiveConformanceRequest,
    LiveConformanceRuntime,
    verify_clean_checkout,
)


_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ATPROTO_REQUIRED_SCOPES = frozenset({"atproto"})
_STANDARD_PLATFORMS = frozenset({"youtube", "x", "reddit"})


class BuiltInLiveConformanceFactory:
    """Restore the trusted local boundaries used by an explicitly gated proof.

    The factory does not run OAuth, contact a social platform, or execute an
    action.  It opens an explicitly named existing SQLite database, decrypts
    only the requested owner-scoped connection, verifies its credential and
    scope binding, and builds an exact-action candidate adapter.  Candidate
    capability exists only in this module so the ordinary service bootstrap
    still requires a signed, validated live certification.
    """

    def __init__(
        self,
        *,
        http_client_factory: Callable[[float], HttpClient] | None = None,
        clock: Callable[[], datetime] | None = None,
        checkout_verifier: Callable[[LiveConformanceRequest], None] | None = None,
    ) -> None:
        self._http_client_factory = http_client_factory or (
            lambda timeout: HttpxNoAmbientClient(timeout_seconds=timeout)
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._checkout_verifier = checkout_verifier or verify_clean_checkout

    def __call__(self, request: LiveConformanceRequest) -> LiveConformanceRuntime:
        self._preflight_request(request)
        database_path = self._existing_database_path()
        connection_key = self._external_key("FEED_PASSPORT_CONNECTION_KEY_B64")
        index_key = self._external_key("FEED_PASSPORT_CONNECTION_INDEX_KEY_B64")
        connection_key_id = self._key_id("FEED_PASSPORT_CONNECTION_KEY_ID")
        timeout = self._timeout()

        store = SQLiteStore(database_path)
        http_client: HttpClient | None = None
        try:
            registry = EncryptedConnectionRegistry(
                store,
                AesGcmKeyring(
                    active_key_id=connection_key_id,
                    keys={connection_key_id: connection_key},
                    index_key=index_key,
                ),
            )
            try:
                connection = registry.get_connection(
                    request.connection_id,
                    owner_id=request.owner_id,
                )
            except KeyError as exc:
                raise LiveConformanceGateError(
                    "owner-bound encrypted connection was not found"
                ) from exc
            self._validate_connection(request, connection)
            http_client = self._http_client_factory(timeout)
            if not callable(getattr(http_client, "request", None)):
                raise LiveConformanceGateError(
                    "live conformance requires an injected no-ambient HTTP client"
                )
            if request.platform in _STANDARD_PLATFORMS:
                adapter = self._standard_adapter(
                    request,
                    connection,
                    registry=registry,
                    store=store,
                    http_client=http_client,
                )
            else:
                adapter = self._atproto_adapter(
                    request,
                    connection,
                    http_client=http_client,
                )
        except Exception:
            _close_if_supported(http_client)
            store.close()
            raise

        return LiveConformanceRuntime(
            adapter=adapter,
            journal=store,
            close_callbacks=(
                lambda: _close_if_supported(http_client),
                store.close,
            ),
        )

    def _standard_adapter(
        self,
        request: LiveConformanceRequest,
        connection: ExternalConnection,
        *,
        registry: EncryptedConnectionRegistry,
        store: SQLiteStore,
        http_client: HttpClient,
    ) -> Any:
        vault_key = self._external_key("FEED_PASSPORT_OAUTH_VAULT_KEY_B64")
        vault_key_id = self._key_id("FEED_PASSPORT_OAUTH_VAULT_KEY_ID")
        try:
            providers = OAuthProviderCatalog.from_env()
            provider = providers.get(request.platform)
        except (OAuthFlowError, ValueError) as exc:
            raise LiveConformanceGateError(
                "the requested platform OAuth client registration is incomplete"
            ) from exc
        action_types = _action_types(request)
        required_scopes = _required_scopes(request.platform, action_types)
        if not required_scopes <= provider.scopes:
            raise LiveConformanceGateError(
                "OAuth client registration does not cover the exact conformance action subset"
            )
        if not required_scopes <= connection.granted_scopes:
            raise LiveConformanceGateError(
                "owner-bound connection does not grant the exact conformance action scopes"
            )
        vault = LocalEncryptedOAuthVault(
            store,
            encryption_key=vault_key,
            key_id=vault_key_id,
            providers=providers,
            http_client=http_client,
        )
        try:
            lease = vault.lease(
                connection,
                required_scopes=required_scopes,
                now=_aware_now(self._clock),
            )
        except CredentialError as exc:
            raise LiveConformanceGateError(
                "encrypted OAuth credential is unavailable or does not grant the exact action scopes"
            ) from exc
        if lease.credential_ref != connection.credential_ref or not required_scopes <= lease.scopes:
            raise LiveConformanceGateError("encrypted OAuth credential binding did not verify")
        del lease

        classes = {
            "youtube": _YouTubeConformanceAdapter,
            "x": _XConformanceAdapter,
            "reddit": _RedditConformanceAdapter,
        }

        def mark_reauth_required(
            current: ExternalConnection,
            now: datetime,
        ) -> ExternalConnection:
            return registry.mark_reauth_required(
                current.id,
                owner_id=current.owner_id,
                expected_credential_ref=current.credential_ref,
                now=now,
            )

        arguments: dict[str, Any] = {
            "conformance_actions": action_types,
            "connections": {connection.id: connection},
            "credential_provider": vault,
            "http_client": http_client,
            "mark_reauth_required": mark_reauth_required,
        }
        if request.platform == "reddit":
            arguments["approval_verified"] = True
            arguments["user_agent"] = provider.user_agent
        return classes[request.platform](**arguments)

    def _atproto_adapter(
        self,
        request: LiveConformanceRequest,
        connection: ExternalConnection,
        *,
        http_client: HttpClient,
    ) -> Any:
        if not _ATPROTO_REQUIRED_SCOPES <= connection.granted_scopes:
            raise LiveConformanceGateError(
                "owner-bound Bluesky connection does not record the required AT Protocol scope"
            )
        sidecar_url = self._required("FEED_PASSPORT_ATPROTO_SIDECAR_URL")
        sidecar_secret = self._required("FEED_PASSPORT_ATPROTO_SIDECAR_INTERNAL_SECRET")
        if not 32 <= len(sidecar_secret) <= 4096 or any(
            character in sidecar_secret for character in "\r\n"
        ):
            raise LiveConformanceGateError(
                "AT Protocol sidecar internal secret must contain 32 to 4096 safe characters"
            )
        try:
            from feed_passport.adapters.platforms.live.atproto_sidecar import (
                AtprotoSidecarClient,
                AtprotoSidecarLiveAdapter,
            )
        except ImportError as exc:
            raise LiveConformanceGateError(
                "the token-isolated AT Protocol sidecar bridge is not installed"
            ) from exc
        sidecar_client = AtprotoSidecarClient(
            base_url=sidecar_url,
            internal_service_secret=sidecar_secret,
            http_client=http_client,
        )

        class ExactAtprotoConformanceAdapter(
            _ExactConformanceCapabilityMixin,
            AtprotoSidecarLiveAdapter,
        ):
            pass

        return ExactAtprotoConformanceAdapter(
            conformance_actions=_action_types(request),
            connections={connection.id: connection},
            sidecar_client=sidecar_client,
        )

    def _preflight_request(self, request: LiveConformanceRequest) -> None:
        if not isinstance(request, LiveConformanceRequest):
            raise TypeError("built-in live conformance factory requires a validated request")
        if not request.acknowledge_authorized_dummy_account_mutations:
            raise LiveConformanceGateError(
                "explicit acknowledgement of authorized dummy-account mutations is required"
            )
        if request.output_path.exists():
            raise LiveConformanceGateError(
                "certification output already exists; refusing to initialize a live runtime"
            )
        self._checkout_verifier(request)

    def _existing_database_path(self) -> Path:
        raw = self._required("FEED_PASSPORT_DB_PATH")
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            raise LiveConformanceGateError(
                "FEED_PASSPORT_DB_PATH must name the existing owner-bound connection database"
            )
        return path

    def _external_key(self, name: str) -> bytes:
        encoded = self._required(name)
        try:
            padded = encoded + "=" * (-len(encoded) % 4)
            value = base64.b64decode(padded, altchars=b"-_", validate=True)
        except (ValueError, binascii.Error) as exc:
            raise LiveConformanceGateError(f"{name} must be URL-safe base64") from exc
        if len(value) != 32:
            raise LiveConformanceGateError(f"{name} must decode to exactly 32 bytes")
        return value

    def _key_id(self, name: str) -> str:
        value = self._required(name)
        if not _KEY_ID.fullmatch(value):
            raise LiveConformanceGateError(f"{name} is invalid")
        return value

    def _timeout(self) -> float:
        raw = os.environ.get("FEED_PASSPORT_PLATFORM_HTTP_TIMEOUT_SECONDS", "20").strip()
        try:
            value = float(raw)
        except ValueError as exc:
            raise LiveConformanceGateError(
                "FEED_PASSPORT_PLATFORM_HTTP_TIMEOUT_SECONDS is invalid"
            ) from exc
        if not 0 < value <= 120:
            raise LiveConformanceGateError(
                "FEED_PASSPORT_PLATFORM_HTTP_TIMEOUT_SECONDS must be between 0 and 120"
            )
        return value

    def _required(self, name: str) -> str:
        value = os.environ.get(name, "").strip()
        if not value or "\x00" in value:
            raise LiveConformanceGateError(f"required external configuration is missing: {name}")
        return value

    @staticmethod
    def _validate_connection(
        request: LiveConformanceRequest,
        connection: ExternalConnection,
    ) -> None:
        if (
            connection.id != request.connection_id
            or connection.owner_id != request.owner_id
            or connection.platform != request.platform
        ):
            raise LiveConformanceGateError(
                "encrypted connection does not match the requested owner and platform"
            )
        if connection.status is not ConnectionStatus.ACTIVE:
            raise LiveConformanceGateError("owner-bound connection is not active")
        if not connection.external_subject.strip() or not connection.credential_ref.strip():
            raise LiveConformanceGateError("owner-bound connection binding is incomplete")


class _ExactConformanceCapabilityMixin:
    """Ephemeral exact-action grant used only while producing its own proof."""

    def __init__(
        self,
        *,
        conformance_actions: frozenset[ActionType],
        **kwargs: Any,
    ) -> None:
        actions = frozenset(conformance_actions)
        candidates = self.CANDIDATE_ACTIONS or frozenset()
        if not actions or not actions <= candidates:
            raise LiveConformanceGateError(
                "candidate adapter actions do not match its exact supported surface"
            )
        self._conformance_actions = actions
        if kwargs.pop("certification", None) is not None:
            raise LiveConformanceGateError(
                "conformance candidate adapters cannot accept a promotion certification"
            )
        super().__init__(certification=None, **kwargs)

    def capabilities(self, account_id: str) -> PlatformCapabilityManifest:
        connection = self._connection(account_id)
        required_scopes = _required_scopes(self.platform, self._conformance_actions)
        if self.platform == "bluesky":
            required_scopes = _ATPROTO_REQUIRED_SCOPES
        if not required_scopes <= frozenset(connection.granted_scopes):
            raise LiveConformanceGateError(
                "connection scopes changed after the exact conformance gate"
            )
        return PlatformCapabilityManifest(
            platform=self.platform,
            level=CapabilityLevel.EXECUTABLE,
            observe=frozenset(),
            execute=self._conformance_actions,
            verify=frozenset(
                {"candidate_post_write_reconciliation", "candidate_verified_reverse_rollback"}
            ),
            rollback=self._conformance_actions,
            requires_user_handoff=self.PROFILE.native_handoff_actions - self._conformance_actions,
            evidence_url=None,
            certified_at=None,
        )

    def _capabilities_at(
        self,
        account_id: str,
        *,
        now: datetime,
    ) -> PlatformCapabilityManifest:
        self._require_aware_now(now)
        return self.capabilities(account_id)

    def _require_active_certification(self, now: datetime) -> None:
        """Use the factory gate as authority while producing its own proof."""

        self._require_aware_now(now)

    def _validate_prepared(
        self,
        prepared: Any,
        *,
        now: datetime,
        require_current_certification: bool,
    ) -> None:
        del require_current_certification
        self._require_aware_now(now)
        if prepared.platform != self.platform:
            raise ValueError("prepared action belongs to a different platform")
        connection = self._connection(prepared.connection_id)
        required_scopes = self.ACTION_SCOPES.get(
            prepared.action.action_type,
            _ATPROTO_REQUIRED_SCOPES if self.platform == "bluesky" else frozenset(),
        )
        if (
            prepared.action.action_type not in self._conformance_actions
            or not required_scopes <= frozenset(connection.granted_scopes)
        ):
            raise LiveConformanceGateError(
                "prepared action is outside the exact conformance gate"
            )


class _YouTubeConformanceAdapter(_ExactConformanceCapabilityMixin, YouTubeLiveAdapter):
    pass


class _XConformanceAdapter(_ExactConformanceCapabilityMixin, XLiveAdapter):
    pass


class _RedditConformanceAdapter(_ExactConformanceCapabilityMixin, RedditLiveAdapter):
    pass


def _required_scopes(
    platform: str,
    actions: frozenset[ActionType],
) -> frozenset[str]:
    classes = {
        "youtube": YouTubeLiveAdapter,
        "x": XLiveAdapter,
        "reddit": RedditLiveAdapter,
    }
    if platform == "bluesky":
        return _ATPROTO_REQUIRED_SCOPES
    try:
        adapter_class = classes[platform]
    except KeyError as exc:
        raise LiveConformanceGateError("unsupported built-in live conformance platform") from exc
    scopes: set[str] = set()
    for action in actions:
        try:
            scopes.update(adapter_class.ACTION_SCOPES[action])
        except KeyError as exc:
            raise LiveConformanceGateError(
                "action subset exceeds the built-in platform adapter"
            ) from exc
    return frozenset(scopes)


def _action_types(request: LiveConformanceRequest) -> frozenset[ActionType]:
    return frozenset(action.action_type for action in request.actions)


def _aware_now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("built-in live conformance clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def _close_if_supported(value: object | None) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()


def build_builtin_live_conformance_runtime(
    request: LiveConformanceRequest,
) -> LiveConformanceRuntime:
    """Dynamic CLI entry point; still inert until the caller passes every gate."""

    return BuiltInLiveConformanceFactory()(request)
