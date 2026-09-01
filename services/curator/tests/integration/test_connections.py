from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feed_passport.domain.connections import ConnectionStatus
from feed_passport.infrastructure.connection_registry import EncryptedConnectionRegistry
from feed_passport.infrastructure.crypto import AesGcmKeyring, SecretMaterialRejected
from feed_passport.infrastructure.sqlite_store import ConcurrencyConflict, SQLiteStore


NOW = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


def keyring(*, active: str = "key-1", include_rotated: bool = False) -> AesGcmKeyring:
    keys = {"key-1": b"a" * 32}
    if include_rotated:
        keys["key-2"] = b"b" * 32
    return AesGcmKeyring(active_key_id=active, keys=keys, index_key=b"i" * 32)


class EncryptedConnectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "connections.db"
        self.store = SQLiteStore(self.path)
        identifiers = iter(("connection-one", "connection-two"))
        self.registry = EncryptedConnectionRegistry(
            self.store,
            keyring(),
            id_factory=lambda: next(identifiers),
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_connection_is_owner_bound_and_sensitive_metadata_is_encrypted(self) -> None:
        connection = self.registry.register_connection(
            owner_id="person-a",
            platform="youtube",
            external_subject="channel-private-123",
            credential_ref="agentcore-identity/provider/person-a/youtube",
            metadata={
                "display_handle": "private-handle",
                "granted_scopes": ["youtube.readonly", "youtube.force-ssl"],
                "token_expires_at": "2026-09-01T11:00:00+00:00",
            },
            now=NOW,
        )

        self.assertEqual(connection.owner_id, "person-a")
        self.assertEqual(connection.external_subject_id, "channel-private-123")
        self.assertEqual(
            connection.granted_scopes,
            frozenset({"youtube.readonly", "youtube.force-ssl"}),
        )
        self.assertEqual(
            self.registry.list_connections(owner_id="person-a", platform="youtube"),
            (connection,),
        )
        with self.assertRaises(KeyError):
            self.registry.get_connection(connection.id, owner_id="person-b")

        self.store.close()
        database_bytes = b"".join(path.read_bytes() for path in Path(self.temp.name).glob("connections.db*"))
        self.store = SQLiteStore(self.path)
        self.assertNotIn(b"channel-private-123", database_bytes)
        self.assertNotIn(b"private-handle", database_bytes)
        self.assertNotIn(b"agentcore-identity/provider/person-a/youtube", database_bytes)

    def test_connection_rejects_credentials_and_authenticates_bound_row_fields(self) -> None:
        with self.assertRaises(SecretMaterialRejected):
            self.registry.register_connection(
                owner_id="person-a",
                platform="youtube",
                external_subject="channel-private-123",
                credential_ref="identity-ref",
                metadata={"nested": {"access_token": "must-not-persist"}},
                now=NOW,
            )

        value = self.registry.register_connection(
            owner_id="person-a",
            platform="youtube",
            external_subject="channel-private-123",
            credential_ref="identity-ref",
            metadata={"display_handle": "private-handle"},
            now=NOW,
        )
        with self.store.transaction() as database:
            database.execute(
                "UPDATE external_connections SET owner_id = ? WHERE id = ?",
                ("person-b", value.id),
            )
        with self.assertRaisesRegex(ValueError, "authentication failed"):
            self.registry.get_connection(value.id, owner_id="person-b")

    def test_key_rotation_reads_old_ciphertext_and_writes_with_active_key(self) -> None:
        value = self.registry.register_connection(
            owner_id="person-a",
            platform="bluesky",
            external_subject="did:plc:private",
            credential_ref="agentcore-identity/provider/person-a/bluesky",
            metadata={"granted_scopes": ["atproto"]},
            now=NOW,
        )
        rotated = EncryptedConnectionRegistry(
            self.store,
            keyring(active="key-2", include_rotated=True),
        )
        self.assertEqual(
            rotated.get_connection(value.id, owner_id="person-a").external_subject,
            "did:plc:private",
        )
        updated = rotated.update_connection(
            value.id,
            owner_id="person-a",
            expected_version=1,
            metadata={"granted_scopes": ["atproto"], "label": "rotated"},
            now=NOW + timedelta(minutes=1),
        )
        self.assertEqual(updated.version, 2)
        with self.store.transaction() as database:
            row = database.execute(
                "SELECT encryption_key_id FROM external_connections WHERE id = ?",
                (value.id,),
            ).fetchone()
        self.assertEqual(row["encryption_key_id"], "key-2")

    def test_revoked_connection_is_terminal(self) -> None:
        value = self.registry.register_connection(
            owner_id="person-a",
            platform="youtube",
            external_subject="channel-private-123",
            credential_ref="identity-ref",
            metadata={},
            now=NOW,
        )
        revoked = self.registry.revoke_connection(
            value.id,
            owner_id="person-a",
            expected_version=1,
            now=NOW + timedelta(minutes=1),
        )
        self.assertEqual(revoked.status, ConnectionStatus.REVOKED)
        self.assertIsNotNone(revoked.revoked_at)
        with self.store.transaction() as database:
            tombstone = database.execute(
                "SELECT subject_fingerprint FROM external_connections WHERE id = ?",
                (value.id,),
            ).fetchone()["subject_fingerprint"]
        self.assertTrue(tombstone.startswith(f"revoked:{value.id}:"))
        self.assertNotEqual(tombstone, self.registry.keyring.blind_index("youtube", value.external_subject))
        with self.assertRaisesRegex(ValueError, "cannot be reactivated"):
            self.registry.update_connection(
                value.id,
                owner_id="person-a",
                expected_version=2,
                status=ConnectionStatus.ACTIVE,
                now=NOW + timedelta(minutes=2),
            )

    def test_legacy_revoked_fingerprint_is_lazily_tombstoned_for_fresh_record(self) -> None:
        original = self.registry.register_connection(
            owner_id="person-a",
            platform="youtube",
            external_subject="channel-private-123",
            credential_ref="identity-ref-one",
            metadata={},
            now=NOW,
        )
        revoked = self.registry.revoke_connection(
            original.id,
            owner_id="person-a",
            expected_version=original.version,
            now=NOW + timedelta(minutes=1),
        )
        canonical = self.registry.keyring.blind_index("youtube", original.external_subject)
        with self.store.transaction() as database:
            database.execute(
                "UPDATE external_connections SET subject_fingerprint = ? WHERE id = ?",
                (canonical, original.id),
            )

        fresh = self.registry.register_connection(
            owner_id="person-a",
            platform="youtube",
            external_subject=original.external_subject,
            credential_ref="identity-ref-two",
            metadata={},
            now=NOW + timedelta(minutes=2),
        )

        with self.store.transaction() as database:
            rows = database.execute(
                "SELECT id, subject_fingerprint FROM external_connections ORDER BY created_at, id"
            ).fetchall()
        self.assertEqual(revoked.status, ConnectionStatus.REVOKED)
        self.assertEqual(fresh.status, ConnectionStatus.ACTIVE)
        self.assertNotEqual(fresh.id, original.id)
        self.assertTrue(rows[0]["subject_fingerprint"].startswith(f"revoked:{original.id}:"))
        self.assertEqual(rows[1]["subject_fingerprint"], canonical)
        self.assertNotEqual(rows[0]["subject_fingerprint"], rows[1]["subject_fingerprint"])

    def test_revoking_connection_is_non_executable_and_can_only_finish(self) -> None:
        value = self.registry.register_connection(
            owner_id="person-a",
            platform="youtube",
            external_subject="channel-private-123",
            credential_ref="identity-ref",
            metadata={},
            now=NOW,
        )
        pending = self.registry.update_connection(
            value.id,
            owner_id="person-a",
            expected_version=value.version,
            status=ConnectionStatus.REVOKING,
            now=NOW + timedelta(minutes=1),
        )

        self.assertEqual(pending.status, ConnectionStatus.REVOKING)
        self.assertEqual(self.registry.list_runtime_connections(platform="youtube"), ())
        with self.assertRaises(ConcurrencyConflict):
            self.registry.register_connection(
                owner_id="person-a",
                platform="youtube",
                external_subject=value.external_subject,
                credential_ref="replacement-ref",
                metadata={},
                now=NOW + timedelta(minutes=2),
            )
        with self.assertRaisesRegex(ValueError, "only complete revocation"):
            self.registry.update_connection(
                value.id,
                owner_id="person-a",
                expected_version=pending.version,
                status=ConnectionStatus.ACTIVE,
                now=NOW + timedelta(minutes=2),
            )
        revoked = self.registry.revoke_connection(
            value.id,
            owner_id="person-a",
            expected_version=pending.version,
            now=NOW + timedelta(minutes=2),
        )
        self.assertEqual(revoked.status, ConnectionStatus.REVOKED)

    def test_legacy_connection_status_schema_is_migrated_without_data_loss(self) -> None:
        legacy_path = Path(self.temp.name) / "legacy-connections.db"
        connection = sqlite3.connect(legacy_path)
        connection.executescript(
            """
            CREATE TABLE external_connections (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                subject_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('active', 'reauth_required', 'revoked')),
                metadata_ciphertext BLOB NOT NULL,
                metadata_nonce BLOB NOT NULL,
                encryption_key_id TEXT NOT NULL,
                version INTEGER NOT NULL CHECK(version > 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                revoked_at TEXT,
                UNIQUE(owner_id, platform, subject_fingerprint)
            );
            INSERT INTO external_connections VALUES (
                'legacy-one', 'owner-one', 'youtube', 'blind-index', 'active',
                X'01', X'02', 'legacy-key', 1,
                '2026-09-01T10:00:00+00:00', '2026-09-01T10:00:00+00:00', NULL
            );
            """
        )
        connection.close()

        migrated = SQLiteStore(legacy_path)
        try:
            with migrated.transaction() as database:
                schema = database.execute(
                    "SELECT sql FROM sqlite_master WHERE name = 'external_connections'"
                ).fetchone()["sql"]
                row = database.execute(
                    "SELECT id, status FROM external_connections"
                ).fetchone()
            self.assertIn("'revoking'", schema)
            self.assertEqual((row["id"], row["status"]), ("legacy-one", "active"))
        finally:
            migrated.close()


if __name__ == "__main__":
    unittest.main()
