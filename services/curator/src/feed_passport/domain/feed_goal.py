from __future__ import annotations

import re
from collections.abc import Mapping


_PERCENT_TARGET = re.compile(
    r"(?P<percent>\d{1,3}(?:\.\d+)?)\s*(?:%|percent)\s+(?:of\s+)?"
    r"(?P<topic>[a-z][a-z0-9 '&/-]{1,60}?)"
    r"(?=\s*(?:,|;|\band\b|\bplus\b|\bwith\b|\bwhile\b|\.|$))",
    re.IGNORECASE,
)


def has_explicit_topic_targets(goal: str) -> bool:
    return bool(_PERCENT_TARGET.search(goal))


def target_topics_for_goal(goal: str, current: Mapping[str, float]) -> dict[str, float]:
    """Resolve exact natural-language percentages without model discretion."""

    explicit: dict[str, float] = {}
    for match in _PERCENT_TARGET.finditer(goal):
        percent = float(match.group("percent"))
        if not 0 < percent <= 100:
            raise ValueError("topic percentages must be greater than zero and at most 100")
        topic = _topic_slug(match.group("topic"))
        if topic in explicit:
            raise ValueError("each explicit topic percentage must appear only once")
        explicit[topic] = percent / 100
    if not explicit:
        return {key: round(float(value), 6) for key, value in current.items()}
    total = sum(explicit.values())
    if total > 1.000001:
        raise ValueError("explicit topic percentages cannot exceed 100%")
    remaining = max(0.0, 1.0 - total)
    remainder_phrase = any(
        phrase in goal.casefold()
        for phrase in (
            "remainder exploratory",
            "remainder exploration",
            "rest exploratory",
            "rest exploration",
        )
    )
    result = dict(explicit)
    if remaining > 0 and remainder_phrase:
        result["exploration"] = remaining
    elif remaining > 0:
        untouched = {key: float(value) for key, value in current.items() if key not in result}
        untouched_total = sum(untouched.values())
        if untouched_total:
            for key, value in untouched.items():
                result[key] = remaining * value / untouched_total
        else:
            result["exploration"] = remaining
    return {key: round(value, 6) for key, value in sorted(result.items()) if value > 0}


def _topic_slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    if not normalized:
        raise ValueError("topic names must contain letters or numbers")
    return normalized[:64]
