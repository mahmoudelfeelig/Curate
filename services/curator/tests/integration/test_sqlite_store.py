from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feed_passport.infrastructure import ConcurrencyConflict, SQLiteStore


NOW = datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)


class SQLiteStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "feed-passport.db"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_event_projection_survives_restart_and_conflicts_fail_closed(self) -> None:
        store = SQLiteStore(self.path)
        version = store.append_event(
            aggregate_id="passport-1",
            aggregate_type="feed_passport",
            expected_version=0,
            event_type="passport.created",
            payload={"owner_id": "person-a"},
            actor_id="person-a",
            trace_id="trace-1",
            occurred_at=NOW,
            projection_kind="passports",
            projection={"id": "passport-1", "version": 1, "intent": "useful signal"},
        )
        self.assertEqual(version, 1)
        store.close()

        reopened = SQLiteStore(self.path)
        self.assertEqual(reopened.load_events("passport-1")[0].event_type, "passport.created")
        self.assertEqual(reopened.get_projection("passports", "passport-1")[1]["intent"], "useful signal")
        with self.assertRaises(ConcurrencyConflict):
            reopened.append_event(
                aggregate_id="passport-1",
                aggregate_type="feed_passport",
                expected_version=0,
                event_type="passport.revised",
                payload={},
                actor_id="person-a",
                trace_id="trace-2",
                occurred_at=NOW + timedelta(minutes=1),
            )
        reopened.close()

    def test_idempotency_replay_and_request_collision(self) -> None:
        store = SQLiteStore(self.path)
        self.assertTrue(store.reserve_idempotency("key-1", "hash-a", NOW))
        self.assertFalse(store.reserve_idempotency("key-1", "hash-a", NOW))
        with self.assertRaises(ConcurrencyConflict):
            store.reserve_idempotency("key-1", "hash-b", NOW)
        store.complete_idempotency("key-1", {"receipt_id": "receipt-1"}, NOW + timedelta(seconds=1))
        self.assertEqual(store.idempotent_response("key-1"), {"receipt_id": "receipt-1"})
        store.close()

    def test_scheduler_claims_expiry_once(self) -> None:
        store = SQLiteStore(self.path)
        self.assertTrue(store.schedule_once("expire-visa-1", "overlay.expire", NOW, {"overlay_id": "visa-1"}))
        self.assertFalse(store.schedule_once("expire-visa-1", "overlay.expire", NOW, {"overlay_id": "visa-1"}))
        claimed = store.claim_due_jobs(NOW + timedelta(seconds=1))
        self.assertEqual(len(claimed), 1)
        self.assertFalse(store.claim_due_jobs(NOW + timedelta(seconds=2)))
        store.complete_job("expire-visa-1", NOW + timedelta(seconds=3))
        self.assertFalse(store.claim_due_jobs(NOW + timedelta(seconds=4)))
        store.close()

    def test_pending_scheduled_job_survives_restart(self) -> None:
        store = SQLiteStore(self.path)
        run_at = NOW + timedelta(minutes=15)
        store.schedule_once(
            "restart-monitor-1",
            "drift.monitor",
            run_at,
            {"monitor_id": "monitor-1"},
        )
        store.close()

        reopened = SQLiteStore(self.path)
        self.assertFalse(reopened.claim_due_jobs(run_at - timedelta(seconds=1)))
        claimed = reopened.claim_due_jobs(run_at + timedelta(seconds=1))
        self.assertEqual([item["id"] for item in claimed], ["restart-monitor-1"])
        self.assertEqual(claimed[0]["payload"], {"monitor_id": "monitor-1"})
        reopened.close()

    def test_scheduler_compares_instants_across_timezone_offsets(self) -> None:
        store = SQLiteStore(self.path)
        berlin = timezone(timedelta(hours=2))
        run_at = datetime(2026, 8, 30, 0, 16, tzinfo=berlin)
        store.schedule_once("offset-job", "overlay.expire", run_at, {"overlay_id": "visa-offset"})
        claimed = store.claim_due_jobs(datetime(2026, 8, 29, 22, 16, 1, tzinfo=timezone.utc))
        self.assertEqual([item["id"] for item in claimed], ["offset-job"])
        store.close()


if __name__ == "__main__":
    unittest.main()
