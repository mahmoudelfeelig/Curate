from __future__ import annotations

from datetime import datetime
from math import sqrt
from typing import Mapping, Sequence

from .models import (
    CompanionBlend,
    FeedPassport,
    PassportOverlay,
    ShareablePassportSlice,
    normalize_weights,
)


def apply_overlay(base: FeedPassport, overlay: PassportOverlay, now: datetime) -> FeedPassport:
    if overlay.base_passport_id != base.id:
        raise ValueError("overlay targets a different passport")
    if not overlay.is_active(now):
        raise ValueError("overlay is not active")

    adjusted = dict(base.topic_targets)
    for topic, delta in overlay.topic_adjustments.items():
        adjusted[topic] = max(0.0, adjusted.get(topic, 0.0) + float(delta))

    exclusions = (set(base.hard_exclusions) | set(overlay.add_exclusions)) - set(overlay.remove_exclusions)
    return base.revise(
        updated_at=now,
        name=f"{base.name} / {overlay.name}",
        topic_targets=normalize_weights(adjusted),
        hard_exclusions=frozenset(exclusions),
        serendipity=overlay.serendipity if overlay.serendipity is not None else base.serendipity,
        max_outrage=overlay.max_outrage if overlay.max_outrage is not None else base.max_outrage,
        expires_at=overlay.expires_at,
    )


def _weighted_average(values: Sequence[tuple[float, float]]) -> float:
    total_weight = sum(weight for _, weight in values)
    if total_weight <= 0:
        raise ValueError("blend weights must contain a positive value")
    return sum(value * weight for value, weight in values) / total_weight


def blend_slices(
    *,
    blend_id: str,
    name: str,
    slices: Sequence[ShareablePassportSlice],
    weights: Mapping[str, float],
    strategy: str,
    created_at: datetime,
    expires_at: datetime,
) -> CompanionBlend:
    if len(slices) < 2:
        raise ValueError("at least two slices are required")
    if len({item.owner_id for item in slices}) != len(slices):
        raise ValueError("each participant can contribute only one slice")
    if any(item.expires_at <= created_at for item in slices):
        raise ValueError("all shared slices must still be valid")
    if expires_at > min(item.expires_at for item in slices):
        raise ValueError("a companion blend cannot outlive any contributing consent")
    participant_ids = {item.owner_id for item in slices}
    if set(weights) - participant_ids:
        raise ValueError("blend weights may name only participating owners")
    if any(float(weights.get(owner_id, 1.0)) <= 0 for owner_id in participant_ids):
        raise ValueError("every companion blend weight must be positive")
    if strategy not in {"common_ground", "taste_swap", "bridge", "weighted"}:
        raise ValueError("companion strategy must be common_ground, taste_swap, bridge, or weighted")

    supplied_weights = {owner_id: float(weights.get(owner_id, 1.0)) for owner_id in participant_ids}
    if strategy == "taste_swap":
        total = sum(supplied_weights.values())
        effective_weights = {
            owner_id: max(0.000001, total - owner_weight)
            for owner_id, owner_weight in supplied_weights.items()
        }
    elif strategy == "bridge":
        effective_weights = {owner_id: sqrt(owner_weight) for owner_id, owner_weight in supplied_weights.items()}
    else:
        effective_weights = supplied_weights

    topic_names = set().union(*(item.topic_targets.keys() for item in slices))
    creator_names = set().union(*(item.creator_preferences.keys() for item in slices))
    format_names = set().union(*(item.format_preferences.keys() for item in slices))

    if strategy == "common_ground":
        topic_names = set.intersection(*(set(item.topic_targets) for item in slices))
        if not topic_names:
            raise ValueError("common-ground blend has no shared topics")

    topic_targets: dict[str, float] = {}
    for topic in topic_names:
        topic_targets[topic] = _weighted_average(
            [(item.topic_targets.get(topic, 0.0), effective_weights[item.owner_id]) for item in slices]
        )

    creator_preferences: dict[str, float] = {}
    for creator in creator_names:
        creator_preferences[creator] = _weighted_average(
            [
                (item.creator_preferences.get(creator, 0.0), effective_weights[item.owner_id])
                for item in slices
            ]
        )

    format_preferences: dict[str, float] = {}
    for format_name in format_names:
        format_preferences[format_name] = _weighted_average(
            [
                (item.format_preferences[format_name], effective_weights[item.owner_id])
                for item in slices
                if format_name in item.format_preferences
            ]
        )

    hard_exclusions = frozenset().union(*(item.hard_exclusions for item in slices))

    serendipity_values = [
        (item.serendipity, effective_weights[item.owner_id])
        for item in slices
        if item.serendipity is not None
    ]
    serendipity = _weighted_average(serendipity_values) if serendipity_values else 0.2

    return CompanionBlend(
        id=blend_id,
        name=name,
        participant_ids=tuple(item.owner_id for item in slices),
        topic_targets=normalize_weights(topic_targets) if topic_targets else {},
        creator_preferences=creator_preferences,
        serendipity=serendipity,
        strategy=strategy,
        created_at=created_at,
        expires_at=expires_at,
        consent_ids=tuple(item.consent_id for item in slices),
        format_preferences=format_preferences,
        hard_exclusions=hard_exclusions,
    )
