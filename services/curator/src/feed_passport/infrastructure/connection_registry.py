from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from feed_passport.domain.connections import (
    ConnectionStatus,
    ExternalConnection,
    OAuthTransaction,
    OAuthTransactionStart,
)
from feed_passport.infrastructure.crypto import (
    AesGcmKeyring,
    EncryptedJson,
    assert_no_stored_credentials,
)
from feed_passport.infrastructure.sqlite_store import ConcurrencyConflict, SQLiteStore


class OAuthTransactionError(PermissionError):
    """Raised when OAuth state is absent, mismatched, expired, or replayed."""


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _canonical_time(value: datetime) -> str:
    _require_aware(value, "timestamp")
    return value.astimezone(timezone.utc).isoformat()


def _connection_aad(
    connection_id: str,
    owner_id: str,
    platform: str,
    version: int,
) -> bytes:
    return json.dumps(
        {
            "kind": "external_connection",
            "id": connection_id,
            "owner_id": owner_id,
            "platform": platform,
            "version": version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _oauth_aad(state_hash: str, owner_id: str, platform: str) -> bytes:
    return json.dumps(
        {
            "kind": "oauth_transaction",
            "state_hash": state_hash,
            "owner_id": owner_id,
            "platform": platform,
            "version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class EncryptedConnectionRegistry:
    """SQLite registry that never persists OAuth bearer or refresh credentials."""

    def __init__(
        self,
        store: SQLiteStore,
        keyring: AesGcmKeyring,
        *,
        id_factory: Callable[[], str] | None = None,
        state_factory: Callable[[], str] | None = None,
        max_oauth_transactions_per_owner_provider: int = 8,
        oauth_transaction_retention: timedelta = timedelta(hours=1),
    ) -> None:
        if not 1 <= max_oauth_transactions_per_owner_provider <= 50:
            raise ValueError("OAuth transaction bound must be between one and fifty")
        if not timedelta(minutes=15) <= oauth_transaction_retention <= timedelta(days=7):
            raise ValueError("OAuth transaction retention must be between fifteen minutes and seven days")
        self.store = store
        self.keyring = keyring
        self._id_factory = id_factory or (lambda: f"connection_{secrets.token_urlsafe(18)}")
        self._state_factory = state_factory or (lambda: secrets.token_urlsafe(32))
        self._max_oauth_transactions = max_oauth_transactions_per_owner_provider
        self._oauth_transaction_retention = oauth_transaction_retention

    def register_connection(
        self,
        *,
        owner_id: str,
        platform: str,
        external_subject: str,
        credential_ref: str,
        metadata: Mapping[str, Any],
        now: datetime,
        status: ConnectionStatus = ConnectionStatus.ACTIVE,
    ) -> ExternalConnection:
        _require_aware(now, "now")
        if not all(value.strip() for value in (owner_id, platform, external_subject, credential_ref)):
            raise ValueError("connection owner, platform, subject, and credential reference are required")
        if status is ConnectionStatus.REVOKED:
            raise ValueError("a new external connection cannot start revoked")
        assert_no_stored_credentials(metadata)
        connection_id = self._id_factory()
        if not connection_id.strip():
            raise ValueError("connection ID factory returned an empty value")
        version = 1
        payload = {
            "external_subject": external_subject,
            "credential_ref": credential_ref,
            "metadata": dict(metadata),
        }
        encrypted = self.keyring.encrypt_json(
            payload,
            associated_data=_connection_aad(connection_id, owner_id, platform, version),
        )
        subject_fingerprint = self.keyring.blind_index(platform, external_subject)
        canonical_now = _canonical_time(now)
        try:
            with self.store.transaction() as connection:
                # Older databases retained the canonical subject blind index
                # on terminal rows. Migrate that row lazily and atomically so
                # a fresh OAuth authorization can create a new connection
                # without ever reactivating the revoked record.
                historical_row = connection.execute(
                    """
                    SELECT * FROM external_connections
                    WHERE owner_id = ? AND platform = ? AND subject_fingerprint = ?
                    """,
                    (owner_id, platform, subject_fingerprint),
                ).fetchone()
                if (
                    historical_row is not None
                    and historical_row["status"] == ConnectionStatus.REVOKED.value
                ):
                    historical = self._connection_from_row(historical_row)
                    if historical.external_subject != external_subject:
                        raise ConcurrencyConflict(
                            "the external account blind index did not match its encrypted subject"
                        )
                    tombstone = self._revoked_subject_fingerprint(historical)
                    cursor = connection.execute(
                        """
                        UPDATE external_connections SET subject_fingerprint = ?
                        WHERE id = ? AND owner_id = ? AND platform = ?
                            AND status = ? AND subject_fingerprint = ?
                        """,
                        (
                            tombstone,
                            historical.id,
                            owner_id,
                            platform,
                            ConnectionStatus.REVOKED.value,
                            subject_fingerprint,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise ConcurrencyConflict(
                            "the revoked external connection changed during migration"
                        )
                connection.execute(
                    """
                    INSERT INTO external_connections (
                        id, owner_id, platform, subject_fingerprint, status,
                        metadata_ciphertext, metadata_nonce, encryption_key_id,
                        version, created_at, updated_at, revoked_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        connection_id,
                        owner_id,
                        platform,
                        subject_fingerprint,
                        status.value,
                        encrypted.ciphertext,
                        encrypted.nonce,
                        encrypted.key_id,
                        version,
                        canonical_now,
                        canonical_now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ConcurrencyConflict("this owner already registered the external platform account") from exc
        return self.get_connection(connection_id, owner_id=owner_id)

    def get_connection(self, connection_id: str, *, owner_id: str) -> ExternalConnection:
        if not connection_id.strip() or not owner_id.strip():
            raise ValueError("connection ID and owner are required")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM external_connections WHERE id = ? AND owner_id = ?",
                (connection_id, owner_id),
            ).fetchone()
        if row is None:
            raise KeyError("external connection was not found for this owner")
        return self._connection_from_row(row)

    def list_connections(
        self,
        *,
        owner_id: str,
        platform: str | None = None,
    ) -> tuple[ExternalConnection, ...]:
        if not owner_id.strip():
            raise ValueError("connection owner is required")
        with self.store.transaction() as connection:
            if platform is None:
                rows = connection.execute(
                    "SELECT * FROM external_connections WHERE owner_id = ? ORDER BY created_at, id",
                    (owner_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM external_connections
                    WHERE owner_id = ? AND platform = ? ORDER BY created_at, id
                    """,
                    (owner_id, platform),
                ).fetchall()
        return tuple(self._connection_from_row(row) for row in rows)

    def list_runtime_connections(self, *, platform: str) -> tuple[ExternalConnection, ...]:
        """Restore active token-free bindings inside the trusted service runtime."""

        if not platform.strip():
            raise ValueError("connection platform is required")
        with self.store.transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM external_connections
                WHERE platform = ? AND status = ? ORDER BY created_at, id
                """,
                (platform, ConnectionStatus.ACTIVE.value),
            ).fetchall()
        return tuple(self._connection_from_row(row) for row in rows)

    def update_connection(
        self,
        connection_id: str,
        *,
        owner_id: str,
        expected_version: int,
        now: datetime,
        status: ConnectionStatus | None = None,
        credential_ref: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        expected_external_subject: str | None = None,
        retire_superseded_credential: bool = False,
    ) -> ExternalConnection:
        _require_aware(now, "now")
        current = self.get_connection(connection_id, owner_id=owner_id)
        if current.version != expected_version:
            raise ConcurrencyConflict(
                f"expected connection {connection_id} at version {expected_version}, found {current.version}"
            )
        if expected_external_subject is not None and not secrets.compare_digest(
            current.external_subject,
            expected_external_subject,
        ):
            raise ConcurrencyConflict("external connection subject changed during credential rotation")
        if current.status is ConnectionStatus.REVOKED and status is not ConnectionStatus.REVOKED:
            raise ValueError("a revoked connection cannot be reactivated; start a fresh OAuth connection")
        if current.status is ConnectionStatus.REVOKING and status not in {
            None,
            ConnectionStatus.REVOKING,
            ConnectionStatus.REVOKED,
        }:
            raise ValueError("a revoking connection can only complete revocation")
        next_status = status or current.status
        next_credential_ref = credential_ref or current.credential_ref
        retires_credential = retire_superseded_credential and not secrets.compare_digest(
            current.credential_ref,
            next_credential_ref,
        )
        next_metadata = dict(current.metadata if metadata is None else metadata)
        if not next_credential_ref.strip():
            raise ValueError("credential reference is required")
        assert_no_stored_credentials(next_metadata)
        next_version = current.version + 1
        next_subject_fingerprint = (
            self._revoked_subject_fingerprint(current)
            if next_status is ConnectionStatus.REVOKED
            else self.keyring.blind_index(current.platform, current.external_subject)
        )
        payload = {
            "external_subject": current.external_subject,
            "credential_ref": next_credential_ref,
            "metadata": next_metadata,
        }
        encrypted = self.keyring.encrypt_json(
            payload,
            associated_data=_connection_aad(connection_id, owner_id, current.platform, next_version),
        )
        revoked_at = now if next_status is ConnectionStatus.REVOKED else current.revoked_at
        with self.store.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE external_connections SET
                    status = ?, subject_fingerprint = ?,
                    metadata_ciphertext = ?, metadata_nonce = ?,
                    encryption_key_id = ?, version = ?, updated_at = ?, revoked_at = ?
                WHERE id = ? AND owner_id = ? AND version = ?
                """,
                (
                    next_status.value,
                    next_subject_fingerprint,
                    encrypted.ciphertext,
                    encrypted.nonce,
                    encrypted.key_id,
                    next_version,
                    _canonical_time(now),
                    _canonical_time(revoked_at) if revoked_at is not None else None,
                    connection_id,
                    owner_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyConflict("external connection changed concurrently")
            if retires_credential:
                connection.execute(
                    """
                    INSERT INTO oauth_credential_retirements (
                        credential_ref, owner_id, platform, connection_id,
                        replacement_credential_ref, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        current.credential_ref,
                        owner_id,
                        current.platform,
                        connection_id,
                        next_credential_ref,
                        _canonical_time(now),
                    ),
                )
        return self.get_connection(connection_id, owner_id=owner_id)

    def mark_reauth_required(
        self,
        connection_id: str,
        *,
        owner_id: str,
        expected_credential_ref: str,
        now: datetime,
    ) -> ExternalConnection:
        _require_aware(now, "now")
        if not connection_id.strip() or not owner_id.strip() or not expected_credential_ref.strip():
            raise ValueError("connection, owner, and expected credential reference are required")
        for _attempt in range(3):
            current = self.get_connection(connection_id, owner_id=owner_id)
            if not secrets.compare_digest(current.credential_ref, expected_credential_ref):
                return current
            if current.status is not ConnectionStatus.ACTIVE:
                return current
            try:
                return self.update_connection(
                    connection_id,
                    owner_id=owner_id,
                    expected_version=current.version,
                    now=now,
                    status=ConnectionStatus.REAUTH_REQUIRED,
                    expected_external_subject=current.external_subject,
                )
            except ConcurrencyConflict:
                continue
        raise ConcurrencyConflict("external connection kept changing while marking reauthorization")

    def _revoked_subject_fingerprint(self, connection: ExternalConnection) -> str:
        digest = self.keyring.blind_index(
            f"external-connection:revoked:{connection.owner_id}:{connection.platform}",
            connection.id,
        )
        # The primary-key connection ID makes tombstones structurally unique;
        # the HMAC binds each value to the owner, platform, and external index
        # key without revealing the former account subject.
        return f"revoked:{connection.id}:{digest}"

    def revoke_connection(
        self,
        connection_id: str,
        *,
        owner_id: str,
        expected_version: int,
        now: datetime,
    ) -> ExternalConnection:
        return self.update_connection(
            connection_id,
            owner_id=owner_id,
            expected_version=expected_version,
            now=now,
            status=ConnectionStatus.REVOKED,
        )

    def begin_oauth_transaction(
        self,
        *,
        owner_id: str,
        platform: str,
        redirect_uri: str,
        metadata: Mapping[str, Any],
        now: datetime,
        ttl: timedelta = timedelta(minutes=10),
    ) -> OAuthTransactionStart:
        _require_aware(now, "now")
        if not all(value.strip() for value in (owner_id, platform, redirect_uri)):
            raise ValueError("OAuth owner, platform, and redirect URI are required")
        if ttl <= timedelta(0) or ttl > timedelta(minutes=15):
            raise ValueError("OAuth transaction lifetime must be between zero and fifteen minutes")
        assert_no_stored_credentials(metadata)
        state = self._state_factory()
        if len(state) < 32:
            raise ValueError("OAuth state factory must provide at least 32 characters")
        state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
        encrypted = self.keyring.encrypt_json(
            dict(metadata),
            associated_data=_oauth_aad(state_hash, owner_id, platform),
        )
        expires_at = now + ttl
        try:
            with self.store.transaction() as connection:
                self._prune_oauth_transactions(connection, now=now)
                connection.execute(
                    """
                    INSERT INTO oauth_transactions (
                        state_hash, owner_id, platform, redirect_uri,
                        metadata_ciphertext, metadata_nonce, encryption_key_id,
                        created_at, expires_at, consumed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        state_hash,
                        owner_id,
                        platform,
                        redirect_uri,
                        encrypted.ciphertext,
                        encrypted.nonce,
                        encrypted.key_id,
                        _canonical_time(now),
                        _canonical_time(expires_at),
                    ),
                )
                overflow = connection.execute(
                    """
                    SELECT state_hash FROM oauth_transactions
                    WHERE owner_id = ? AND platform = ?
                    ORDER BY rowid DESC
                    LIMIT -1 OFFSET ?
                    """,
                    (owner_id, platform, self._max_oauth_transactions),
                ).fetchall()
                if overflow:
                    connection.executemany(
                        "DELETE FROM oauth_transactions WHERE state_hash = ?",
                        ((row["state_hash"],) for row in overflow),
                    )
        except sqlite3.IntegrityError as exc:
            raise ConcurrencyConflict("OAuth state was generated more than once") from exc
        return OAuthTransactionStart(
            state=state,
            owner_id=owner_id,
            platform=platform,
            redirect_uri=redirect_uri,
            expires_at=expires_at,
        )

    def consume_oauth_transaction(
        self,
        state: str,
        *,
        owner_id: str,
        platform: str,
        now: datetime,
    ) -> OAuthTransaction:
        _require_aware(now, "now")
        if not state or not owner_id.strip() or not platform.strip():
            raise OAuthTransactionError("OAuth transaction is invalid or unavailable")
        state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
        canonical_now = _canonical_time(now)
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM oauth_transactions WHERE state_hash = ?",
                (state_hash,),
            ).fetchone()
            if row is None:
                raise OAuthTransactionError("OAuth transaction is invalid or unavailable")
            if row["owner_id"] != owner_id or row["platform"] != platform:
                raise OAuthTransactionError(
                    "OAuth transaction does not belong to this principal and platform"
                )
            if row["consumed_at"] is not None:
                raise OAuthTransactionError("OAuth transaction has already been consumed")
            expires_at = datetime.fromisoformat(row["expires_at"])
            if now.astimezone(timezone.utc) >= expires_at:
                raise OAuthTransactionError("OAuth transaction has expired")
            cursor = connection.execute(
                """
                UPDATE oauth_transactions SET consumed_at = ?
                WHERE state_hash = ? AND consumed_at IS NULL
                """,
                (canonical_now, state_hash),
            )
            if cursor.rowcount != 1:
                raise OAuthTransactionError("OAuth transaction has already been consumed")
        metadata = self.keyring.decrypt_json(
            EncryptedJson(
                key_id=row["encryption_key_id"],
                nonce=bytes(row["metadata_nonce"]),
                ciphertext=bytes(row["metadata_ciphertext"]),
            ),
            associated_data=_oauth_aad(state_hash, owner_id, platform),
        )
        if not isinstance(metadata, dict):
            raise ValueError("OAuth transaction metadata must be an object")
        return OAuthTransaction(
            owner_id=owner_id,
            platform=platform,
            redirect_uri=row["redirect_uri"],
            metadata=metadata,
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=expires_at,
            consumed_at=now,
        )

    def _prune_oauth_transactions(
        self,
        connection: sqlite3.Connection,
        *,
        now: datetime,
    ) -> None:
        retention_cutoff = _canonical_time(now - self._oauth_transaction_retention)
        connection.execute(
            """
            DELETE FROM oauth_transactions
            WHERE
                (consumed_at IS NOT NULL AND consumed_at <= ?)
                OR expires_at <= ?
            """,
            (retention_cutoff, retention_cutoff),
        )

    def _connection_from_row(self, row: sqlite3.Row) -> ExternalConnection:
        version = int(row["version"])
        payload = self.keyring.decrypt_json(
            EncryptedJson(
                key_id=row["encryption_key_id"],
                nonce=bytes(row["metadata_nonce"]),
                ciphertext=bytes(row["metadata_ciphertext"]),
            ),
            associated_data=_connection_aad(
                row["id"],
                row["owner_id"],
                row["platform"],
                version,
            ),
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("metadata"), dict):
            raise ValueError("external connection metadata has an invalid shape")
        return ExternalConnection(
            id=row["id"],
            owner_id=row["owner_id"],
            platform=row["platform"],
            status=ConnectionStatus(row["status"]),
            external_subject=str(payload["external_subject"]),
            credential_ref=str(payload["credential_ref"]),
            metadata=payload["metadata"],
            version=version,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            revoked_at=(
                datetime.fromisoformat(row["revoked_at"])
                if row["revoked_at"] is not None
                else None
            ),
        )
