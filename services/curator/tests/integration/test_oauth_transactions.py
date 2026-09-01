from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feed_passport.infrastructure.connection_registry import (
    EncryptedConnectionRegistry,
    OAuthTransactionError,
)
from feed_passport.infrastructure.crypto import AesGcmKeyring, SecretMaterialRejected
from feed_passport.infrastructure.sqlite_store import SQLiteStore


NOW = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
STATE = "state-value-with-more-than-thirty-two-characters"


class OAuthTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "oauth.db"
        self.store = SQLiteStore(self.path)
        self.keyring = AesGcmKeyring(
            active_key_id="key-1",
            keys={"key-1": b"a" * 32},
            index_key=b"i" * 32,
        )
        self.registry = EncryptedConnectionRegistry(
            self.store,
            self.keyring,
            state_factory=lambda: STATE,
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_state_is_hashed_principal_bound_one_time_and_restart_safe(self) -> None:
        started = self.registry.begin_oauth_transaction(
            owner_id="person-a",
            platform="youtube",
            redirect_uri="https://feed-passport.example/oauth/youtube/callback",
            metadata={"pkce_verifier": "encrypted-pkce-value", "nonce": "encrypted-nonce"},
            now=NOW,
        )
        self.assertEqual(started.state, STATE)
        with self.store.transaction() as database:
            row = database.execute("SELECT * FROM oauth_transactions").fetchone()
        self.assertEqual(row["state_hash"], hashlib.sha256(STATE.encode()).hexdigest())
        self.assertNotEqual(row["state_hash"], STATE)

        with self.assertRaisesRegex(OAuthTransactionError, "principal and platform"):
            self.registry.consume_oauth_transaction(
                STATE,
                owner_id="person-b",
                platform="youtube",
                now=NOW + timedelta(minutes=1),
            )

        self.store.close()
        self.store = SQLiteStore(self.path)
        self.registry = EncryptedConnectionRegistry(self.store, self.keyring)
        consumed = self.registry.consume_oauth_transaction(
            STATE,
            owner_id="person-a",
            platform="youtube",
            now=NOW + timedelta(minutes=1),
        )
        self.assertEqual(consumed.metadata["pkce_verifier"], "encrypted-pkce-value")
        with self.assertRaisesRegex(OAuthTransactionError, "already been consumed"):
            self.registry.consume_oauth_transaction(
                STATE,
                owner_id="person-a",
                platform="youtube",
                now=NOW + timedelta(minutes=2),
            )

    def test_expired_state_and_credential_fields_fail_closed(self) -> None:
        self.registry.begin_oauth_transaction(
            owner_id="person-a",
            platform="youtube",
            redirect_uri="https://feed-passport.example/oauth/youtube/callback",
            metadata={"pkce_verifier": "encrypted-pkce-value"},
            now=NOW,
            ttl=timedelta(minutes=1),
        )
        with self.assertRaisesRegex(OAuthTransactionError, "expired"):
            self.registry.consume_oauth_transaction(
                STATE,
                owner_id="person-a",
                platform="youtube",
                now=NOW + timedelta(minutes=1),
            )

        with self.assertRaises(SecretMaterialRejected):
            self.registry.begin_oauth_transaction(
                owner_id="person-a",
                platform="youtube",
                redirect_uri="https://feed-passport.example/oauth/youtube/callback",
                metadata={"refresh-token": "must-not-persist"},
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
