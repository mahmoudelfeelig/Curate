from __future__ import annotations

from collections import Counter

from .models import FeedEvaluation, FeedPassport, FeedSample, StopReason, normalize_weights


def _topic_distribution(sample: FeedSample) -> dict[str, float]:
    counts: Counter[str] = Counter()
    for item in sample.items:
        if not item.topics:
            counts["unclassified"] += 1
            continue
        share = 1.0 / len(item.topics)
        for topic in item.topics:
            counts[topic] += share
    if not counts:
        return {"empty": 1.0}
    return normalize_weights(counts)


def _total_variation(desired: dict[str, float], observed: dict[str, float]) -> float:
    keys = set(desired) | set(observed)
    return min(1.0, 0.5 * sum(abs(desired.get(key, 0.0) - observed.get(key, 0.0)) for key in keys))


def evaluate_feed(
    passport: FeedPassport,
    sample: FeedSample,
    *,
    distance_threshold: float = 0.18,
) -> FeedEvaluation:
    if not sample.items:
        return FeedEvaluation(
            desired_topics=passport.topic_targets,
            observed_topics={"empty": 1.0},
            total_variation_distance=1.0,
            unwanted_rate=0.0,
            serendipity_rate=0.0,
            source_concentration=0.0,
            matched_creator_rate=0.0,
            stop_reason=StopReason.LOW_CONFIDENCE,
            recommendations=("Collect a larger destination sample before applying more actions.",),
        )

    observed = _topic_distribution(sample)
    distance = _total_variation(dict(passport.topic_targets), observed)
    unwanted = sum(
        1
        for item in sample.items
        if item.outrage > passport.max_outrage or bool(set(item.topics) & passport.hard_exclusions)
    ) / len(sample.items)
    serendipity = sum(1 for item in sample.items if item.novelty >= 0.6) / len(sample.items)
    sources = Counter(item.source for item in sample.items)
    source_concentration = max(sources.values()) / len(sample.items)
    preferred_creators = {creator for creator, weight in passport.creator_preferences.items() if weight > 0}
    matched_creator_rate = (
        sum(1 for item in sample.items if item.creator_id in preferred_creators) / len(sample.items)
        if preferred_creators
        else 1.0
    )

    recommendations: list[str] = []
    if distance > distance_threshold:
        recommendations.append("Rebalance topic controls toward the Passport target.")
    if unwanted > passport.max_outrage:
        recommendations.append("Reduce excluded or high-outrage sources within the remaining budget.")
    if abs(serendipity - passport.serendipity) > 0.12:
        recommendations.append("Adjust exploration so the measured serendipity rate matches the Passport.")
    if source_concentration > passport.max_source_share:
        recommendations.append("Diversify sources to respect the maximum source share.")

    stop_reason = StopReason.TARGET_REACHED if not recommendations else StopReason.CONTINUE
    return FeedEvaluation(
        desired_topics=passport.topic_targets,
        observed_topics=observed,
        total_variation_distance=distance,
        unwanted_rate=unwanted,
        serendipity_rate=serendipity,
        source_concentration=source_concentration,
        matched_creator_rate=matched_creator_rate,
        stop_reason=stop_reason,
        recommendations=tuple(recommendations),
    )
