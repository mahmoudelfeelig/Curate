from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from feed_passport.domain import (
    ActionEnvelope,
    ActionLedger,
    ActionOutcome,
    ActionStatus,
    ActionType,
    CapabilityLevel,
    FeedItem,
    FeedPassport,
    FeedSample,
    OverlayMode,
    PassportOverlay,
    PlatformCapabilityManifest,
    PolicyGuard,
    ProposedAction,
    ShareablePassportSlice,
    StopReason,
    apply_overlay,
    blend_slices,
    evaluate_feed,
)


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def passport() -> FeedPassport:
    return FeedPassport(
        id="passport-1",
        owner_id="person-a",
        name="Useful internet",
        version=1,
        intent="Research, independent games, design, and local culture.",
        topic_targets={"research": 0.4, "indie": 0.25, "design": 0.2, "local": 0.15},
        creator_preferences={"studio-a": 1.0},
        hard_exclusions=frozenset({"ragebait"}),
        serendipity=0.2,
        max_outrage=0.05,
        max_source_share=0.4,
        created_at=NOW,
        updated_at=NOW,
    )


class PassportTests(unittest.TestCase):
    def test_topics_are_normalized_and_revisions_are_versioned(self) -> None:
        current = passport()
        self.assertAlmostEqual(sum(current.topic_targets.values()), 1.0)
        revised = current.revise(updated_at=NOW + timedelta(minutes=1), topic_targets={"research": 3, "design": 1})
        self.assertEqual(revised.version, 2)
        self.assertEqual(dict(revised.topic_targets), {"design": 0.25, "research": 0.75})
        self.assertEqual(current.version, 1)

    def test_temporary_overlay_is_active_only_inside_window_and_keeps_base_immutable(self) -> None:
        base = passport()
        overlay = PassportOverlay(
            id="visa-1",
            base_passport_id=base.id,
            name="Conference mode",
            topic_adjustments={"research": -0.2, "local": 0.4},
            add_exclusions=frozenset({"spoilers"}),
            remove_exclusions=frozenset(),
            starts_at=NOW,
            expires_at=NOW + timedelta(hours=3),
            mode=OverlayMode.ISOLATED,
            serendipity=0.35,
        )
        active = apply_overlay(base, overlay, NOW + timedelta(minutes=1))
        self.assertEqual(active.version, 2)
        self.assertIn("spoilers", active.hard_exclusions)
        self.assertNotIn("spoilers", base.hard_exclusions)
        self.assertAlmostEqual(sum(active.topic_targets.values()), 1.0)
        with self.assertRaises(ValueError):
            apply_overlay(base, overlay, overlay.expires_at)

    def test_partner_blend_uses_only_explicitly_shared_fields(self) -> None:
        slices = (
            ShareablePassportSlice(
                owner_id="person-a",
                passport_id="passport-a",
                passport_version=1,
                topic_targets={"design": 0.7, "games": 0.3},
                creator_preferences={"shared-a": 1.0},
                serendipity=0.2,
                expires_at=NOW + timedelta(days=1),
                consent_id="consent-a",
                format_preferences={"longform": 0.9},
                hard_exclusions=frozenset({"ragebait"}),
            ),
            ShareablePassportSlice(
                owner_id="person-b",
                passport_id="passport-b",
                passport_version=2,
                topic_targets={"design": 0.2, "music": 0.8},
                creator_preferences={"shared-b": 0.8},
                serendipity=0.4,
                expires_at=NOW + timedelta(days=1),
                consent_id="consent-b",
                format_preferences={"video": 0.5},
                hard_exclusions=frozenset({"spoilers"}),
            ),
        )
        blend = blend_slices(
            blend_id="blend-1",
            name="Weekend lane",
            slices=slices,
            weights={"person-a": 1.0, "person-b": 1.0},
            strategy="bridge",
            created_at=NOW,
            expires_at=NOW + timedelta(hours=8),
        )
        self.assertEqual(set(blend.topic_targets), {"design", "games", "music"})
        self.assertNotIn("private-health-topic", blend.topic_targets)
        self.assertAlmostEqual(blend.serendipity, 0.3)
        self.assertEqual(set(blend.format_preferences), {"longform", "video"})
        self.assertEqual(blend.hard_exclusions, frozenset({"ragebait", "spoilers"}))

    def test_every_declared_companion_strategy_produces_a_valid_scoped_blend(self) -> None:
        slices = (
            ShareablePassportSlice(
                owner_id="person-a",
                passport_id="passport-a",
                passport_version=1,
                topic_targets={"design": 0.7, "games": 0.3},
                creator_preferences={},
                serendipity=0.2,
                expires_at=NOW + timedelta(days=1),
                consent_id="consent-a",
            ),
            ShareablePassportSlice(
                owner_id="person-b",
                passport_id="passport-b",
                passport_version=1,
                topic_targets={"design": 0.2, "music": 0.8},
                creator_preferences={},
                serendipity=0.4,
                expires_at=NOW + timedelta(days=1),
                consent_id="consent-b",
            ),
        )
        expected = {
            "weighted": {"design": 0.55, "games": 0.21, "music": 0.24},
            "taste_swap": {"design": 0.35, "games": 0.09, "music": 0.56},
        }
        for strategy in ("bridge", "weighted", "common_ground", "taste_swap"):
            with self.subTest(strategy=strategy):
                blend = blend_slices(
                    blend_id=f"blend-{strategy}",
                    name=strategy,
                    slices=slices,
                    weights={"person-a": 70.0, "person-b": 30.0},
                    strategy=strategy,
                    created_at=NOW,
                    expires_at=NOW + timedelta(hours=8),
                )
                self.assertEqual(blend.strategy, strategy)
                self.assertAlmostEqual(sum(blend.topic_targets.values()), 1.0)
                if strategy == "common_ground":
                    self.assertEqual(set(blend.topic_targets), {"design"})
                    self.assertAlmostEqual(blend.topic_targets["design"], 1.0)
                elif strategy == "bridge":
                    owner_weight = 70**0.5
                    partner_weight = 30**0.5
                    total = owner_weight + partner_weight
                    self.assertAlmostEqual(
                        blend.topic_targets["design"],
                        0.7 * owner_weight / total + 0.2 * partner_weight / total,
                    )
                    self.assertAlmostEqual(blend.topic_targets["games"], 0.3 * owner_weight / total)
                    self.assertAlmostEqual(blend.topic_targets["music"], 0.8 * partner_weight / total)
                else:
                    for topic, value in expected[strategy].items():
                        self.assertAlmostEqual(blend.topic_targets[topic], value)


class PolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capability = PlatformCapabilityManifest(
            platform="lab",
            level=CapabilityLevel.CLOSED_LOOP,
            observe=frozenset({"feed", "follows"}),
            execute=frozenset({ActionType.FOLLOW_CREATOR, ActionType.MUTE_KEYWORD}),
            verify=frozenset({"feed"}),
            rollback=frozenset({ActionType.FOLLOW_CREATOR, ActionType.MUTE_KEYWORD}),
        )
        self.envelope = ActionEnvelope(
            id="envelope-1",
            passport_id="passport-1",
            passport_version=1,
            destination_ids=frozenset({"lab-account"}),
            allowed_actions=frozenset({ActionType.FOLLOW_CREATOR, ActionType.LIKE}),
            max_total_actions=2,
            max_per_type={ActionType.FOLLOW_CREATOR: 1, ActionType.LIKE: 1},
            expires_at=NOW + timedelta(hours=1),
            approved_by="person-a",
            approved_at=NOW,
            stop_conditions=("target reached", "budget exhausted"),
        )

    def action(self, action_type: ActionType, key: str = "key-1") -> ProposedAction:
        return ProposedAction(
            id=f"action-{key}",
            destination_id="lab-account",
            action_type=action_type,
            target="studio-a",
            reason="Preserve a preferred creator.",
            idempotency_key=key,
            reversible=True,
        )

    def test_public_engagement_is_denied_even_when_user_envelope_contains_it(self) -> None:
        decision = PolicyGuard().check(
            action=self.action(ActionType.LIKE),
            envelope=self.envelope,
            capability=self.capability,
            prior_actions=(),
            now=NOW + timedelta(minutes=1),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "public_engagement_forbidden")

    def test_action_budget_and_adapter_capability_are_enforced(self) -> None:
        allowed = PolicyGuard().check(
            action=self.action(ActionType.FOLLOW_CREATOR),
            envelope=self.envelope,
            capability=self.capability,
            prior_actions=(),
            now=NOW + timedelta(minutes=1),
        )
        self.assertTrue(allowed.allowed)
        exhausted = PolicyGuard().check(
            action=self.action(ActionType.FOLLOW_CREATOR, "key-2"),
            envelope=self.envelope,
            capability=self.capability,
            prior_actions=((ActionType.FOLLOW_CREATOR, ActionStatus.EXECUTED),),
            now=NOW + timedelta(minutes=2),
        )
        self.assertEqual(exhausted.code, "type_budget_exhausted")


class EvaluationAndLedgerTests(unittest.TestCase):
    def test_evaluation_detects_drift_and_concentration(self) -> None:
        sample = FeedSample(
            platform="lab",
            account_id="lab-account",
            sampled_at=NOW,
            items=tuple(
                FeedItem(
                    id=f"item-{index}",
                    creator_id="rage-farm",
                    topics=("ragebait",),
                    format="short_video",
                    language="en",
                    quality=0.2,
                    outrage=0.9,
                    novelty=0.1,
                    source="same-source",
                )
                for index in range(10)
            ),
        )
        evaluation = evaluate_feed(passport(), sample)
        self.assertEqual(evaluation.stop_reason, StopReason.CONTINUE)
        self.assertEqual(evaluation.unwanted_rate, 1.0)
        self.assertEqual(evaluation.source_concentration, 1.0)
        self.assertGreater(evaluation.total_variation_distance, 0.9)

    def test_ledger_is_idempotent_and_rollback_swaps_state(self) -> None:
        action = ProposedAction(
            id="action-1",
            destination_id="lab-account",
            action_type=ActionType.FOLLOW_CREATOR,
            target="studio-a",
            reason="Preserve creator continuity.",
            idempotency_key="idem-1",
            reversible=True,
        )
        outcome = ActionOutcome(
            action=action,
            status=ActionStatus.EXECUTED,
            before_state={"following": False},
            after_state={"following": True},
            executed_at=NOW,
        )
        ledger = ActionLedger()
        self.assertIs(ledger.record(outcome), outcome)
        self.assertIs(ledger.record(outcome), outcome)
        rolled_back = ledger.mark_rolled_back(action, NOW + timedelta(minutes=5))
        self.assertEqual(rolled_back.status, ActionStatus.ROLLED_BACK)
        self.assertEqual(rolled_back.after_state["following"], False)
        receipt = ledger.issue_receipt(
            receipt_id="receipt-1",
            passport_id="passport-1",
            passport_version=1,
            destination_id="lab-account",
            issued_at=NOW + timedelta(minutes=6),
            trace_id="trace-1",
            previous_checkpoint_id="checkpoint-1",
        )
        self.assertEqual(len(receipt.outcomes), 1)


if __name__ == "__main__":
    unittest.main()
