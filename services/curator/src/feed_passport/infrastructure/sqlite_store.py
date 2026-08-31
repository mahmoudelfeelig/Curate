from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterator

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

    def schedule_once(self, job_id: str, kind: str, run_at: datetime, payload: Any) -> bool:
        if run_at.tzinfo is None or run_at.utcoffset() is None:
            raise ValueError("run_at must be timezone-aware")
        canonical_run_at = run_at.astimezone(timezone.utc).isoformat()
        payload_json = json.dumps(to_primitive(payload), separators=(",", ":"), sort_keys=True)
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO scheduled_jobs (id, kind, run_at, payload_json, status) VALUES (?, ?, ?, ?, 'pending')",
                (job_id, kind, canonical_run_at, payload_json),
            )
        return cursor.rowcount == 1

    def claim_due_jobs(self, now: datetime, limit: int = 50) -> tuple[dict[str, Any], ...]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        canonical_now = now.astimezone(timezone.utc).isoformat()
        claimed: list[dict[str, Any]] = []
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT id, kind, run_at, payload_json FROM scheduled_jobs
                WHERE status = 'pending' AND run_at <= ? ORDER BY run_at, id LIMIT ?
                """,
                (canonical_now, limit),
            ).fetchall()
            for row in rows:
                cursor = connection.execute(
                    "UPDATE scheduled_jobs SET status = 'claimed', claimed_at = ? WHERE id = ? AND status = 'pending'",
                    (canonical_now, row["id"]),
                )
                if cursor.rowcount == 1:
                    claimed.append(
                        {
                            "id": row["id"],
                            "kind": row["kind"],
                            "run_at": row["run_at"],
                            "payload": json.loads(row["payload_json"]),
                        }
                    )
        return tuple(claimed)

    def complete_job(self, job_id: str, completed_at: datetime, *, error: str | None = None) -> None:
        status = "failed" if error else "completed"
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE scheduled_jobs SET status = ?, completed_at = ?, error = ? WHERE id = ? AND status = 'claimed'",
                (status, completed_at.isoformat(), error, job_id),
            )
            if cursor.rowcount != 1:
                raise ConcurrencyConflict("scheduled job is not in a claimable completion state")

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
                claimed_at TEXT,
                completed_at TEXT,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_scheduled_jobs_due ON scheduled_jobs(status, run_at);
            """
        )

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
