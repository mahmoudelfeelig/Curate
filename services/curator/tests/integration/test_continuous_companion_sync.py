from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feed_passport.adapters.lab import LabAdapter
from feed_passport.application import CuratorApplication
from feed_passport.application.curator import InvalidStateError, NotFoundError
from feed_passport.infrastructure import SQLiteStore
from feed_passport.infrastructure.serialization import to_primitive


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class IncrementingIds:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"sync-{self.value:04d}"


class ContinuousCompanionSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "continuous-companion.db"
        self.clock = MutableClock(datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc))
        self.ids = IncrementingIds()
        self.store = SQLiteStore(self.path)
        self.adapter = LabAdapter()
        self.app = CuratorApplication(
            store=self.store,
            adapters={self.adapter.platform: self.adapter},
            clock=self.clock,
            id_factory=self.ids,
        )
        self.first = self.app.create_passport(
            owner_id="person-a",
            name="Research route",
            intent="Research and design without ragebait.",
            topic_targets={"research": 0.6, "design": 0.4},
            creator_preferences={"studio-a": 1.0, "private-a": -1.0},
            format_preferences={"longform": 0.9},
            hard_exclusions=frozenset({"ragebait"}),
            serendipity=0.2,
            max_outrage=0.04,
            max_source_share=0.35,
        )
        self.second = self.app.create_passport(
            owner_id="person-b",
            name="Music route",
            intent="Music and design with a private exclusion.",
            topic_targets={"design": 0.2, "music": 0.8},
            creator_preferences={"radio-night": 1.0},
            format_preferences={"video": 0.7},
            hard_exclusions=frozenset({"private-health-topic"}),
            serendipity=0.5,
            max_outrage=0.08,
            max_source_share=0.45,
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def create_continuous_companion(self, *, expires_at: datetime | None = None) -> tuple[dict, dict, dict]:
        expiry = expires_at or self.clock() + timedelta(hours=8)
        first_slice = self.app.create_share_slice(
            passport_id=self.first.id,
            topic_names=("research", "design"),
            creator_ids=("studio-a",),
            include_serendipity=True,
            include_formats=True,
            include_exclusions=True,
            expires_at=expiry,
            actor_id="person-a",
            scope="continuous",
            refresh_on_revision=True,
            target_passport_ids=(self.first.id,),
            pair_id="pair-person-a-person-b",
            counterparty_owner_id="person-b",
        )
        second_slice = self.app.create_share_slice(
            passport_id=self.second.id,
            topic_names=("design", "music"),
            creator_ids=("radio-night",),
            include_serendipity=False,
            include_formats=False,
            include_exclusions=False,
            expires_at=expiry,
            actor_id="person-b",
            scope="continuous",
            refresh_on_revision=True,
            target_passport_ids=(self.second.id,),
            pair_id="pair-person-a-person-b",
            counterparty_owner_id="person-a",
        )
        companion = self.app.create_companion_blend(
            name="Continuously shared lane",
            slice_ids=(first_slice["id"], second_slice["id"]),
            weights={"person-a": 1.0, "person-b": 1.0},
            strategy="weighted",
            expires_at=expiry,
            actor_id="person-a",
            scope="continuous",
        )
        return first_slice, second_slice, companion

    def test_continuous_blend_applies_only_selected_fields_without_mutating_bases(self) -> None:
        base_first = to_primitive(self.app.get_passport(self.first.id))
        base_second = to_primitive(self.app.get_passport(self.second.id))
        first_slice, second_slice, companion = self.create_continuous_companion()

        self.assertEqual(companion["scope"], "continuous")
        self.assertTrue(companion["refresh_on_revision"])
        self.assertEqual(companion["sync_revision"], 1)
        self.assertEqual(set(companion["source_passport_ids"]), {self.first.id, self.second.id})
        self.assertEqual(set(companion["target_passport_ids"]), {self.first.id, self.second.id})
        self.assertEqual(
            companion["source_passport_versions"],
            {self.first.id: 1, self.second.id: 1},
        )
        self.assertEqual(len(set(companion["consent_ids"])), 2)
        self.assertNotEqual(first_slice["consent_id"], second_slice["consent_id"])
        self.assertEqual(
            first_slice["selected_fields"],
            {
                "topic_names": ["design", "research"],
                "creator_ids": ["studio-a"],
                "include_serendipity": True,
                "include_formats": True,
                "include_exclusions": True,
            },
        )

        effective_first = self.app.effective_passport(self.first.id)
        effective_second = self.app.effective_passport(self.second.id)
        self.assertIn("music", effective_first.topic_targets)
        self.assertIn("research", effective_second.topic_targets)
        self.assertIn("radio-night", effective_first.creator_preferences)
        self.assertIn("studio-a", effective_second.creator_preferences)
        self.assertIn("ragebait", effective_second.hard_exclusions)
        self.assertNotIn("private-health-topic", effective_first.hard_exclusions)
        self.assertEqual(to_primitive(self.app.get_passport(self.first.id)), base_first)
        self.assertEqual(to_primitive(self.app.get_passport(self.second.id)), base_second)

    def test_revision_recomputes_persists_and_survives_sqlite_restart(self) -> None:
        first_slice, second_slice, companion = self.create_continuous_companion()
        before = self.app.effective_passport(self.first.id)
        self.app.revise_passport(
            self.second.id,
            actor_id="person-b",
            changes={"topic_targets": {"design": 0.1, "music": 0.2, "science": 0.7}},
        )

        synced = self.app.projection_get("companions", companion["id"])
        refreshed_slice = self.app.projection_get("shares", second_slice["id"])
        after = self.app.effective_passport(self.first.id)
        self.assertEqual(synced["sync_revision"], 2)
        self.assertEqual(synced["source_passport_versions"][self.second.id], 2)
        self.assertEqual(refreshed_slice["passport_version"], 2)
        self.assertNotEqual(after.topic_targets["music"], before.topic_targets["music"])
        self.assertNotIn("science", after.topic_targets)

        self.app.revise_passport(
            self.first.id,
            actor_id="person-a",
            changes={"topic_targets": {"research": 0.2, "design": 0.8}},
        )
        synced_from_either_source = self.app.projection_get("companions", companion["id"])
        self.assertEqual(synced_from_either_source["sync_revision"], 3)
        self.assertEqual(
            synced_from_either_source["source_passport_versions"],
            {self.first.id: 2, self.second.id: 2},
        )
        self.assertEqual(self.app.projection_get("shares", first_slice["id"])["passport_version"], 2)
        persisted_topics = dict(self.app.effective_passport(self.first.id).topic_targets)

        self.store.close()
        self.store = SQLiteStore(self.path)
        self.adapter = LabAdapter()
        self.app = CuratorApplication(
            store=self.store,
            adapters={self.adapter.platform: self.adapter},
            clock=self.clock,
            id_factory=self.ids,
        )

        restarted = self.app.projection_get("companions", companion["id"])
        self.assertEqual(restarted["sync_revision"], 3)
        self.assertEqual(restarted["source_passport_versions"][self.second.id], 2)
        self.assertEqual(restarted["source_passport_versions"][self.first.id], 2)
        self.assertEqual(dict(self.app.effective_passport(self.first.id).topic_targets), persisted_topics)

    def test_revocation_or_expiry_removes_the_effective_blend_immediately(self) -> None:
        first_slice, _, companion = self.create_continuous_companion(
            expires_at=self.clock() + timedelta(hours=1)
        )
        self.assertIn("music", self.app.effective_passport(self.first.id).topic_targets)
        with self.assertRaises(PermissionError):
            self.app.revoke_share_slice(first_slice["id"], actor_id="person-b")
        self.assertEqual(self.app.projection_get("companions", companion["id"])["status"], "active")

        self.app.revoke_share_slice(first_slice["id"], actor_id="person-a")
        self.assertNotIn("music", self.app.effective_passport(self.first.id).topic_targets)
        self.assertEqual(
            self.app.projection_get("companions", companion["id"])["status"],
            "consent_invalidated",
        )

        _, _, expiring = self.create_continuous_companion(expires_at=self.clock() + timedelta(hours=1))
        self.clock.value += timedelta(hours=1, seconds=1)
        self.assertNotIn("music", self.app.effective_passport(self.first.id).topic_targets)
        self.app.process_due_jobs()
        self.assertIn(
            self.app.projection_get("companions", expiring["id"])["status"],
            {"expired", "consent_invalidated"},
        )

    def test_continuous_consent_and_targets_fail_closed(self) -> None:
        expiry = self.clock() + timedelta(hours=2)
        with self.assertRaises(NotFoundError):
            self.app.create_share_slice(
                passport_id=self.first.id,
                topic_names=("design",),
                creator_ids=(),
                include_serendipity=False,
                expires_at=expiry,
                actor_id="person-a",
                scope="continuous",
                refresh_on_revision=True,
                target_passport_ids=("passport-missing",),
                pair_id="pair-person-a-person-b",
                counterparty_owner_id="person-b",
            )
        with self.assertRaises(PermissionError):
            self.app.create_share_slice(
                passport_id=self.first.id,
                topic_names=("design",),
                creator_ids=(),
                include_serendipity=False,
                expires_at=expiry,
                actor_id="person-a",
                scope="continuous",
                refresh_on_revision=True,
                target_passport_ids=(self.second.id,),
                pair_id="pair-person-a-person-b",
                counterparty_owner_id="person-b",
            )
        with self.assertRaises(ValueError):
            self.app.create_share_slice(
                passport_id=self.first.id,
                topic_names=("design",),
                creator_ids=(),
                include_serendipity=False,
                expires_at=expiry,
                actor_id="person-a",
                scope="continuous",
                refresh_on_revision=False,
                target_passport_ids=(self.first.id,),
                pair_id="pair-person-a-person-b",
                counterparty_owner_id="person-b",
            )

        first_slice, second_slice, _ = self.create_continuous_companion(expires_at=expiry)

        third = self.app.create_passport(
            owner_id="person-c",
            name="Third route",
            intent="A separate consent pair for negative coverage.",
            topic_targets={"design": 1.0},
        )
        wrong_pair_slice = self.app.create_share_slice(
            passport_id=third.id,
            topic_names=("design",),
            creator_ids=(),
            include_serendipity=False,
            expires_at=expiry,
            actor_id="person-c",
            scope="continuous",
            refresh_on_revision=True,
            target_passport_ids=(third.id,),
            pair_id="pair-person-b-person-c",
            counterparty_owner_id="person-b",
        )
        with self.assertRaisesRegex(InvalidStateError, "same pair|reciprocal"):
            self.app.create_companion_blend(
                name="Cross-pair splice",
                slice_ids=(first_slice["id"], wrong_pair_slice["id"]),
                weights={"person-a": 1.0, "person-c": 1.0},
                strategy="weighted",
                expires_at=expiry,
                actor_id="person-a",
                scope="continuous",
            )
        with self.assertRaises(PermissionError):
            self.app.create_companion_blend(
                name="Impersonated",
                slice_ids=(first_slice["id"], second_slice["id"]),
                weights={"person-a": 1.0, "person-b": 1.0},
                strategy="weighted",
                expires_at=expiry,
                actor_id="person-c",
                scope="continuous",
            )
        with self.assertRaises(NotFoundError):
            self.app.create_companion_blend(
                name="Missing consent",
                slice_ids=(first_slice["id"], "slice-missing"),
                weights={"person-a": 1.0, "person-b": 1.0},
                strategy="weighted",
                expires_at=expiry,
                actor_id="person-a",
                scope="continuous",
            )
        self.clock.value = expiry + timedelta(seconds=1)
        with self.assertRaises(InvalidStateError):
            self.app.create_companion_blend(
                name="Expired consent",
                slice_ids=(first_slice["id"], second_slice["id"]),
                weights={"person-a": 1.0, "person-b": 1.0},
                strategy="weighted",
                expires_at=expiry + timedelta(hours=1),
                actor_id="person-a",
                scope="continuous",
            )

    def test_snapshot_mode_remains_pinned_and_does_not_change_effective_passports(self) -> None:
        expiry = self.clock() + timedelta(hours=4)
        first_slice = self.app.create_share_slice(
            passport_id=self.first.id,
            topic_names=("research", "design"),
            creator_ids=(),
            include_serendipity=False,
            expires_at=expiry,
            actor_id="person-a",
        )
        second_slice = self.app.create_share_slice(
            passport_id=self.second.id,
            topic_names=("design", "music"),
            creator_ids=(),
            include_serendipity=False,
            expires_at=expiry,
            actor_id="person-b",
        )
        snapshot = self.app.create_companion_blend(
            name="Pinned comparison",
            slice_ids=(first_slice["id"], second_slice["id"]),
            weights={"person-a": 1.0, "person-b": 1.0},
            strategy="weighted",
            expires_at=expiry,
            actor_id="person-a",
        )
        self.assertEqual(snapshot["scope"], "snapshot")
        self.assertEqual(
            to_primitive(self.app.effective_passport(self.first.id)),
            to_primitive(self.app.get_passport(self.first.id)),
        )

    def test_migration_uses_current_sync_and_stale_preview_cannot_execute(self) -> None:
        self.create_continuous_companion()
        effective = self.app.effective_passport(self.first.id)
        migration = self.app.prepare_migration(
            passport_id=self.first.id,
            platform=self.adapter.platform,
            destination_account_id="destination-new",
            actor_id="person-a",
        )
        self.assertEqual(migration["passport_version"], effective.version)
        topic_actions = {
            action["target"]: action
            for action in migration["plan"]["actions"]
            if action["action_type"] == "set_topic_preference"
        }
        self.assertAlmostEqual(
            topic_actions["music"]["parameters"]["weight"],
            effective.topic_targets["music"],
        )

        self.app.revise_passport(
            self.second.id,
            actor_id="person-b",
            changes={"topic_targets": {"design": 0.7, "music": 0.3}},
        )
        with self.assertRaises(InvalidStateError):
            self.app.execute_migration(migration["id"], approved_by="person-a")

        current = self.app.effective_passport(self.first.id)
        fresh = self.app.prepare_migration(
            passport_id=self.first.id,
            platform=self.adapter.platform,
            destination_account_id="destination-new",
            actor_id="person-a",
        )
        self.assertEqual(fresh["passport_version"], current.version)

    def test_migration_fingerprint_rejects_expiry_even_when_integer_versions_collide(self) -> None:
        expires_at = self.clock() + timedelta(hours=1)
        self.create_continuous_companion(expires_at=expires_at)
        migration = self.app.prepare_migration(
            passport_id=self.first.id,
            platform=self.adapter.platform,
            destination_account_id="destination-new",
            actor_id="person-a",
        )
        self.assertEqual(migration["passport_version"], 2)

        self.clock.value = expires_at + timedelta(seconds=1)
        revised = self.app.revise_passport(
            self.first.id,
            actor_id="person-a",
            changes={"topic_targets": {"research": 0.2, "design": 0.8}},
        )
        self.assertEqual(revised.version, migration["passport_version"])
        with self.assertRaises(InvalidStateError):
            self.app.execute_migration(migration["id"], approved_by="person-a")


if __name__ == "__main__":
    unittest.main()
