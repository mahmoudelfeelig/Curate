from __future__ import annotations

import math
import unittest

from feed_passport.domain.feed_goal import (
    has_explicit_topic_targets,
    has_relative_topic_directions,
    target_topics_for_goal,
)


class FeedGoalTests(unittest.TestCase):
    def test_preserves_a_complete_seven_topic_mix_exactly(self) -> None:
        goal = (
            "Make it 50% astronomy, 15% coding, 12% drawing, 3% anime, "
            "10% Naruto, 5% One Piece, and 5% perfumes."
        )

        result = target_topics_for_goal(goal, {"research": 0.6, "design": 0.4})

        self.assertTrue(has_explicit_topic_targets(goal))
        self.assertEqual(
            result,
            {
                "anime": 0.03,
                "astronomy": 0.5,
                "coding": 0.15,
                "drawing": 0.12,
                "naruto": 0.1,
                "one_piece": 0.05,
                "perfumes": 0.05,
            },
        )

    def test_vague_relative_goal_moves_mix_in_requested_directions(self) -> None:
        current = {"ragebait": 0.35, "research": 0.4, "drawing": 0.25}

        result = target_topics_for_goal(
            "I want less ragebait and more science-based pages.",
            current,
        )

        self.assertTrue(has_relative_topic_directions("less ragebait and more science"))
        self.assertLess(result["ragebait"], current["ragebait"])
        self.assertIn("science", result)
        self.assertGreater(result["science"], 0)
        self.assertTrue(math.isclose(sum(result.values()), 1.0, abs_tol=0.000001))

    def test_ragebait_direction_is_a_cross_cutting_guardrail_not_a_topic(self) -> None:
        current = {"research": 0.6, "drawing": 0.4}

        result = target_topics_for_goal("Reduce ragebait.", current)

        self.assertFalse(has_relative_topic_directions("Reduce ragebait."))
        self.assertEqual(result, current)
        self.assertNotIn("ragebait", result)

    def test_mixed_goal_locks_explicit_share_and_biases_only_remainder(self) -> None:
        result = target_topics_for_goal(
            "Keep 50% astronomy, add more coding, and show less drawing.",
            {"drawing": 0.6, "history": 0.4},
        )

        self.assertEqual(result["astronomy"], 0.5)
        self.assertGreater(result["coding"], result["drawing"])
        self.assertTrue(math.isclose(sum(result.values()), 1.0, abs_tol=0.000001))

    def test_underfilled_exact_mix_assigns_implicit_remainder_to_exploration(self) -> None:
        result = target_topics_for_goal(
            "Make it 60% pet science and 20% cute drawing.",
            {"research": 0.75, "drawing": 0.25},
        )

        self.assertEqual(
            result,
            {
                "cute_drawing": 0.2,
                "exploration": 0.2,
                "pet_science": 0.6,
            },
        )

    def test_remainder_exploration_phrase_overrides_existing_remainder(self) -> None:
        result = target_topics_for_goal(
            "Make it 50% astronomy and 20% coding; keep the rest exploratory.",
            {"research": 0.75, "drawing": 0.25},
        )

        self.assertEqual(
            result,
            {"astronomy": 0.5, "coding": 0.2, "exploration": 0.3},
        )

    def test_rejects_duplicate_topics_after_normalization(self) -> None:
        with self.assertRaisesRegex(ValueError, "only once"):
            target_topics_for_goal(
                "Make it 40% One Piece and 20% one-piece.",
                {"research": 1.0},
            )

    def test_rejects_over_one_hundred_percent(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot exceed 100%"):
            target_topics_for_goal(
                "Make it 70% astronomy and 40% coding.",
                {"research": 1.0},
            )

    def test_rejects_conflicting_relative_directions(self) -> None:
        with self.assertRaisesRegex(ValueError, "directions conflict"):
            target_topics_for_goal(
                "Show me more astronomy but less astronomy.",
                {"research": 1.0},
            )

    def test_rejects_a_percentage_for_an_excluded_topic(self) -> None:
        with self.assertRaisesRegex(ValueError, "conflict with excluded"):
            target_topics_for_goal(
                "Make it 50% astronomy and 50% coding, but exclude coding.",
                {"research": 1.0},
            )

    def test_ignores_a_direction_without_a_meaningful_topic(self) -> None:
        current = {"research": 1.0}

        self.assertEqual(target_topics_for_goal("Show me more content.", current), current)

    def test_unqualified_goal_preserves_current_mix(self) -> None:
        current = {"research": 0.55, "drawing": 0.45}

        self.assertEqual(target_topics_for_goal("Make my feed calmer.", current), current)


if __name__ == "__main__":
    unittest.main()
