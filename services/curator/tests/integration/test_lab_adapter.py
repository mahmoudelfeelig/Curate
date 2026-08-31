from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from feed_passport.adapters.lab import LabAdapter
from feed_passport.domain import (
    ActionEnvelope,
    ActionLedger,
    ActionStatus,
    ActionType,
    FeedPassport,
    PolicyGuard,
    ProposedAction,
    StopReason,
    evaluate_feed,
)


NOW = datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc)


def target_passport() -> FeedPassport:
    return FeedPassport(
        id="passport-demo",
        owner_id="demo-person",
        name="Signal without outrage",
        version=1,
        intent="Research, independent games, design, and local culture.",
        topic_targets={"research": 0.4, "indie": 0.25, "design": 0.2, "local": 0.15},
        creator_preferences={"studio-a": 1.0, "rage-farm": -1.0},
        hard_exclusions=frozenset({"ragebait"}),
        serendipity=0.2,
        max_outrage=0.05,
        max_source_share=0.4,
        created_at=NOW,
        updated_at=NOW,
    )


class LabAdapterTests(unittest.TestCase):
    def test_seeded_twins_are_deterministic(self) -> None:
        adapter = LabAdapter()
        first = adapter.sample("destination-new", now=NOW, limit=20)
        twin = adapter.sample("destination-twin", now=NOW, limit=20)
        self.assertEqual([item.id for item in first.items], [item.id for item in twin.items])
        adapter.reset("destination-new")
        repeated = adapter.sample("destination-new", now=NOW, limit=20)
        self.assertEqual(first, repeated)

    def test_targeted_plan_beats_an_equal_action_budget_neutral_twin(self) -> None:
        adapter = LabAdapter()
        passport = target_passport()
        observation = adapter.observe("destination-new", now=NOW)
        plan = adapter.compile(passport, observation, now=NOW)
        neutral_actions = 0

        for index, action in enumerate(plan.actions):
            adapter.execute("destination-new", action, now=NOW + timedelta(seconds=index + 1))
            neutral_type = ActionType.SET_SERENDIPITY if index % 2 == 0 else ActionType.SET_SOURCE_CAP
            neutral_value = 0.05 if neutral_type is ActionType.SET_SERENDIPITY else 0.8
            neutral = ProposedAction(
                id=f"neutral-{index}",
                destination_id="destination-twin",
                action_type=neutral_type,
                target="neutral-control",
                reason="Consume the same control budget without targeting Passport topics.",
                idempotency_key=f"neutral-budget-{index}",
                reversible=True,
                parameters={"value": neutral_value},
            )
            adapter.execute("destination-twin", neutral, now=NOW + timedelta(seconds=index + 1))
            neutral_actions += 1

        targeted = evaluate_feed(passport, adapter.sample("destination-new", now=NOW, limit=24))
        neutral = evaluate_feed(passport, adapter.sample("destination-twin", now=NOW, limit=24))
        self.assertEqual(len(plan.actions), neutral_actions)
        self.assertLess(targeted.total_variation_distance, neutral.total_variation_distance)
        self.assertLess(targeted.unwanted_rate, neutral.unwanted_rate)

    def test_closed_loop_improves_feed_and_rolls_back_exactly(self) -> None:
        adapter = LabAdapter()
        passport = target_passport()
        before_sample = adapter.sample("destination-new", now=NOW, limit=24)
        before = evaluate_feed(passport, before_sample)
        observation = adapter.observe("destination-new", now=NOW, sample_size=24)
        plan = adapter.compile(passport, observation, now=NOW)
        self.assertGreater(len(plan.actions), 4)

        envelope = ActionEnvelope(
            id="envelope-demo",
            passport_id=passport.id,
            passport_version=passport.version,
            destination_ids=frozenset({"destination-new"}),
            allowed_actions=frozenset(action.action_type for action in plan.actions),
            max_total_actions=len(plan.actions),
            max_per_type={action_type: len(plan.actions) for action_type in {a.action_type for a in plan.actions}},
            expires_at=NOW + timedelta(hours=1),
            approved_by=passport.owner_id,
            approved_at=NOW,
            stop_conditions=("target reached", "budget exhausted", "human judgment required"),
        )
        capability = adapter.capabilities("destination-new")
        guard = PolicyGuard()
        ledger = ActionLedger()
        prior: list[tuple] = []
        for index, action in enumerate(plan.actions):
            decision = guard.check(
                action=action,
                envelope=envelope,
                capability=capability,
                prior_actions=prior,
                now=NOW + timedelta(seconds=index + 1),
            )
            self.assertTrue(decision.allowed, decision)
            outcome = adapter.execute("destination-new", action, now=NOW + timedelta(seconds=index + 1))
            ledger.record(outcome)
            prior.append((action.action_type, outcome.status))

        after_sample = adapter.sample("destination-new", now=NOW + timedelta(minutes=1), limit=24)
        after = evaluate_feed(passport, after_sample)
        self.assertLess(after.total_variation_distance, before.total_variation_distance)
        self.assertLess(after.unwanted_rate, before.unwanted_rate)

        receipt = ledger.issue_receipt(
            receipt_id="receipt-demo",
            passport_id=passport.id,
            passport_version=passport.version,
            destination_id="destination-new",
            issued_at=NOW + timedelta(minutes=2),
            trace_id="trace-demo",
            previous_checkpoint_id="checkpoint-before-demo",
        )
        rollback = adapter.rollback("destination-new", receipt, now=NOW + timedelta(minutes=3))
        self.assertFalse(rollback.failed_actions)
        self.assertEqual(len(rollback.restored_actions), len(plan.actions))
        restored_sample = adapter.sample("destination-new", now=NOW, limit=24)
        self.assertEqual([item.id for item in before_sample.items], [item.id for item in restored_sample.items])

    def test_execute_is_idempotent(self) -> None:
        adapter = LabAdapter()
        passport = target_passport()
        observation = adapter.observe("destination-new", now=NOW)
        action = adapter.compile(passport, observation, now=NOW).actions[0]
        first = adapter.execute("destination-new", action, now=NOW)
        repeated = adapter.execute("destination-new", action, now=NOW + timedelta(minutes=5))
        self.assertIs(first, repeated)
        self.assertEqual(first.status, ActionStatus.EXECUTED)

    def test_idempotency_treats_persisted_list_parameters_as_equivalent_to_tuples(self) -> None:
        adapter = LabAdapter()
        original = ProposedAction(
            id="persisted-action",
            destination_id="destination-new",
            action_type=ActionType.MUTE_KEYWORD,
            target="ragebait",
            reason="Exercise an action before and after JSON persistence.",
            idempotency_key="persisted-action-key",
            reversible=True,
            parameters={
                "profile_evidence_urls": ("https://example.test/one", "https://example.test/two"),
                "nested": {"hard_exclusions": ("ragebait",)},
            },
        )
        persisted = replace(
            original,
            parameters={
                "profile_evidence_urls": ["https://example.test/one", "https://example.test/two"],
                "nested": {"hard_exclusions": ["ragebait"]},
            },
        )

        first = adapter.execute("destination-new", original, now=NOW)
        replayed = adapter.execute("destination-new", persisted, now=NOW + timedelta(minutes=1))

        self.assertIs(replayed, first)

    def test_idempotency_still_rejects_a_semantically_different_action(self) -> None:
        adapter = LabAdapter()
        original = ProposedAction(
            id="collision-action",
            destination_id="destination-new",
            action_type=ActionType.MUTE_KEYWORD,
            target="ragebait",
            reason="Protect one idempotency key from a different request.",
            idempotency_key="collision-action-key",
            reversible=True,
            parameters={},
        )
        adapter.execute("destination-new", original, now=NOW)

        with self.assertRaisesRegex(ValueError, "idempotency key collision"):
            adapter.execute(
                "destination-new",
                replace(original, target="different-target"),
                now=NOW + timedelta(minutes=1),
            )

    def test_clone_after_execution_keeps_history_and_isolates_account_state(self) -> None:
        adapter = LabAdapter()
        passport = target_passport()
        observation = adapter.observe("destination-new", now=NOW)
        action = adapter.compile(passport, observation, now=NOW).actions[0]
        original_outcome = adapter.execute("destination-new", action, now=NOW)

        cloned = adapter.clone()

        self.assertIs(cloned.execute("destination-new", action, now=NOW), original_outcome)
        cloned.reset("destination-new")
        original_sample = adapter.sample("destination-new", now=NOW, limit=24)
        reset_clone_sample = cloned.sample("destination-new", now=NOW, limit=24)
        self.assertNotEqual(original_sample, reset_clone_sample)

    def test_same_passport_version_with_different_parameters_gets_a_new_action_key(self) -> None:
        adapter = LabAdapter()
        observation = adapter.observe("destination-new", now=NOW)
        first_passport = target_passport()
        adjusted_passport = FeedPassport(
            id=first_passport.id,
            owner_id=first_passport.owner_id,
            name=first_passport.name,
            version=first_passport.version,
            intent=first_passport.intent,
            topic_targets={"research": 0.8, "indie": 0.08, "design": 0.08, "local": 0.04},
            creator_preferences=first_passport.creator_preferences,
            hard_exclusions=first_passport.hard_exclusions,
            serendipity=first_passport.serendipity,
            max_outrage=first_passport.max_outrage,
            max_source_share=first_passport.max_source_share,
            created_at=first_passport.created_at,
            updated_at=first_passport.updated_at,
        )
        first = next(
            action for action in adapter.compile(first_passport, observation, now=NOW).actions
            if action.target == "research"
        )
        adjusted = next(
            action for action in adapter.compile(adjusted_passport, observation, now=NOW).actions
            if action.target == "research"
        )

        self.assertEqual(first.action_type, adjusted.action_type)
        self.assertEqual(first.target, adjusted.target)
        self.assertNotEqual(first.parameters, adjusted.parameters)
        self.assertNotEqual(first.idempotency_key, adjusted.idempotency_key)
        adapter.execute("destination-new", first, now=NOW)
        outcome = adapter.execute("destination-new", adjusted, now=NOW + timedelta(seconds=1))
        self.assertEqual(outcome.action, adjusted)

    def test_repeated_target_after_state_change_gets_a_new_action_key(self) -> None:
        adapter = LabAdapter()
        passport = target_passport()
        initial = adapter.observe("destination-new", now=NOW)
        first = next(
            action for action in adapter.compile(passport, initial, now=NOW).actions
            if action.target == "research"
        )
        adapter.execute("destination-new", first, now=NOW)

        adjusted_passport = FeedPassport(
            id=passport.id,
            owner_id=passport.owner_id,
            name=passport.name,
            version=passport.version,
            intent=passport.intent,
            topic_targets={"research": 0.8, "indie": 0.08, "design": 0.08, "local": 0.04},
            creator_preferences=passport.creator_preferences,
            hard_exclusions=passport.hard_exclusions,
            serendipity=passport.serendipity,
            max_outrage=passport.max_outrage,
            max_source_share=passport.max_source_share,
            created_at=passport.created_at,
            updated_at=passport.updated_at,
        )
        after_first = adapter.observe("destination-new", now=NOW + timedelta(seconds=1))
        detour = next(
            action for action in adapter.compile(adjusted_passport, after_first, now=NOW).actions
            if action.target == "research"
        )
        adapter.execute("destination-new", detour, now=NOW + timedelta(seconds=1))

        after_detour = adapter.observe("destination-new", now=NOW + timedelta(seconds=2))
        repeated = next(
            action for action in adapter.compile(passport, after_detour, now=NOW).actions
            if action.target == "research"
        )
        self.assertEqual(first.parameters, repeated.parameters)
        self.assertNotEqual(first.reason, repeated.reason)
        self.assertNotEqual(first.idempotency_key, repeated.idempotency_key)
        outcome = adapter.execute("destination-new", repeated, now=NOW + timedelta(seconds=2))
        self.assertEqual(outcome.action, repeated)


if __name__ == "__main__":
    unittest.main()
