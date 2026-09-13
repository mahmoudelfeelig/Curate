from __future__ import annotations

import re
from collections.abc import Mapping
from math import isfinite


_PERCENT_TARGET = re.compile(
    r"(?P<percent>\d{1,3}(?:\.\d+)?)\s*(?:%|percent)\s+(?:of\s+)?"
    r"(?P<topic>[a-z][a-z0-9 '&/-]{1,60}?)"
    r"(?=\s*(?:,|;|\band\b|\bplus\b|\bwith\b|\bwhile\b|\.|$))",
    re.IGNORECASE,
)

_RELATIVE_TARGET = re.compile(
    r"\b(?P<direction>"
    r"more|increase|increased|boost|prioritize|prioritise|focus\s+on|"
    r"less|fewer|reduce|decrease|decreased|cut\s+back\s+on|"
    r"no|avoid|without|exclude|remove|stop\s+showing(?:\s+me)?"
    r")\s+(?:of\s+)?"
    r"(?P<topic>[a-z0-9][a-z0-9 '&/-]{0,80}?)"
    r"(?=\s*(?:,|;|\.|!|\?|$|\band\b|\bbut\b|\bwhile\b|\binstead\b))",
    re.IGNORECASE,
)

_MORE_DIRECTIONS = frozenset(
    {"more", "increase", "increased", "boost", "prioritize", "prioritise", "focus on"}
)
_LESS_DIRECTIONS = frozenset(
    {"less", "fewer", "reduce", "decrease", "decreased", "cut back on"}
)
_REMOVE_DIRECTIONS = frozenset(
    {"no", "avoid", "without", "exclude", "remove", "stop showing", "stop showing me"}
)
_GENERIC_TOPIC_SUFFIXES = frozenset(
    {
        "account",
        "accounts",
        "channel",
        "channels",
        "content",
        "creator",
        "creators",
        "page",
        "pages",
        "post",
        "posts",
        "video",
        "videos",
    }
)


def has_explicit_topic_targets(goal: str) -> bool:
    return bool(_PERCENT_TARGET.search(goal))


def has_relative_topic_directions(goal: str) -> bool:
    return any(_relative_topics(goal))


def target_topics_for_goal(goal: str, current: Mapping[str, float]) -> dict[str, float]:
    """Resolve exact percentages and bounded relative topic directions.

    Explicit percentages are immutable constraints. Relative phrases such as
    ``more science`` or ``less ragebait`` can only shape the unallocated share.
    A goal without either form of topic direction preserves the current mix.
    """

    explicit: dict[str, float] = {}
    for match in _PERCENT_TARGET.finditer(goal):
        percent = float(match.group("percent"))
        if not 0 < percent <= 100:
            raise ValueError("topic percentages must be greater than zero and at most 100")
        topic = _topic_slug(match.group("topic"))
        if topic in explicit:
            raise ValueError("each explicit topic percentage must appear only once")
        explicit[topic] = percent / 100
    increased, decreased, removed = _relative_topics(goal)
    conflicts = increased & (decreased | removed)
    if conflicts:
        names = ", ".join(sorted(conflicts))
        raise ValueError(f"topic directions conflict for: {names}")
    excluded_targets = set(explicit) & removed
    if excluded_targets:
        names = ", ".join(sorted(excluded_targets))
        raise ValueError(f"explicit topic percentages conflict with excluded topics: {names}")
    if not explicit and not (increased or decreased or removed):
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
        flexible = {
            key: float(value)
            for key, value in current.items()
            if key not in result and isfinite(float(value)) and float(value) > 0
        }
        baseline = sum(flexible.values()) / len(flexible) if flexible else 1.0
        for topic in removed:
            flexible.pop(topic, None)
        for topic in decreased:
            if topic in flexible:
                flexible[topic] *= 0.2
        for topic in increased:
            if topic in result:
                continue
            flexible[topic] = max(flexible.get(topic, baseline), baseline * 0.5) * 2.0
        flexible_total = sum(flexible.values())
        if flexible_total:
            for key, value in flexible.items():
                result[key] = remaining * value / flexible_total
        else:
            result["exploration"] = remaining
    return _rounded_mix(result, fixed_topics=frozenset(explicit))


def _relative_topics(goal: str) -> tuple[set[str], set[str], set[str]]:
    increased: set[str] = set()
    decreased: set[str] = set()
    removed: set[str] = set()
    for match in _RELATIVE_TARGET.finditer(goal):
        direction = " ".join(match.group("direction").casefold().split())
        topic = _relative_topic_slug(match.group("topic"))
        if topic is None:
            continue
        if direction in _MORE_DIRECTIONS:
            increased.add(topic)
        elif direction in _LESS_DIRECTIONS:
            decreased.add(topic)
        elif direction in _REMOVE_DIRECTIONS:
            removed.add(topic)
    return increased, decreased, removed


def _relative_topic_slug(value: str) -> str | None:
    tokens = re.findall(r"[a-z0-9]+", value.casefold())
    while tokens and tokens[-1] in _GENERIC_TOPIC_SUFFIXES:
        tokens.pop()
    if tokens and tokens[-1] == "based":
        tokens.pop()
    if not tokens:
        return None
    return _topic_slug(" ".join(tokens))


def _rounded_mix(values: Mapping[str, float], *, fixed_topics: frozenset[str]) -> dict[str, float]:
    result = {
        key: round(float(value), 6)
        for key, value in sorted(values.items())
        if isfinite(float(value)) and float(value) > 0
    }
    adjustable = [key for key in result if key not in fixed_topics]
    residual = round(1.0 - sum(result.values()), 6)
    if adjustable and residual:
        key = max(adjustable, key=lambda item: result[item])
        result[key] = round(result[key] + residual, 6)
    return result


def _topic_slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    if not normalized:
        raise ValueError("topic names must contain letters or numbers")
    return normalized[:64]
