from __future__ import annotations

import base64
import json
import secrets
import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from feed_passport.domain.connections import ConnectionStatus, ExternalConnection
from feed_passport.ports.credentials import (
    CredentialScopeDenied,
    CredentialUnavailable,
    OAuthCredentialLease,
)
from feed_passport.ports.live_platform import HttpClient
from feed_passport.ports.oauth import (
    OAuthClientAuthentication,
    OAuthProviderRegistry,
)

from .sqlite_store import ConcurrencyConflict, SQLiteStore


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _aad(credential_ref: str, owner_id: str, platform: str, version: int) -> bytes:
    return json.dumps(
        {
            "kind": "oauth_credential",
            "credential_ref": credential_ref,
            "owner_id": owner_id,
            "platform": platform,
            "version": version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class LocalEncryptedOAuthVault:
    """Local credential broker for development and authorized dummy accounts.

    Tokens are encrypted with an externally supplied AES-256-GCM key and bound
    to owner, platform, reference, and version through associated data. The key
    is never written to SQLite. Production deployment should use AgentCore
    Identity instead of this local implementation.
    """

    def __init__(
        self,
        store: SQLiteStore,
        *,
        encryption_key: bytes,
        key_id: str,
        providers: OAuthProviderRegistry,
        http_client: HttpClient,
        id_factory: Callable[[], str] | None = None,
        refresh_skew: timedelta = timedelta(minutes=2),
    ) -> None:
        if len(encryption_key) != 32 or not key_id.strip():
            raise ValueError("the OAuth vault requires a named 32-byte external key")
        if refresh_skew < timedelta(0) or refresh_skew > timedelta(minutes=10):
            raise ValueError("OAuth refresh skew must be between zero and ten minutes")
        self.store = store
        self._cipher = AESGCM(bytes(encryption_key))
        self._key_id = key_id
        self._providers = providers
        self._http = http_client
        self._id_factory = id_factory or (lambda: f"credential_{secrets.token_urlsafe(24)}")
        self._refresh_skew = refresh_skew
        self._initialize_schema()

    def put(
        self,
        *,
        owner_id: str,
        platform: str,
        token_response: Mapping[str, Any],
        granted_scopes: frozenset[str],
        now: datetime,
        resource_server: str | None = None,
    ) -> str:
        canonical_now = _aware(now, "now")
        if not owner_id.strip() or not platform.strip() or not granted_scopes:
            raise ValueError("OAuth credential owner, platform, and scopes are required")
        credential_ref = self._id_factory()
        if not credential_ref.strip():
            raise ValueError("credential reference factory returned an empty value")
        payload = self._normalize_token_response(
            token_response,
            granted_scopes=granted_scopes,
            now=canonical_now,
            resource_server=resource_server,
        )
        version = 1
        nonce, ciphertext = self._encrypt(
            credential_ref,
            owner_id,
            platform,
            version,
            payload,
        )
        try:
            with self.store.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO oauth_credentials (
                        credential_ref, owner_id, platform, key_id, nonce,
                        ciphertext, version, created_at, updated_at, revoked_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        credential_ref,
                        owner_id,
                        platform,
                        self._key_id,
                        nonce,
                        ciphertext,
                        version,
                        canonical_now.isoformat(),
                        canonical_now.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ConcurrencyConflict("OAuth credential reference already exists") from exc
        return credential_ref

    def lease(
        self,
        connection: ExternalConnection,
        *,
        required_scopes: frozenset[str],
        now: datetime,
        force_refresh: bool = False,
    ) -> OAuthCredentialLease:
        canonical_now = _aware(now, "now")
        if connection.status is not ConnectionStatus.ACTIVE:
            raise CredentialUnavailable("connection_inactive", "The connected account is not active.")
        row, payload = self._load(
            connection.credential_ref,
            owner_id=connection.owner_id,
            platform=connection.platform,
        )
        scopes = frozenset(str(value) for value in payload.get("scopes", ()))
        if not required_scopes <= scopes:
            raise CredentialScopeDenied(
                "credential_scope_denied",
                "The connected account did not grant every required scope.",
            )
        expires_at = datetime.fromisoformat(str(payload["expires_at"]))
        if force_refresh or expires_at <= canonical_now + self._refresh_skew:
            payload = self._refresh(row, payload, now=canonical_now)
            scopes = frozenset(str(value) for value in payload.get("scopes", ()))
            expires_at = datetime.fromisoformat(str(payload["expires_at"]))
        if expires_at <= canonical_now:
            raise CredentialUnavailable("credential_expired", "The connected account credential expired.")
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise CredentialUnavailable("credential_invalid", "The credential vault record is invalid.")
        return OAuthCredentialLease(
            access_token=access_token,
            credential_ref=connection.credential_ref,
            scopes=scopes,
            expires_at=expires_at,
            resource_server=(
                str(payload["resource_server"])
                if payload.get("resource_server") is not None
                else None
            ),
        )

    def revoke(self, connection: ExternalConnection, *, now: datetime) -> None:
        canonical_now = _aware(now, "now")
        if self._revocation_confirmed(connection):
            return
        _row, payload = self._load(
            connection.credential_ref,
            owner_id=connection.owner_id,
            platform=connection.platform,
        )
        provider = self._providers.get(connection.platform)
        if provider.revoke_url:
            token = payload.get("refresh_token") or payload.get("access_token")
            if not isinstance(token, str) or not token:
                raise CredentialUnavailable("credential_invalid", "The credential vault record is invalid.")
            data: dict[str, Any] = {"token": token, "client_id": provider.client_id}
            headers = self._provider_headers(provider)
            self._attach_client_auth(provider, data, headers)
            try:
                response = self._http.request(
                    "POST",
                    provider.revoke_url,
                    headers=headers,
                    data=data,
                )
            except (httpx.TimeoutException, httpx.NetworkError, TimeoutError, OSError) as exc:
                raise CredentialUnavailable(
                    "revocation_interrupted",
                    "The platform credential revocation could not be confirmed.",
                ) from exc
            if response.status_code >= 500 or response.status_code == 429:
                raise CredentialUnavailable(
                    "revocation_unavailable",
                    "The platform credential revocation could not be confirmed.",
                )
            if response.status_code >= 400:
                raise CredentialUnavailable(
                    "revocation_rejected",
                    "The platform rejected credential revocation.",
                )
        self._complete_revocation(connection, now=canonical_now)

    def discard(self, credential_ref: str, *, owner_id: str) -> None:
        if not credential_ref.strip() or not owner_id.strip():
            raise ValueError("credential reference and owner are required")
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM oauth_credentials WHERE credential_ref = ? AND owner_id = ?",
                (credential_ref, owner_id),
            )
            if cursor.rowcount not in {0, 1}:
                raise RuntimeError("unexpected OAuth credential deletion count")

    def _revocation_confirmed(self, connection: ExternalConnection) -> bool:
        with self.store.transaction() as database:
            row = database.execute(
                """
                SELECT owner_id, platform, credential_ref
                FROM oauth_revocation_receipts
                WHERE connection_id = ?
                """,
                (connection.id,),
            ).fetchone()
        if row is None:
            return False
        if (
            row["owner_id"] != connection.owner_id
            or row["platform"] != connection.platform
            or row["credential_ref"] != connection.credential_ref
        ):
            raise CredentialUnavailable(
                "revocation_receipt_invalid",
                "The credential revocation receipt does not match this connected account.",
            )
        return True

    def _complete_revocation(self, connection: ExternalConnection, *, now: datetime) -> None:
        with self.store.transaction() as database:
            existing = database.execute(
                """
                SELECT owner_id, platform, credential_ref
                FROM oauth_revocation_receipts
                WHERE connection_id = ?
                """,
                (connection.id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["owner_id"] != connection.owner_id
                    or existing["platform"] != connection.platform
                    or existing["credential_ref"] != connection.credential_ref
                ):
                    raise CredentialUnavailable(
                        "revocation_receipt_invalid",
                        "The credential revocation receipt does not match this connected account.",
                    )
                return
            database.execute(
                """
                INSERT INTO oauth_revocation_receipts (
                    connection_id, owner_id, platform, credential_ref, confirmed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    connection.id,
                    connection.owner_id,
                    connection.platform,
                    connection.credential_ref,
                    now.isoformat(),
                ),
            )
            cursor = database.execute(
                """
                DELETE FROM oauth_credentials
                WHERE credential_ref = ? AND owner_id = ? AND platform = ?
                """,
                (connection.credential_ref, connection.owner_id, connection.platform),
            )
            if cursor.rowcount not in {0, 1}:
                raise RuntimeError("unexpected OAuth credential deletion count")

    def _refresh(
        self,
        row: sqlite3.Row,
        current: Mapping[str, Any],
        *,
        now: datetime,
    ) -> dict[str, Any]:
        refresh_token = current.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise CredentialUnavailable(
                "refresh_unavailable",
                "The connected account requires authorization again.",
            )
        provider = self._providers.get(str(row["platform"]))
        data: dict[str, Any] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": provider.client_id,
        }
        headers = self._provider_headers(provider)
        self._attach_client_auth(provider, data, headers)
        try:
            response = self._http.request(
                "POST",
                provider.token_url,
                headers=headers,
                data=data,
            )
        except (httpx.TimeoutException, httpx.NetworkError, TimeoutError, OSError) as exc:
            raise CredentialUnavailable(
                "refresh_interrupted",
                "The connected account credential could not be refreshed.",
            ) from exc
        if response.status_code >= 400:
            raise CredentialUnavailable(
                "refresh_rejected",
                "The connected account requires authorization again.",
            )
        try:
            value = response.json()
        except (TypeError, ValueError) as exc:
            raise CredentialUnavailable(
                "refresh_invalid",
                "The platform returned an invalid credential response.",
            ) from exc
        if not isinstance(value, dict):
            raise CredentialUnavailable(
                "refresh_invalid",
                "The platform returned an invalid credential response.",
            )
        scopes = self._scopes_from_response(value, fallback=current.get("scopes", ()))
        merged = dict(value)
        merged.setdefault("refresh_token", refresh_token)
        normalized = self._normalize_token_response(
            merged,
            granted_scopes=scopes,
            now=now,
            resource_server=(
                str(current["resource_server"])
                if current.get("resource_server") is not None
                else provider.resource_server
            ),
        )
        expected_version = int(row["version"])
        next_version = expected_version + 1
        nonce, ciphertext = self._encrypt(
            str(row["credential_ref"]),
            str(row["owner_id"]),
            str(row["platform"]),
            next_version,
            normalized,
        )
        with self.store.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE oauth_credentials SET
                    key_id = ?, nonce = ?, ciphertext = ?, version = ?, updated_at = ?
                WHERE credential_ref = ? AND owner_id = ? AND version = ? AND revoked_at IS NULL
                """,
                (
                    self._key_id,
                    nonce,
                    ciphertext,
                    next_version,
                    now.isoformat(),
                    row["credential_ref"],
                    row["owner_id"],
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyConflict("OAuth credential changed during refresh")
        return normalized

    def _load(
        self,
        credential_ref: str,
        *,
        owner_id: str,
        platform: str,
    ) -> tuple[sqlite3.Row, dict[str, Any]]:
        with self.store.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM oauth_credentials
                WHERE credential_ref = ? AND owner_id = ? AND platform = ? AND revoked_at IS NULL
                """,
                (credential_ref, owner_id, platform),
            ).fetchone()
        if row is None:
            raise CredentialUnavailable(
                "credential_unavailable",
                "No active credential exists for this connected account.",
            )
        if row["key_id"] != self._key_id:
            raise CredentialUnavailable(
                "credential_key_unavailable",
                "The credential encryption key is unavailable.",
            )
        try:
            plaintext = self._cipher.decrypt(
                bytes(row["nonce"]),
                bytes(row["ciphertext"]),
                _aad(credential_ref, owner_id, platform, int(row["version"])),
            )
            value = json.loads(plaintext)
        except (InvalidTag, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CredentialUnavailable(
                "credential_authentication_failed",
                "The credential vault record failed authentication.",
            ) from exc
        if not isinstance(value, dict):
            raise CredentialUnavailable("credential_invalid", "The credential vault record is invalid.")
        return row, value

    def _encrypt(
        self,
        credential_ref: str,
        owner_id: str,
        platform: str,
        version: int,
        payload: Mapping[str, Any],
    ) -> tuple[bytes, bytes]:
        plaintext = json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        nonce = secrets.token_bytes(12)
        ciphertext = self._cipher.encrypt(
            nonce,
            plaintext,
            _aad(credential_ref, owner_id, platform, version),
        )
        return nonce, ciphertext

    @staticmethod
    def _normalize_token_response(
        value: Mapping[str, Any],
        *,
        granted_scopes: frozenset[str],
        now: datetime,
        resource_server: str | None,
    ) -> dict[str, Any]:
        access_token = value.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise CredentialUnavailable(
                "token_response_invalid",
                "The platform returned an invalid credential response.",
            )
        if "\r" in access_token or "\n" in access_token:
            raise CredentialUnavailable(
                "token_response_invalid",
                "The platform returned an invalid credential response.",
            )
        token_type = str(value.get("token_type", "Bearer"))
        if token_type.casefold() != "bearer":
            raise CredentialUnavailable(
                "token_type_unsupported",
                "The platform returned an unsupported credential type.",
            )
        try:
            expires_in = int(value.get("expires_in", 3600))
        except (TypeError, ValueError) as exc:
            raise CredentialUnavailable(
                "token_response_invalid",
                "The platform returned an invalid credential lifetime.",
            ) from exc
        if expires_in < 30 or expires_in > 31_536_000:
            raise CredentialUnavailable(
                "token_response_invalid",
                "The platform returned an invalid credential lifetime.",
            )
        refresh_token = value.get("refresh_token")
        if refresh_token is not None and (
            not isinstance(refresh_token, str)
            or not refresh_token
            or "\r" in refresh_token
            or "\n" in refresh_token
        ):
            raise CredentialUnavailable(
                "token_response_invalid",
                "The platform returned an invalid refresh credential.",
            )
        if resource_server is not None and not resource_server.startswith("https://"):
            raise ValueError("OAuth resource servers must use HTTPS")
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_at": (now + timedelta(seconds=expires_in)).isoformat(),
            "scopes": sorted(granted_scopes),
            "resource_server": resource_server,
        }

    @staticmethod
    def _scopes_from_response(
        value: Mapping[str, Any],
        *,
        fallback: Any,
    ) -> frozenset[str]:
        raw = value.get("scope")
        if isinstance(raw, str):
            scopes = frozenset(item for item in raw.split() if item)
        elif isinstance(raw, (list, tuple, set, frozenset)):
            scopes = frozenset(str(item) for item in raw if str(item).strip())
        else:
            scopes = frozenset(str(item) for item in fallback if str(item).strip())
        if not scopes:
            raise CredentialUnavailable(
                "token_scope_invalid",
                "The platform returned no granted OAuth scopes.",
            )
        return scopes

    @staticmethod
    def _attach_client_auth(provider: Any, data: dict[str, Any], headers: dict[str, str]) -> None:
        if provider.client_authentication is OAuthClientAuthentication.NONE:
            return
        secret = provider.client_secret
        if not isinstance(secret, str) or not secret:
            raise CredentialUnavailable(
                "oauth_client_unavailable",
                "The OAuth client registration is incomplete.",
            )
        if provider.client_authentication is OAuthClientAuthentication.CLIENT_SECRET_POST:
            data["client_secret"] = secret
            return
        encoded = base64.b64encode(f"{provider.client_id}:{secret}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {encoded}"

    @staticmethod
    def _provider_headers(provider: Any) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if provider.user_agent is not None:
            headers["User-Agent"] = provider.user_agent
        return headers

    def _initialize_schema(self) -> None:
        with self.store.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS oauth_credentials (
                    credential_ref TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    key_id TEXT NOT NULL,
                    nonce BLOB NOT NULL,
                    ciphertext BLOB NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revoked_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_oauth_credentials_owner
                ON oauth_credentials(owner_id, platform)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS oauth_revocation_receipts (
                    connection_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    credential_ref TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_oauth_revocation_receipts_owner
                ON oauth_revocation_receipts(owner_id, platform)
                """
            )
