from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterator, Mapping

from feed_passport.infrastructure.crypto import assert_no_stored_credentials
from feed_passport.ports.action_journal import (
    RemoteActionAttempt,
    RemoteActionState,
    validate_remote_action_transition,
)

from .serialization import to_primitive


class ConcurrencyConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EventRecord:
    position: int
    aggregate_id: str
    aggregate_type: str
    version: int
    event_type: str
    payload: dict[str, Any]
    actor_id: str
    trace_id: str
    occurred_at: datetime


class SQLiteStore:
    """Append-only event log plus rebuildable JSON projections."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                yield self._connection
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def append_event(
        self,
        *,
        aggregate_id: str,
        aggregate_type: str,
        expected_version: int,
        event_type: str,
        payload: Any,
        actor_id: str,
        trace_id: str,
        occurred_at: datetime,
        projection_kind: str | None = None,
        projection: Any | None = None,
    ) -> int:
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        next_version = expected_version + 1
        payload_json = json.dumps(to_primitive(payload), separators=(",", ":"), sort_keys=True)
        projection_json = (
            json.dumps(to_primitive(projection), separators=(",", ":"), sort_keys=True)
            if projection is not None
            else None
        )
        with self.transaction() as connection:
            current = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM events WHERE aggregate_id = ?",
                (aggregate_id,),
            ).fetchone()["version"]
            if int(current) != expected_version:
                raise ConcurrencyConflict(
                    f"expected aggregate {aggregate_id} at version {expected_version}, found {current}"
                )
            connection.execute(
                """
                INSERT INTO events (
                    aggregate_id, aggregate_type, version, event_type, payload_json,
                    actor_id, trace_id, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    aggregate_id,
                    aggregate_type,
                    next_version,
                    event_type,
                    payload_json,
                    actor_id,
                    trace_id,
                    occurred_at.isoformat(),
                ),
            )
            if projection_kind is not None and projection_json is not None:
                connection.execute(
                    """
                    INSERT INTO projections (kind, id, version, data_json, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(kind, id) DO UPDATE SET
                        version = excluded.version,
                        data_json = excluded.data_json,
                        updated_at = excluded.updated_at
                    """,
                    (projection_kind, aggregate_id, next_version, projection_json, occurred_at.isoformat()),
                )
        return next_version

    def load_events(self, aggregate_id: str | None = None, *, after_position: int = 0) -> tuple[EventRecord, ...]:
        with self._lock:
            if aggregate_id is None:
                rows = self._connection.execute(
                    "SELECT * FROM events WHERE position > ? ORDER BY position",
                    (after_position,),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM events WHERE aggregate_id = ? AND position > ? ORDER BY position",
                    (aggregate_id, after_position),
                ).fetchall()
        return tuple(self._event_from_row(row) for row in rows)

    def get_projection(self, kind: str, identifier: str) -> tuple[int, dict[str, Any]] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT version, data_json FROM projections WHERE kind = ? AND id = ?",
                (kind, identifier),
            ).fetchone()
        if row is None:
            return None
        return int(row["version"]), json.loads(row["data_json"])

    def list_projections(self, kind: str) -> tuple[tuple[str, int, dict[str, Any]], ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, version, data_json FROM projections WHERE kind = ? ORDER BY updated_at DESC, id",
                (kind,),
            ).fetchall()
        return tuple((row["id"], int(row["version"]), json.loads(row["data_json"])) for row in rows)

    def reserve_idempotency(self, key: str, request_hash: str, created_at: datetime) -> bool:
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT request_hash FROM idempotency WHERE key = ?",
                (key,),
            ).fetchone()
            if row is not None:
                if row["request_hash"] != request_hash:
                    raise ConcurrencyConflict("idempotency key was reused for a different request")
                return False
            connection.execute(
                "INSERT INTO idempotency (key, request_hash, created_at) VALUES (?, ?, ?)",
                (key, request_hash, created_at.isoformat()),
            )
        return True

    def get_or_create_secret(self, name: str, *, byte_length: int = 32) -> bytes:
        """Return a durable local runtime secret without exposing it through projections."""

        if not name.strip() or byte_length < 32:
            raise ValueError("runtime secret name and at least 32 bytes are required")
        candidate = secrets.token_bytes(byte_length)
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO runtime_secrets (name, value) VALUES (?, ?)",
                (name, candidate),
            )
            row = connection.execute(
                "SELECT value FROM runtime_secrets WHERE name = ?",
                (name,),
            ).fetchone()
        if row is None:
            raise RuntimeError("failed to resolve the durable local runtime secret")
        return bytes(row["value"])

    def complete_idempotency(self, key: str, response: Any, completed_at: datetime) -> None:
        response_json = json.dumps(to_primitive(response), separators=(",", ":"), sort_keys=True)
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE idempotency SET response_json = ?, completed_at = ? WHERE key = ?",
                (response_json, completed_at.isoformat(), key),
            )
            if cursor.rowcount != 1:
                raise KeyError("unknown idempotency reservation")

    def idempotent_response(self, key: str) -> Any | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT response_json FROM idempotency WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None or row["response_json"] is None:
            return None
        return json.loads(row["response_json"])

    def schedule_once(
        self,
        job_id: str,
        kind: str,
        run_at: datetime,
        payload: Any,
        *,
        max_attempts: int = 3,
    ) -> bool:
        if run_at.tzinfo is None or run_at.utcoffset() is None:
            raise ValueError("run_at must be timezone-aware")
        if max_attempts < 1:
            raise ValueError("scheduled job max_attempts must be positive")
        canonical_run_at = run_at.astimezone(timezone.utc).isoformat()
        payload_json = json.dumps(to_primitive(payload), separators=(",", ":"), sort_keys=True)
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO scheduled_jobs (
                    id, kind, run_at, payload_json, status, attempt_count, max_attempts,
                    next_attempt_at
                ) VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)
                """,
                (job_id, kind, canonical_run_at, payload_json, max_attempts, canonical_run_at),
            )
        return cursor.rowcount == 1

    def claim_due_jobs(
        self,
        now: datetime,
        limit: int = 50,
        *,
        worker_id: str = "local-scheduler",
        lease_for: timedelta = timedelta(minutes=5),
    ) -> tuple[dict[str, Any], ...]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if limit < 1 or not worker_id.strip() or lease_for <= timedelta(0):
            raise ValueError("job claim limit, worker ID, and lease duration must be positive")
        canonical_now = now.astimezone(timezone.utc).isoformat()
        lease_expires_at = (now.astimezone(timezone.utc) + lease_for).isoformat()
        claimed: list[dict[str, Any]] = []
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE scheduled_jobs SET
                    status = 'failed', completed_at = ?,
                    error = COALESCE(error, 'lease_expired_after_final_attempt'),
                    claimed_by = NULL, claim_expires_at = NULL, claim_token = NULL
                WHERE status = 'claimed'
                  AND claim_expires_at IS NOT NULL
                  AND claim_expires_at <= ?
                  AND attempt_count >= max_attempts
                """,
                (canonical_now, canonical_now),
            )
            rows = connection.execute(
                """
                SELECT id, kind, run_at, payload_json, status, attempt_count, max_attempts
                FROM scheduled_jobs
                WHERE attempt_count < max_attempts AND (
                    (status = 'pending' AND run_at <= ?)
                    OR (status = 'retryable' AND COALESCE(next_attempt_at, run_at) <= ?)
                    OR (
                        status = 'claimed'
                        AND claim_expires_at IS NOT NULL
                        AND claim_expires_at <= ?
                    )
                )
                ORDER BY COALESCE(next_attempt_at, run_at), id LIMIT ?
                """,
                (canonical_now, canonical_now, canonical_now, limit),
            ).fetchall()
            for row in rows:
                claim_token = secrets.token_urlsafe(18)
                cursor = connection.execute(
                    """
                    UPDATE scheduled_jobs SET
                        status = 'claimed', claimed_at = ?, claimed_by = ?,
                        claim_expires_at = ?, claim_token = ?,
                        attempt_count = attempt_count + 1, completed_at = NULL, error = NULL
                    WHERE id = ? AND attempt_count < max_attempts AND (
                        (status = 'pending' AND run_at <= ?)
                        OR (status = 'retryable' AND COALESCE(next_attempt_at, run_at) <= ?)
                        OR (
                            status = 'claimed'
                            AND claim_expires_at IS NOT NULL
                            AND claim_expires_at <= ?
                        )
                    )
                    """,
                    (
                        canonical_now,
                        worker_id,
                        lease_expires_at,
                        claim_token,
                        row["id"],
                        canonical_now,
                        canonical_now,
                        canonical_now,
                    ),
                )
                if cursor.rowcount == 1:
                    claimed.append(
                        {
                            "id": row["id"],
                            "kind": row["kind"],
                            "run_at": row["run_at"],
                            "payload": json.loads(row["payload_json"]),
                            "attempt_count": int(row["attempt_count"]) + 1,
                            "max_attempts": int(row["max_attempts"]),
                            "claimed_by": worker_id,
                            "claim_expires_at": lease_expires_at,
                            "claim_token": claim_token,
                        }
                    )
        return tuple(claimed)

    def complete_job(
        self,
        job_id: str,
        completed_at: datetime,
        *,
        error: str | None = None,
        claim_token: str | None = None,
        retry_delay_seconds: float = 5.0,
    ) -> None:
        if completed_at.tzinfo is None or completed_at.utcoffset() is None:
            raise ValueError("completed_at must be timezone-aware")
        if retry_delay_seconds < 0:
            raise ValueError("retry delay cannot be negative")
        canonical_completed_at = completed_at.astimezone(timezone.utc).isoformat()
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT status, attempt_count, max_attempts, claim_token
                FROM scheduled_jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if row is None or row["status"] != "claimed":
                raise ConcurrencyConflict("scheduled job is not in a claimable completion state")
            if claim_token is not None and row["claim_token"] != claim_token:
                raise ConcurrencyConflict("scheduled job claim token does not match the active lease")
            attempt_count = int(row["attempt_count"])
            max_attempts = int(row["max_attempts"])
            if error is None:
                status = "completed"
                next_attempt_at = None
            elif attempt_count < max_attempts:
                status = "retryable"
                delay = retry_delay_seconds * (2 ** max(0, attempt_count - 1))
                next_attempt_at = (
                    completed_at.astimezone(timezone.utc) + timedelta(seconds=delay)
                ).isoformat()
            else:
                status = "failed"
                next_attempt_at = None
            cursor = connection.execute(
                """
                UPDATE scheduled_jobs SET
                    status = ?, completed_at = ?, error = ?, next_attempt_at = ?,
                    claimed_by = NULL, claim_expires_at = NULL, claim_token = NULL
                WHERE id = ? AND status = 'claimed'
                """,
                (
                    status,
                    canonical_completed_at if status in {"completed", "failed"} else None,
                    error,
                    next_attempt_at,
                    job_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyConflict("scheduled job is not in a claimable completion state")

    def get_scheduled_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM scheduled_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise KeyError("scheduled job was not found")
        return {
            "id": row["id"],
            "kind": row["kind"],
            "run_at": row["run_at"],
            "payload": json.loads(row["payload_json"]),
            "status": row["status"],
            "attempt_count": int(row["attempt_count"]),
            "max_attempts": int(row["max_attempts"]),
            "claimed_at": row["claimed_at"],
            "claimed_by": row["claimed_by"],
            "claim_expires_at": row["claim_expires_at"],
            "next_attempt_at": row["next_attempt_at"],
            "completed_at": row["completed_at"],
            "error": row["error"],
        }

    def reserve_remote_action(
        self,
        *,
        attempt_id: str,
        owner_id: str,
        connection_id: str,
        platform: str,
        migration_id: str,
        action_id: str,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
        action_payload: Mapping[str, Any],
        created_at: datetime,
    ) -> RemoteActionAttempt:
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        required = (
            attempt_id,
            owner_id,
            connection_id,
            platform,
            migration_id,
            action_id,
            operation,
            idempotency_key,
        )
        if not all(value.strip() for value in required):
            raise ValueError("remote action identifiers are required")
        if len(request_fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in request_fingerprint
        ):
            raise ValueError("remote action request fingerprint must be a lowercase SHA-256 digest")
        assert_no_stored_credentials(action_payload, path="action_payload")
        payload_json = json.dumps(
            to_primitive(action_payload),
            separators=(",", ":"),
            sort_keys=True,
        )
        canonical_created_at = created_at.astimezone(timezone.utc).isoformat()
        with self.transaction() as connection:
            bound_connection = connection.execute(
                """
                SELECT status FROM external_connections
                WHERE id = ? AND owner_id = ? AND platform = ?
                """,
                (connection_id, owner_id, platform),
            ).fetchone()
            if bound_connection is None:
                raise PermissionError("remote action connection is not owned by this principal and platform")
            if bound_connection["status"] != "active":
                raise PermissionError("remote action connection is not active")
            existing = connection.execute(
                """
                SELECT * FROM remote_action_attempts
                WHERE connection_id = ? AND operation = ? AND idempotency_key = ?
                """,
                (connection_id, operation, idempotency_key),
            ).fetchone()
            if existing is not None:
                immutable = (
                    existing["owner_id"] == owner_id
                    and existing["platform"] == platform
                    and existing["migration_id"] == migration_id
                    and existing["action_id"] == action_id
                    and existing["request_fingerprint"] == request_fingerprint
                    and existing["action_payload_json"] == payload_json
                )
                if not immutable:
                    raise ConcurrencyConflict(
                        "remote action idempotency key was reused for a different request"
                    )
                return self._remote_action_from_row(existing)
            try:
                connection.execute(
                    """
                    INSERT INTO remote_action_attempts (
                        id, owner_id, connection_id, platform, migration_id, action_id,
                        operation, idempotency_key, request_fingerprint, state,
                        action_payload_json, attempt_count, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, 0, ?, ?)
                    """,
                    (
                        attempt_id,
                        owner_id,
                        connection_id,
                        platform,
                        migration_id,
                        action_id,
                        operation,
                        idempotency_key,
                        request_fingerprint,
                        payload_json,
                        canonical_created_at,
                        canonical_created_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ConcurrencyConflict("remote action attempt ID already exists") from exc
            row = connection.execute(
                "SELECT * FROM remote_action_attempts WHERE id = ?",
                (attempt_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("failed to persist remote action reservation")
        return self._remote_action_from_row(row)

    def get_remote_action(
        self,
        attempt_id: str,
        *,
        owner_id: str | None = None,
    ) -> RemoteActionAttempt:
        with self._lock:
            if owner_id is None:
                row = self._connection.execute(
                    "SELECT * FROM remote_action_attempts WHERE id = ?",
                    (attempt_id,),
                ).fetchone()
            else:
                row = self._connection.execute(
                    "SELECT * FROM remote_action_attempts WHERE id = ? AND owner_id = ?",
                    (attempt_id, owner_id),
                ).fetchone()
        if row is None:
            raise KeyError("remote action attempt was not found")
        return self._remote_action_from_row(row)

    def transition_remote_action(
        self,
        attempt_id: str,
        *,
        expected_state: RemoteActionState,
        new_state: RemoteActionState,
        updated_at: datetime,
        lease_owner: str | None = None,
        lease_expires_at: datetime | None = None,
        next_attempt_at: datetime | None = None,
        platform_reference: str | None = None,
        before_state: Mapping[str, Any] | None = None,
        after_state: Mapping[str, Any] | None = None,
        error_code: str | None = None,
    ) -> RemoteActionAttempt:
        if updated_at.tzinfo is None or updated_at.utcoffset() is None:
            raise ValueError("updated_at must be timezone-aware")
        validate_remote_action_transition(expected_state, new_state)
        active_lease_states = {
            RemoteActionState.DISPATCHING,
            RemoteActionState.RECONCILING,
            RemoteActionState.ROLLBACK_PENDING,
        }
        if new_state in active_lease_states:
            if not lease_owner or lease_expires_at is None:
                raise ValueError(f"{new_state.value} requires a lease owner and expiry")
            if lease_expires_at.tzinfo is None or lease_expires_at.utcoffset() is None:
                raise ValueError("lease_expires_at must be timezone-aware")
            if lease_expires_at <= updated_at:
                raise ValueError("remote action lease expiry must be after the transition time")
        if next_attempt_at is not None:
            if next_attempt_at.tzinfo is None or next_attempt_at.utcoffset() is None:
                raise ValueError("next_attempt_at must be timezone-aware")
            if next_attempt_at < updated_at:
                raise ValueError("next remote action attempt cannot be scheduled in the past")
        if before_state is not None:
            assert_no_stored_credentials(before_state, path="before_state")
        if after_state is not None:
            assert_no_stored_credentials(after_state, path="after_state")
        before_json = (
            json.dumps(to_primitive(before_state), separators=(",", ":"), sort_keys=True)
            if before_state is not None
            else None
        )
        after_json = (
            json.dumps(to_primitive(after_state), separators=(",", ":"), sort_keys=True)
            if after_state is not None
            else None
        )
        canonical_updated_at = updated_at.astimezone(timezone.utc).isoformat()
        canonical_lease = (
            lease_expires_at.astimezone(timezone.utc).isoformat()
            if lease_expires_at is not None
            else None
        )
        canonical_next = (
            next_attempt_at.astimezone(timezone.utc).isoformat()
            if next_attempt_at is not None
            else None
        )
        increment_attempt = new_state in {
            RemoteActionState.DISPATCHING,
            RemoteActionState.RECONCILING,
            RemoteActionState.ROLLBACK_PENDING,
        }
        with self.transaction() as connection:
            current = connection.execute(
                "SELECT * FROM remote_action_attempts WHERE id = ?",
                (attempt_id,),
            ).fetchone()
            if current is None:
                raise KeyError("remote action attempt was not found")
            current_state = RemoteActionState(current["state"])
            if current_state is not expected_state:
                raise ConcurrencyConflict(
                    f"expected remote action {attempt_id} in {expected_state.value}, "
                    f"found {current_state.value}"
                )
            cursor = connection.execute(
                """
                UPDATE remote_action_attempts SET
                    state = ?,
                    attempt_count = attempt_count + ?,
                    platform_reference = COALESCE(?, platform_reference),
                    before_state_json = COALESCE(?, before_state_json),
                    after_state_json = COALESCE(?, after_state_json),
                    error_code = ?,
                    lease_owner = ?,
                    lease_expires_at = ?,
                    next_attempt_at = ?,
                    updated_at = ?
                WHERE id = ? AND state = ?
                """,
                (
                    new_state.value,
                    1 if increment_attempt else 0,
                    platform_reference,
                    before_json,
                    after_json,
                    error_code,
                    lease_owner if new_state in active_lease_states else None,
                    canonical_lease if new_state in active_lease_states else None,
                    canonical_next,
                    canonical_updated_at,
                    attempt_id,
                    expected_state.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyConflict("remote action changed concurrently")
            row = connection.execute(
                "SELECT * FROM remote_action_attempts WHERE id = ?",
                (attempt_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("remote action disappeared after transition")
        return self._remote_action_from_row(row)

    def list_remote_actions(
        self,
        *,
        owner_id: str | None = None,
        migration_id: str | None = None,
        connection_id: str | None = None,
    ) -> tuple[RemoteActionAttempt, ...]:
        filters: list[str] = []
        parameters: list[str] = []
        if owner_id is not None:
            filters.append("owner_id = ?")
            parameters.append(owner_id)
        if migration_id is not None:
            filters.append("migration_id = ?")
            parameters.append(migration_id)
        if connection_id is not None:
            filters.append("connection_id = ?")
            parameters.append(connection_id)
        where = f" WHERE {' AND '.join(filters)}" if filters else ""
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM remote_action_attempts{where} ORDER BY created_at, id",
                tuple(parameters),
            ).fetchall()
        return tuple(self._remote_action_from_row(row) for row in rows)

    def list_recoverable_remote_actions(
        self,
        now: datetime,
        *,
        limit: int = 100,
    ) -> tuple[RemoteActionAttempt, ...]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if limit < 1:
            raise ValueError("remote action recovery limit must be positive")
        canonical_now = now.astimezone(timezone.utc).isoformat()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM remote_action_attempts WHERE
                    (
                        state IN ('unknown', 'rollback_unknown')
                        AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                    )
                    OR (
                        state IN ('dispatching', 'reconciling', 'rollback_pending')
                        AND lease_expires_at IS NOT NULL
                        AND lease_expires_at <= ?
                    )
                ORDER BY updated_at, id LIMIT ?
                """,
                (canonical_now, canonical_now, limit),
            ).fetchall()
        return tuple(self._remote_action_from_row(row) for row in rows)

    def _migrate(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                position INTEGER PRIMARY KEY AUTOINCREMENT,
                aggregate_id TEXT NOT NULL,
                aggregate_type TEXT NOT NULL,
                version INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                UNIQUE(aggregate_id, version)
            );
            CREATE INDEX IF NOT EXISTS ix_events_trace ON events(trace_id, position);
            CREATE TABLE IF NOT EXISTS projections (
                kind TEXT NOT NULL,
                id TEXT NOT NULL,
                version INTEGER NOT NULL,
                data_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(kind, id)
            );
            CREATE TABLE IF NOT EXISTS idempotency (
                key TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                response_json TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS runtime_secrets (
                name TEXT PRIMARY KEY,
                value BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                run_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                claimed_at TEXT,
                claimed_by TEXT,
                claim_expires_at TEXT,
                claim_token TEXT,
                next_attempt_at TEXT,
                completed_at TEXT,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_scheduled_jobs_due ON scheduled_jobs(status, run_at);
            CREATE TABLE IF NOT EXISTS external_connections (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                subject_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('active', 'reauth_required', 'revoking', 'revoked')),
                metadata_ciphertext BLOB NOT NULL,
                metadata_nonce BLOB NOT NULL,
                encryption_key_id TEXT NOT NULL,
                version INTEGER NOT NULL CHECK(version > 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                revoked_at TEXT,
                UNIQUE(owner_id, platform, subject_fingerprint)
            );
            CREATE INDEX IF NOT EXISTS ix_external_connections_owner
                ON external_connections(owner_id, platform, status);
            CREATE TABLE IF NOT EXISTS oauth_transactions (
                state_hash TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                redirect_uri TEXT NOT NULL,
                metadata_ciphertext BLOB NOT NULL,
                metadata_nonce BLOB NOT NULL,
                encryption_key_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_oauth_transactions_expiry
                ON oauth_transactions(expires_at, consumed_at);
            CREATE INDEX IF NOT EXISTS ix_oauth_transactions_owner_platform
                ON oauth_transactions(owner_id, platform, created_at);
            CREATE TABLE IF NOT EXISTS oauth_credential_retirements (
                credential_ref TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                connection_id TEXT NOT NULL,
                replacement_credential_ref TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_oauth_credential_retirements_owner
                ON oauth_credential_retirements(owner_id, platform, connection_id, created_at);
            CREATE TABLE IF NOT EXISTS remote_action_attempts (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                connection_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                migration_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN (
                    'pending', 'dispatching', 'succeeded', 'failed_retryable',
                    'failed_final', 'unknown', 'reconciling', 'rollback_pending',
                    'rolled_back', 'rollback_unknown', 'needs_human'
                )),
                action_payload_json TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                platform_reference TEXT,
                before_state_json TEXT,
                after_state_json TEXT,
                error_code TEXT,
                lease_owner TEXT,
                lease_expires_at TEXT,
                next_attempt_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(connection_id, operation, idempotency_key),
                FOREIGN KEY(connection_id) REFERENCES external_connections(id)
            );
            CREATE INDEX IF NOT EXISTS ix_remote_action_migration
                ON remote_action_attempts(migration_id, created_at, id);
            CREATE INDEX IF NOT EXISTS ix_remote_action_recovery
                ON remote_action_attempts(state, next_attempt_at, lease_expires_at, updated_at);
            """
        )
        self._migrate_connection_status_constraint()
        for column_name, declaration in (
            ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
            ("max_attempts", "INTEGER NOT NULL DEFAULT 3"),
            ("claimed_by", "TEXT"),
            ("claim_expires_at", "TEXT"),
            ("claim_token", "TEXT"),
            ("next_attempt_at", "TEXT"),
        ):
            self._ensure_column("scheduled_jobs", column_name, declaration)
        self._connection.execute(
            """
            UPDATE scheduled_jobs SET next_attempt_at = run_at
            WHERE next_attempt_at IS NULL AND status IN ('pending', 'retryable')
            """
        )
        self._connection.execute(
            """
            UPDATE scheduled_jobs SET
                status = 'retryable', next_attempt_at = run_at,
                claimed_by = NULL, claim_token = NULL
            WHERE status = 'claimed' AND claim_expires_at IS NULL
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_scheduled_jobs_recovery
            ON scheduled_jobs(status, next_attempt_at, claim_expires_at)
            """
        )

    def _migrate_connection_status_constraint(self) -> None:
        schema_row = self._connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'external_connections'"
        ).fetchone()
        schema_sql = str(schema_row["sql"] if schema_row is not None else "")
        if "'revoking'" in schema_sql:
            return
        self._connection.execute("PRAGMA foreign_keys = OFF")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                """
                CREATE TABLE external_connections_v2 (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    subject_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'active', 'reauth_required', 'revoking', 'revoked'
                    )),
                    metadata_ciphertext BLOB NOT NULL,
                    metadata_nonce BLOB NOT NULL,
                    encryption_key_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version > 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revoked_at TEXT,
                    UNIQUE(owner_id, platform, subject_fingerprint)
                )
                """
            )
            self._connection.execute(
                """
                INSERT INTO external_connections_v2 (
                    id, owner_id, platform, subject_fingerprint, status,
                    metadata_ciphertext, metadata_nonce, encryption_key_id,
                    version, created_at, updated_at, revoked_at
                )
                SELECT
                    id, owner_id, platform, subject_fingerprint, status,
                    metadata_ciphertext, metadata_nonce, encryption_key_id,
                    version, created_at, updated_at, revoked_at
                FROM external_connections
                """
            )
            self._connection.execute("DROP TABLE external_connections")
            self._connection.execute(
                "ALTER TABLE external_connections_v2 RENAME TO external_connections"
            )
            self._connection.execute(
                """
                CREATE INDEX ix_external_connections_owner
                ON external_connections(owner_id, platform, status)
                """
            )
            self._connection.execute("COMMIT")
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        finally:
            self._connection.execute("PRAGMA foreign_keys = ON")
        violations = self._connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError("connection status migration violated a foreign key")

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        existing = {
            row["name"]
            for row in self._connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            self._connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> EventRecord:
        return EventRecord(
            position=int(row["position"]),
            aggregate_id=row["aggregate_id"],
            aggregate_type=row["aggregate_type"],
            version=int(row["version"]),
            event_type=row["event_type"],
            payload=json.loads(row["payload_json"]),
            actor_id=row["actor_id"],
            trace_id=row["trace_id"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
        )

    @staticmethod
    def _remote_action_from_row(row: sqlite3.Row) -> RemoteActionAttempt:
        return RemoteActionAttempt(
            id=row["id"],
            owner_id=row["owner_id"],
            connection_id=row["connection_id"],
            platform=row["platform"],
            migration_id=row["migration_id"],
            action_id=row["action_id"],
            operation=row["operation"],
            idempotency_key=row["idempotency_key"],
            request_fingerprint=row["request_fingerprint"],
            state=RemoteActionState(row["state"]),
            action_payload=json.loads(row["action_payload_json"]),
            attempt_count=int(row["attempt_count"]),
            platform_reference=row["platform_reference"],
            before_state=(
                json.loads(row["before_state_json"])
                if row["before_state_json"] is not None
                else None
            ),
            after_state=(
                json.loads(row["after_state_json"])
                if row["after_state_json"] is not None
                else None
            ),
            error_code=row["error_code"],
            lease_owner=row["lease_owner"],
            lease_expires_at=(
                datetime.fromisoformat(row["lease_expires_at"])
                if row["lease_expires_at"] is not None
                else None
            ),
            next_attempt_at=(
                datetime.fromisoformat(row["next_attempt_at"])
                if row["next_attempt_at"] is not None
                else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
