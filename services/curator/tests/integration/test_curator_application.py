from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

from feed_passport.adapters.lab import LabAdapter
from feed_passport.application import CuratorApplication
from feed_passport.domain import ActionType, OverlayMode, RollbackOutcome
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
        return f"{self.value:04d}"


class CuratorApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "application.db"
        self.clock = MutableClock(datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc))
        self.ids = IncrementingIds()
        self.store = SQLiteStore(self.path)
        self.adapter = LabAdapter()
        self.app = CuratorApplication(
            store=self.store,
            adapters={self.adapter.platform: self.adapter},
            clock=self.clock,
            id_factory=self.ids,
        )
        self.passport = self.app.create_passport(
            owner_id="person-a",
            name="Portable signal",
            intent="Research, independent games, design, and local culture without ragebait.",
            topic_targets={"research": 0.4, "indie": 0.25, "design": 0.2, "local": 0.15},
            creator_preferences={"studio-a": 1.0, "rage-farm": -1.0},
            format_preferences={"longform": 0.9},
            hard_exclusions=frozenset({"ragebait"}),
            serendipity=0.2,
            max_outrage=0.05,
            max_source_share=0.4,
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_full_migration_preview_execution_restart_and_rollback(self) -> None:
        prepared = self.app.prepare_migration(
            passport_id=self.passport.id,
            platform=self.adapter.platform,
            destination_account_id="destination-new",
            actor_id="person-a",
        )
        self.assertEqual(prepared["status"], "awaiting_approval")
        self.assertGreater(prepared["before"]["total_variation_distance"], prepared["preview"]["total_variation_distance"])
        executed = self.app.execute_migration(prepared["id"], approved_by="person-a")
        self.assertIn(executed["status"], {"issued", "issued_with_drift"})
        receipt_id = executed["receipt_id"]
        changed_ids = [
            item.id for item in self.adapter.sample("destination-new", now=self.clock(), limit=24).items
        ]

        self.store.close()
        self.store = SQLiteStore(self.path)
        restored_adapter = LabAdapter()
        self.adapter = restored_adapter
        self.app = CuratorApplication(
            store=self.store,
            adapters={restored_adapter.platform: restored_adapter},
            clock=self.clock,
            id_factory=self.ids,
        )
        restarted_ids = [
            item.id for item in restored_adapter.sample("destination-new", now=self.clock(), limit=24).items
        ]
        self.assertEqual(changed_ids, restarted_ids)
        rolled_back = self.app.rollback_receipt(
            receipt_id,
            actor_id="person-a",
            platform=restored_adapter.platform,
        )
        self.assertEqual(rolled_back["status"], "rolled_back")
        self.assertFalse(rolled_back["rollback"]["failed_actions"])

    def test_failed_rollback_stays_retryable_and_never_claims_restoration(self) -> None:
        prepared = self.app.prepare_migration(
            passport_id=self.passport.id,
            platform=self.adapter.platform,
            destination_account_id="destination-new",
            actor_id="person-a",
        )
        executed = self.app.execute_migration(prepared["id"], approved_by="person-a")
        receipt_id = executed["receipt_id"]
        receipt = self.app.projection_get("receipts", receipt_id)
        failed_action_id = receipt["outcomes"][0]["action"]["id"]
        failed_outcome = RollbackOutcome(
            receipt_id=receipt_id,
            destination_id="destination-new",
            restored_actions=(),
            failed_actions=(failed_action_id,),
            completed_at=self.clock(),
            caveats=("Synthetic failure for application-state regression coverage.",),
        )

        with patch.object(LabAdapter, "rollback", return_value=failed_outcome):
            failed = self.app.rollback_receipt(
                receipt_id,
                actor_id="person-a",
                platform=self.adapter.platform,
            )
        self.assertEqual(failed["status"], "rollback_failed")
        self.assertEqual(failed["rollback"]["failed_actions"], [failed_action_id])
        self.assertNotEqual(
            self.app.projection_get("receipts", receipt_id)["status"],
            "rolled_back",
        )

        retried = self.app.rollback_receipt(
            receipt_id,
            actor_id="person-a",
            platform=self.adapter.platform,
        )
        self.assertEqual(retried["status"], "rolled_back")
        self.assertEqual(retried["rollback"]["failed_actions"], [])

    def test_temporary_visa_expires_once_and_base_passport_is_unchanged(self) -> None:
        visa = self.app.create_overlay(
            base_passport_id=self.passport.id,
            name="Three-hour sports detour",
            topic_adjustments={"sports": 0.45, "research": -0.2},
            add_exclusions=frozenset({"spoilers"}),
            remove_exclusions=frozenset(),
            starts_at=self.clock(),
            expires_at=self.clock() + timedelta(hours=3),
            mode=OverlayMode.ISOLATED,
            serendipity=0.35,
            max_outrage=0.03,
            actor_id="person-a",
        )
        effective = self.app.effective_passport(self.passport.id)
        self.assertIn("sports", effective.topic_targets)
        self.assertNotIn("sports", self.app.get_passport(self.passport.id).topic_targets)
        self.clock.value += timedelta(hours=3, seconds=1)
        first = self.app.process_due_jobs()
        second = self.app.process_due_jobs()
        self.assertEqual(first[0]["overlay_id"], visa["id"])
        self.assertFalse(second)
        self.assertEqual(self.app.projection_get("overlays", visa["id"])["status"], "expired")
        self.assertNotIn("sports", self.app.effective_passport(self.passport.id).topic_targets)

    def test_temporary_visa_can_be_revoked_early_and_stays_revoked_when_due(self) -> None:
        visa = self.app.create_overlay(
            base_passport_id=self.passport.id,
            name="Quiet afternoon",
            topic_adjustments={"research": 0.2},
            add_exclusions=frozenset({"short_form"}),
            remove_exclusions=frozenset(),
            starts_at=self.clock(),
            expires_at=self.clock() + timedelta(hours=2),
            mode=OverlayMode.ISOLATED,
            serendipity=0.1,
            max_outrage=0.02,
            actor_id="person-a",
        )
        revoked = self.app.revoke_overlay(visa["id"], actor_id="person-a")
        self.assertEqual(revoked["status"], "revoked")
        self.assertNotIn("short_form", self.app.effective_passport(self.passport.id).hard_exclusions)
        with self.assertRaises(PermissionError):
            self.app.revoke_overlay(visa["id"], actor_id="person-b")

        self.clock.value += timedelta(hours=2, seconds=1)
        due = self.app.process_due_jobs()
        self.assertEqual(due[0]["status"], "revoked")
        self.assertEqual(self.app.projection_get("overlays", visa["id"])["status"], "revoked")

    def test_reversible_live_visa_rolls_back_its_activation_receipt_on_expiry(self) -> None:
        before_ids = [item.id for item in self.adapter.sample("destination-new", now=self.clock(), limit=24).items]
        visa = self.app.create_overlay(
            base_passport_id=self.passport.id,
            name="Research sprint",
            topic_adjustments={"research": 0.35, "ragebait": -0.5},
            add_exclusions=frozenset({"ragebait"}),
            remove_exclusions=frozenset(),
            starts_at=self.clock(),
            expires_at=self.clock() + timedelta(hours=2),
            mode=OverlayMode.REVERSIBLE_LIVE,
            serendipity=0.15,
            max_outrage=0.02,
            actor_id="person-a",
        )
        migration = self.app.prepare_migration(
            passport_id=self.passport.id,
            platform=self.adapter.platform,
            destination_account_id="destination-new",
            actor_id="person-a",
            overlay_id=visa["id"],
        )
        self.clock.value += timedelta(seconds=1)
        executed = self.app.execute_migration(migration["id"], approved_by="person-a")
        self.assertTrue(self.app.projection_get("overlays", visa["id"])["activations"])
        changed_ids = [item.id for item in self.adapter.sample("destination-new", now=self.clock(), limit=24).items]
        self.assertNotEqual(before_ids, changed_ids)

        self.clock.value += timedelta(hours=2, seconds=1)
        result = self.app.process_due_jobs()
        self.assertEqual(result[0]["rollbacks"][0]["receipt_id"], executed["receipt_id"])
        restored_ids = [item.id for item in self.adapter.sample("destination-new", now=self.clock(), limit=24).items]
        self.assertEqual(before_ids, restored_ids)
        overlay = self.app.projection_get("overlays", visa["id"])
        self.assertEqual(overlay["status"], "expired")
        self.assertEqual(overlay["activations"][0]["status"], "rolled_back")

    def test_partner_slice_is_explicit_revokeable_and_private(self) -> None:
        other = self.app.create_passport(
            owner_id="person-b",
            name="Music and design",
            intent="Music and design discovery.",
            topic_targets={"music": 0.7, "design": 0.3},
            creator_preferences={"radio-night": 1.0},
            hard_exclusions=frozenset({"private-health-topic"}),
        )
        expires = self.clock() + timedelta(hours=8)
        first = self.app.create_share_slice(
            passport_id=self.passport.id,
            topic_names=("design", "indie"),
            creator_ids=("studio-a",),
            include_serendipity=True,
            include_formats=True,
            include_exclusions=True,
            expires_at=expires,
            actor_id="person-a",
        )
        second = self.app.create_share_slice(
            passport_id=other.id,
            topic_names=("design", "music"),
            creator_ids=("radio-night",),
            include_serendipity=True,
            expires_at=expires,
            actor_id="person-b",
        )
        blend = self.app.create_companion_blend(
            name="Weekend bridge",
            slice_ids=(first["id"], second["id"]),
            weights={"person-a": 1.0, "person-b": 1.0},
            strategy="bridge",
            expires_at=expires,
            actor_id="person-a",
        )
        self.assertNotIn("private-health-topic", blend["topic_targets"])
        self.assertEqual(blend["format_preferences"], {"longform": 0.9})
        self.assertEqual(blend["hard_exclusions"], ["ragebait"])
        revoked = self.app.revoke_share_slice(first["id"], actor_id="person-a")
        self.assertEqual(revoked["status"], "revoked")
        self.assertEqual(
            self.app.projection_get("companions", blend["id"])["status"],
            "consent_invalidated",
        )
        with self.assertRaises(PermissionError):
            self.app.revoke_share_slice(second["id"], actor_id="person-a")

    def test_checkpoint_restores_preferences_as_a_new_version(self) -> None:
        checkpoint = self.app.create_checkpoint(
            self.passport.id,
            actor_id="person-a",
            label="Before conference",
        )
        revised = self.app.revise_passport(
            self.passport.id,
            actor_id="person-a",
            changes={"topic_targets": {"music": 0.8, "design": 0.2}, "serendipity": 0.6},
        )
        restored = self.app.restore_checkpoint(checkpoint["id"], actor_id="person-a")
        self.assertEqual(restored.version, revised.version + 1)
        self.assertEqual(dict(restored.topic_targets), dict(self.passport.topic_targets))
        self.assertEqual(restored.serendipity, self.passport.serendipity)
        self.assertEqual(
            self.app.projection_get("checkpoints", checkpoint["id"])["restored_as_version"],
            restored.version,
        )

    def test_share_and_companion_expiry_remove_the_active_blend(self) -> None:
        partner = self.app.create_passport(
            owner_id="person-b",
            name="Partner snapshot",
            intent="Design and music discovery.",
            topic_targets={"design": 0.4, "music": 0.6},
        )
        expires_at = self.clock() + timedelta(hours=1)
        own_slice = self.app.create_share_slice(
            passport_id=self.passport.id,
            topic_names=("design",),
            creator_ids=("studio-a",),
            include_serendipity=False,
            include_formats=False,
            include_exclusions=True,
            expires_at=expires_at,
            actor_id="person-a",
        )
        partner_slice = self.app.create_share_slice(
            passport_id=partner.id,
            topic_names=("design", "music"),
            creator_ids=(),
            include_serendipity=False,
            include_formats=False,
            include_exclusions=False,
            expires_at=expires_at,
            actor_id="person-b",
        )
        companion = self.app.create_companion_blend(
            name="One-hour bridge",
            slice_ids=(own_slice["id"], partner_slice["id"]),
            weights={"person-a": 1.0, "person-b": 1.0},
            strategy="bridge",
            expires_at=expires_at,
            actor_id="person-a",
        )

        self.clock.value = expires_at + timedelta(seconds=1)
        processed = self.app.process_due_jobs()

        self.assertEqual(len(processed), 3)
        self.assertEqual(self.app.projection_get("shares", own_slice["id"])["status"], "expired")
        self.assertEqual(self.app.projection_get("shares", partner_slice["id"])["status"], "expired")
        self.assertIn(
            self.app.projection_get("companions", companion["id"])["status"],
            {"expired", "consent_invalidated"},
        )

    def test_drift_creator_continuity_and_templates_are_exposed(self) -> None:
        drift = self.app.drift_watch(
            passport_id=self.passport.id,
            platform=self.adapter.platform,
            account_id="destination-new",
            actor_id="person-a",
        )
        self.assertEqual(drift["status"], "decision_required")
        match = self.app.creator_continuity("studio-a", "youtube")
        self.assertEqual(match["status"], "verified")
        self.assertEqual(match["match"], "@studio-a")
        visa = self.app.create_overlay_from_template(
            "template-deep-work",
            base_passport_id=self.passport.id,
            actor_id="person-a",
        )
        self.assertEqual(visa["name"], "Deep work")

    def test_creator_match_is_persisted_idempotently_after_owner_confirmation(self) -> None:
        first = self.app.preserve_creator_match(
            passport_id=self.passport.id,
            creator_id="creator-studio-a",
            destination_platform="youtube",
            actor_id="person-a",
        )
        second = self.app.preserve_creator_match(
            passport_id=self.passport.id,
            creator_id="creator-studio-a",
            destination_platform="youtube",
            actor_id="person-a",
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["destination_identity"], "@studio-a")
        with self.assertRaises(PermissionError):
            self.app.preserve_creator_match(
                passport_id=self.passport.id,
                creator_id="creator-studio-a",
                destination_platform="youtube",
                actor_id="person-b",
            )

    def test_exported_passport_imports_as_a_new_owned_identity(self) -> None:
        exported = self.app.export_passport(self.passport.id)
        imported = self.app.import_passport(
            actor_id="person-a",
            format_name="feed-passport/v1",
            passport_data=exported,
        )
        self.assertNotEqual(imported["passport"]["id"], self.passport.id)
        self.assertEqual(imported["source_passport_id"], exported["passport_id"])
        self.assertNotEqual(imported["source_passport_id"], self.passport.id)
        self.assertEqual(imported["passport"]["version"], 1)
        unsafe = {**exported, "raw_history": ["private-event"]}
        with self.assertRaises(ValueError):
            self.app.import_passport(
                actor_id="person-a",
                format_name="feed-passport/v1",
                passport_data=unsafe,
            )

    def test_bounded_drift_monitor_corrects_only_certified_actions_and_can_be_stopped(self) -> None:
        monitor = self.app.create_drift_monitor(
            passport_id=self.passport.id,
            platform=self.adapter.platform,
            account_id="destination-new",
            actor_id="person-a",
            interval_minutes=15,
            expires_at=self.clock() + timedelta(hours=1),
            mode="bounded_auto",
            allowed_actions=frozenset(
                {
                    ActionType.SET_TOPIC_PREFERENCE,
                    ActionType.HIDE_TOPIC,
                    ActionType.FOLLOW_CREATOR,
                    ActionType.MUTE_CREATOR,
                    ActionType.SET_SERENDIPITY,
                    ActionType.SET_SOURCE_CAP,
                }
            ),
            max_actions_per_run=3,
        )
        self.clock.value += timedelta(minutes=15)
        checked = self.app.process_due_jobs()
        self.assertEqual(checked[0]["status"], "checked")
        self.assertIsNotNone(checked[0]["receipt_id"])
        current = self.app.projection_get("drift_monitors", monitor["id"])
        self.assertEqual(current["run_count"], 1)
        self.assertIsNotNone(current["next_run_at"])

        stopped = self.app.stop_drift_monitor(monitor["id"], actor_id="person-a")
        self.assertEqual(stopped["status"], "stopped")
        self.clock.value += timedelta(minutes=15)
        due_after_stop = self.app.process_due_jobs()
        self.assertEqual(due_after_stop[0]["status"], "stopped")


if __name__ == "__main__":
    unittest.main()
