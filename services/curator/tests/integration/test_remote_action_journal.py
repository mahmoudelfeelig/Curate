from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feed_passport.infrastructure.connection_registry import EncryptedConnectionRegistry
from feed_passport.infrastructure.crypto import AesGcmKeyring, SecretMaterialRejected
from feed_passport.infrastructure.sqlite_store import ConcurrencyConflict, SQLiteStore
from feed_passport.ports.action_journal import RemoteActionState


NOW = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


class RemoteActionJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "journal.db"
        self.store = SQLiteStore(self.path)
        registry = EncryptedConnectionRegistry(
            self.store,
            AesGcmKeyring(
                active_key_id="key-1",
                keys={"key-1": b"a" * 32},
                index_key=b"i" * 32,
            ),
            id_factory=lambda: "connection-one",
        )
        self.connection = registry.register_connection(
            owner_id="person-a",
            platform="youtube",
            external_subject="private-channel",
            credential_ref="identity-ref",
            metadata={"granted_scopes": ["youtube.force-ssl"]},
            now=NOW,
        )
        self.fingerprint = hashlib.sha256(b"immutable request").hexdigest()

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def reserve(self):
        return self.store.reserve_remote_action(
            attempt_id="attempt-one",
            owner_id="person-a",
            connection_id=self.connection.id,
            platform="youtube",
            migration_id="migration-one",
            action_id="action-one",
            operation="execute",
            idempotency_key="youtube-subscribe-channel-one",
            request_fingerprint=self.fingerprint,
            action_payload={"action_type": "subscribe_creator", "target": "channel-one"},
            created_at=NOW,
        )

    def test_cas_unknown_reconciliation_and_rollback_are_restart_safe(self) -> None:
        reserved = self.reserve()
        self.assertEqual(reserved.state, RemoteActionState.PENDING)
        replayed = self.store.reserve_remote_action(
            attempt_id="ignored-second-attempt-id",
            owner_id="person-a",
            connection_id=self.connection.id,
            platform="youtube",
            migration_id="migration-one",
            action_id="action-one",
            operation="execute",
            idempotency_key="youtube-subscribe-channel-one",
            request_fingerprint=self.fingerprint,
            action_payload={"action_type": "subscribe_creator", "target": "channel-one"},
            created_at=NOW,
        )
        self.assertEqual(replayed.id, reserved.id)

        dispatching = self.store.transition_remote_action(
            reserved.id,
            expected_state=RemoteActionState.PENDING,
            new_state=RemoteActionState.DISPATCHING,
            updated_at=NOW + timedelta(seconds=1),
            lease_owner="worker-a",
            lease_expires_at=NOW + timedelta(seconds=31),
        )
        self.assertEqual(dispatching.attempt_count, 1)
        unknown = self.store.transition_remote_action(
            reserved.id,
            expected_state=RemoteActionState.DISPATCHING,
            new_state=RemoteActionState.UNKNOWN,
            updated_at=NOW + timedelta(seconds=2),
            error_code="transport_timeout_after_dispatch",
        )
        self.assertEqual(unknown.state, RemoteActionState.UNKNOWN)

        self.store.close()
        self.store = SQLiteStore(self.path)
        recoverable = self.store.list_recoverable_remote_actions(NOW + timedelta(seconds=3))
        self.assertEqual([item.id for item in recoverable], [reserved.id])
        reconciling = self.store.transition_remote_action(
            reserved.id,
            expected_state=RemoteActionState.UNKNOWN,
            new_state=RemoteActionState.RECONCILING,
            updated_at=NOW + timedelta(seconds=3),
            lease_owner="reconciler-a",
            lease_expires_at=NOW + timedelta(seconds=33),
        )
        succeeded = self.store.transition_remote_action(
            reconciling.id,
            expected_state=RemoteActionState.RECONCILING,
            new_state=RemoteActionState.SUCCEEDED,
            updated_at=NOW + timedelta(seconds=4),
            platform_reference="youtube/subscription/reference",
            before_state={"subscribed": False},
            after_state={"subscribed": True},
        )
        rollback_pending = self.store.transition_remote_action(
            succeeded.id,
            expected_state=RemoteActionState.SUCCEEDED,
            new_state=RemoteActionState.ROLLBACK_PENDING,
            updated_at=NOW + timedelta(seconds=5),
            lease_owner="worker-a",
            lease_expires_at=NOW + timedelta(seconds=35),
        )
        rollback_unknown = self.store.transition_remote_action(
            rollback_pending.id,
            expected_state=RemoteActionState.ROLLBACK_PENDING,
            new_state=RemoteActionState.ROLLBACK_UNKNOWN,
            updated_at=NOW + timedelta(seconds=6),
            error_code="rollback_timeout_after_dispatch",
        )
        rolled_back = self.store.transition_remote_action(
            rollback_unknown.id,
            expected_state=RemoteActionState.ROLLBACK_UNKNOWN,
            new_state=RemoteActionState.ROLLED_BACK,
            updated_at=NOW + timedelta(seconds=7),
            after_state={"subscribed": False},
        )
        self.assertEqual(rolled_back.state, RemoteActionState.ROLLED_BACK)
        self.assertFalse(self.store.list_recoverable_remote_actions(NOW + timedelta(minutes=1)))

    def test_stale_cas_idempotency_collision_binding_and_secret_payload_fail_closed(self) -> None:
        reserved = self.reserve()
        with self.assertRaises(ValueError):
            self.store.transition_remote_action(
                reserved.id,
                expected_state=RemoteActionState.PENDING,
                new_state=RemoteActionState.SUCCEEDED,
                updated_at=NOW + timedelta(seconds=1),
            )
        self.store.transition_remote_action(
            reserved.id,
            expected_state=RemoteActionState.PENDING,
            new_state=RemoteActionState.DISPATCHING,
            updated_at=NOW + timedelta(seconds=1),
            lease_owner="worker-a",
            lease_expires_at=NOW + timedelta(seconds=31),
        )
        with self.assertRaises(ConcurrencyConflict):
            self.store.transition_remote_action(
                reserved.id,
                expected_state=RemoteActionState.PENDING,
                new_state=RemoteActionState.DISPATCHING,
                updated_at=NOW + timedelta(seconds=2),
                lease_owner="worker-b",
                lease_expires_at=NOW + timedelta(seconds=32),
            )
        with self.assertRaises(ConcurrencyConflict):
            self.store.reserve_remote_action(
                attempt_id="attempt-two",
                owner_id="person-a",
                connection_id=self.connection.id,
                platform="youtube",
                migration_id="migration-one",
                action_id="action-one",
                operation="execute",
                idempotency_key="youtube-subscribe-channel-one",
                request_fingerprint=hashlib.sha256(b"different request").hexdigest(),
                action_payload={"action_type": "subscribe_creator", "target": "channel-one"},
                created_at=NOW,
            )
        with self.assertRaises(PermissionError):
            self.store.reserve_remote_action(
                attempt_id="attempt-two",
                owner_id="person-b",
                connection_id=self.connection.id,
                platform="youtube",
                migration_id="migration-two",
                action_id="action-two",
                operation="execute",
                idempotency_key="other-key",
                request_fingerprint=self.fingerprint,
                action_payload={"target": "channel-two"},
                created_at=NOW,
            )
        with self.assertRaises(SecretMaterialRejected):
            self.store.reserve_remote_action(
                attempt_id="attempt-three",
                owner_id="person-a",
                connection_id=self.connection.id,
                platform="youtube",
                migration_id="migration-three",
                action_id="action-three",
                operation="execute",
                idempotency_key="secret-key",
                request_fingerprint=self.fingerprint,
                action_payload={"access_token": "must-not-persist"},
                created_at=NOW,
            )


if __name__ == "__main__":
    unittest.main()
