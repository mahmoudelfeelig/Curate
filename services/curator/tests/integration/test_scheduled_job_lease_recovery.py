from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feed_passport.infrastructure.sqlite_store import ConcurrencyConflict, SQLiteStore


NOW = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


class ScheduledJobLeaseRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "jobs.db"
        self.store = SQLiteStore(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_expired_claim_is_reclaimed_and_final_expired_lease_is_bounded(self) -> None:
        self.assertTrue(
            self.store.schedule_once(
                "rollback-visa",
                "overlay.expire",
                NOW,
                {"overlay_id": "visa-one"},
                max_attempts=2,
            )
        )
        first = self.store.claim_due_jobs(
            NOW,
            worker_id="worker-a",
            lease_for=timedelta(seconds=10),
        )[0]
        self.assertEqual(first["attempt_count"], 1)
        self.assertFalse(
            self.store.claim_due_jobs(
                NOW + timedelta(seconds=9),
                worker_id="worker-b",
                lease_for=timedelta(seconds=10),
            )
        )
        second = self.store.claim_due_jobs(
            NOW + timedelta(seconds=11),
            worker_id="worker-b",
            lease_for=timedelta(seconds=10),
        )[0]
        self.assertEqual(second["attempt_count"], 2)
        self.assertNotEqual(first["claim_token"], second["claim_token"])

        self.assertFalse(
            self.store.claim_due_jobs(
                NOW + timedelta(seconds=22),
                worker_id="worker-c",
            )
        )
        failed = self.store.get_scheduled_job("rollback-visa")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"], "lease_expired_after_final_attempt")

    def test_transient_failure_retries_with_claim_token_and_then_completes(self) -> None:
        self.store.schedule_once(
            "drift-one",
            "drift.monitor",
            NOW,
            {"monitor_id": "monitor-one"},
            max_attempts=3,
        )
        first = self.store.claim_due_jobs(NOW, worker_id="worker-a")[0]
        with self.assertRaises(ConcurrencyConflict):
            self.store.complete_job(
                first["id"],
                NOW + timedelta(seconds=1),
                claim_token="wrong-token",
            )
        self.store.complete_job(
            first["id"],
            NOW + timedelta(seconds=1),
            error="TransportTimeout",
            claim_token=first["claim_token"],
            retry_delay_seconds=0,
        )
        self.assertEqual(self.store.get_scheduled_job(first["id"])["status"], "retryable")
        second = self.store.claim_due_jobs(
            NOW + timedelta(seconds=1),
            worker_id="worker-b",
        )[0]
        self.store.complete_job(
            second["id"],
            NOW + timedelta(seconds=2),
            claim_token=second["claim_token"],
        )
        completed = self.store.get_scheduled_job(second["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["attempt_count"], 2)

    def test_existing_scheduler_schema_is_upgraded_without_losing_pending_jobs(self) -> None:
        self.store.close()
        legacy_path = Path(self.temp.name) / "legacy.db"
        database = sqlite3.connect(legacy_path)
        database.executescript(
            """
            CREATE TABLE scheduled_jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                run_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                claimed_at TEXT,
                completed_at TEXT,
                error TEXT
            );
            INSERT INTO scheduled_jobs (id, kind, run_at, payload_json, status)
            VALUES ('legacy-one', 'overlay.expire', '2026-09-01T10:00:00+00:00', '{}', 'pending');
            """
        )
        database.close()

        upgraded = SQLiteStore(legacy_path)
        try:
            claimed = upgraded.claim_due_jobs(NOW, worker_id="upgrade-worker")
            self.assertEqual([item["id"] for item in claimed], ["legacy-one"])
            self.assertEqual(claimed[0]["attempt_count"], 1)
            self.assertEqual(claimed[0]["max_attempts"], 3)
        finally:
            upgraded.close()
        self.store = SQLiteStore(self.path)


if __name__ == "__main__":
    unittest.main()
