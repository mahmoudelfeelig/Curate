from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping


def _utc_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _bounded(value: float, field_name: str, low: float = 0.0, high: float = 1.0) -> None:
    if not low <= value <= high:
        raise ValueError(f"{field_name} must be between {low} and {high}")


def _freeze_map(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


def normalize_weights(weights: Mapping[str, float]) -> dict[str, float]:
    cleaned = {str(key).strip(): float(value) for key, value in weights.items() if float(value) > 0}
    if not cleaned:
        raise ValueError("at least one positive topic weight is required")
    total = sum(cleaned.values())
    return {key: value / total for key, value in sorted(cleaned.items())}


class CapabilityLevel(StrEnum):
    CLOSED_LOOP = "closed_loop"
    EXECUTABLE = "executable"
    GUIDED = "guided"
    LAB = "lab"
    UNAVAILABLE = "unavailable"


class ActionType(StrEnum):
    FOLLOW_CREATOR = "follow_creator"
    UNFOLLOW_CREATOR = "unfollow_creator"
    MUTE_CREATOR = "mute_creator"
    UNMUTE_CREATOR = "unmute_creator"
    MUTE_KEYWORD = "mute_keyword"
    UNMUTE_KEYWORD = "unmute_keyword"
    SUBSCRIBE_CREATOR = "subscribe_creator"
    UNSUBSCRIBE_CREATOR = "unsubscribe_creator"
    ADD_TO_LIST = "add_to_list"
    REMOVE_FROM_LIST = "remove_from_list"
    CREATE_CUSTOM_FEED = "create_custom_feed"
    INSTALL_CUSTOM_FEED = "install_custom_feed"
    HIDE_TOPIC = "hide_topic"
    SHOW_TOPIC = "show_topic"
    SET_TOPIC_PREFERENCE = "set_topic_preference"
    SET_SERENDIPITY = "set_serendipity"
    SET_SOURCE_CAP = "set_source_cap"
    LIKE = "like"
    COMMENT = "comment"
    POST = "post"
    REPOST = "repost"
    SEND_MESSAGE = "send_message"


class ActionStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTED = "executed"
    GUIDED = "guided"
    SKIPPED = "skipped"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class OverlayMode(StrEnum):
    ISOLATED = "isolated"
    REVERSIBLE_LIVE = "reversible_live"


class StopReason(StrEnum):
    TARGET_REACHED = "target_reached"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    LOW_CONFIDENCE = "low_confidence"
    PERMISSION_REQUIRED = "permission_required"
    ADAPTER_FAILURE = "adapter_failure"
    CANCELLED = "cancelled"
    HUMAN_JUDGMENT = "human_judgment"
    CONTINUE = "continue"


@dataclass(frozen=True, slots=True)
class PreferenceEvidence:
    source: str
    reference: str
    confidence: float
    observed_at: datetime

    def __post_init__(self) -> None:
        _bounded(self.confidence, "confidence")
        _utc_aware(self.observed_at, "observed_at")


@dataclass(frozen=True, slots=True)
class FeedPassport:
    id: str
    owner_id: str
    name: str
    version: int
    intent: str
    topic_targets: Mapping[str, float]
    creator_preferences: Mapping[str, float] = field(default_factory=dict)
    format_preferences: Mapping[str, float] = field(default_factory=dict)
    languages: tuple[str, ...] = ("en",)
    hard_exclusions: frozenset[str] = frozenset()
    serendipity: float = 0.2
    max_outrage: float = 0.05
    max_source_share: float = 0.25
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    provenance: tuple[PreferenceEvidence, ...] = ()

    def __post_init__(self) -> None:
        if not self.id or not self.owner_id or not self.name.strip() or not self.intent.strip():
            raise ValueError("passport id, owner, name, and intent are required")
        if self.version < 1:
            raise ValueError("version must be positive")
        normalized_topics = normalize_weights(self.topic_targets)
        object.__setattr__(self, "topic_targets", _freeze_map(normalized_topics))
        object.__setattr__(self, "creator_preferences", _freeze_map(self.creator_preferences))
        object.__setattr__(self, "format_preferences", _freeze_map(self.format_preferences))
        object.__setattr__(self, "languages", tuple(dict.fromkeys(self.languages)))
        object.__setattr__(self, "hard_exclusions", frozenset(self.hard_exclusions))
        for creator, value in self.creator_preferences.items():
            _bounded(float(value), f"creator_preferences[{creator}]", -1.0, 1.0)
        for format_name, value in self.format_preferences.items():
            _bounded(float(value), f"format_preferences[{format_name}]", -1.0, 1.0)
        _bounded(self.serendipity, "serendipity")
        _bounded(self.max_outrage, "max_outrage")
        _bounded(self.max_source_share, "max_source_share")
        _utc_aware(self.created_at, "created_at")
        _utc_aware(self.updated_at, "updated_at")
        if self.expires_at is not None:
            _utc_aware(self.expires_at, "expires_at")
            if self.expires_at <= self.created_at:
                raise ValueError("expires_at must be after created_at")

    def revise(self, *, updated_at: datetime, **changes: Any) -> FeedPassport:
        _utc_aware(updated_at, "updated_at")
        return replace(self, version=self.version + 1, updated_at=updated_at, **changes)


@dataclass(frozen=True, slots=True)
class PassportOverlay:
    id: str
    base_passport_id: str
    name: str
    topic_adjustments: Mapping[str, float]
    add_exclusions: frozenset[str]
    remove_exclusions: frozenset[str]
    starts_at: datetime
    expires_at: datetime
    mode: OverlayMode
    serendipity: float | None = None
    max_outrage: float | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.base_passport_id or not self.name.strip():
            raise ValueError("overlay id, base passport, and name are required")
        object.__setattr__(self, "topic_adjustments", _freeze_map(self.topic_adjustments))
        object.__setattr__(self, "add_exclusions", frozenset(self.add_exclusions))
        object.__setattr__(self, "remove_exclusions", frozenset(self.remove_exclusions))
        _utc_aware(self.starts_at, "starts_at")
        _utc_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.starts_at:
            raise ValueError("overlay expiry must be after its start")
        if self.add_exclusions & self.remove_exclusions:
            raise ValueError("an exclusion cannot be added and removed by the same overlay")
        if self.serendipity is not None:
            _bounded(self.serendipity, "serendipity")
        if self.max_outrage is not None:
            _bounded(self.max_outrage, "max_outrage")

    def is_active(self, now: datetime) -> bool:
        _utc_aware(now, "now")
        return self.starts_at <= now < self.expires_at


@dataclass(frozen=True, slots=True)
class ShareablePassportSlice:
    owner_id: str
    passport_id: str
    passport_version: int
    topic_targets: Mapping[str, float]
    creator_preferences: Mapping[str, float]
    serendipity: float | None
    expires_at: datetime
    consent_id: str
    format_preferences: Mapping[str, float] = field(default_factory=dict)
    hard_exclusions: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.owner_id or not self.passport_id or not self.consent_id:
            raise ValueError("owner, passport, and consent are required")
        if self.passport_version < 1:
            raise ValueError("passport_version must be positive")
        topics = normalize_weights(self.topic_targets) if self.topic_targets else {}
        object.__setattr__(self, "topic_targets", _freeze_map(topics))
        object.__setattr__(self, "creator_preferences", _freeze_map(self.creator_preferences))
        object.__setattr__(self, "format_preferences", _freeze_map(self.format_preferences))
        object.__setattr__(self, "hard_exclusions", frozenset(self.hard_exclusions))
        for format_name, value in self.format_preferences.items():
            _bounded(float(value), f"format_preferences[{format_name}]", -1.0, 1.0)
        if self.serendipity is not None:
            _bounded(self.serendipity, "serendipity")
        _utc_aware(self.expires_at, "expires_at")


@dataclass(frozen=True, slots=True)
class CompanionBlend:
    id: str
    name: str
    participant_ids: tuple[str, ...]
    topic_targets: Mapping[str, float]
    creator_preferences: Mapping[str, float]
    serendipity: float
    strategy: str
    created_at: datetime
    expires_at: datetime
    consent_ids: tuple[str, ...]
    format_preferences: Mapping[str, float] = field(default_factory=dict)
    hard_exclusions: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if len(set(self.participant_ids)) < 2:
            raise ValueError("a companion blend requires at least two distinct participants")
        normalized_topics = normalize_weights(self.topic_targets) if self.topic_targets else {}
        object.__setattr__(self, "topic_targets", _freeze_map(normalized_topics))
        object.__setattr__(self, "creator_preferences", _freeze_map(self.creator_preferences))
        object.__setattr__(self, "format_preferences", _freeze_map(self.format_preferences))
        object.__setattr__(self, "hard_exclusions", frozenset(self.hard_exclusions))
        if not (
            self.topic_targets
            or self.creator_preferences
            or self.format_preferences
            or self.hard_exclusions
        ):
            raise ValueError("a companion blend requires at least one shared preference field")
        _bounded(self.serendipity, "serendipity")
        _utc_aware(self.created_at, "created_at")
        _utc_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise ValueError("blend expiry must be after creation")
        if len(self.consent_ids) != len(self.participant_ids):
            raise ValueError("every participant must have a consent receipt")


@dataclass(frozen=True, slots=True)
class PlatformCapabilityManifest:
    platform: str
    level: CapabilityLevel
    observe: frozenset[str]
    execute: frozenset[ActionType]
    verify: frozenset[str]
    rollback: frozenset[ActionType]
    requires_user_handoff: frozenset[ActionType] = frozenset()
    evidence_url: str | None = None
    certified_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "observe", frozenset(self.observe))
        object.__setattr__(self, "execute", frozenset(self.execute))
        object.__setattr__(self, "verify", frozenset(self.verify))
        object.__setattr__(self, "rollback", frozenset(self.rollback))
        object.__setattr__(self, "requires_user_handoff", frozenset(self.requires_user_handoff))
        if not self.rollback <= self.execute:
            raise ValueError("rollback actions must also be executable")
        if self.certified_at is not None:
            _utc_aware(self.certified_at, "certified_at")


@dataclass(frozen=True, slots=True)
class ProposedAction:
    id: str
    destination_id: str
    action_type: ActionType
    target: str
    reason: str
    idempotency_key: str
    reversible: bool
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not all((self.id, self.destination_id, self.target, self.reason, self.idempotency_key)):
            raise ValueError("action identifiers, target, reason, and idempotency key are required")
        object.__setattr__(self, "parameters", _freeze_map(self.parameters))


@dataclass(frozen=True, slots=True)
class ActionEnvelope:
    id: str
    passport_id: str
    passport_version: int
    destination_ids: frozenset[str]
    allowed_actions: frozenset[ActionType]
    max_total_actions: int
    max_per_type: Mapping[ActionType, int]
    expires_at: datetime
    approved_by: str
    approved_at: datetime
    stop_conditions: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.passport_version < 1 or self.max_total_actions < 1:
            raise ValueError("passport version and action budget must be positive")
        object.__setattr__(self, "destination_ids", frozenset(self.destination_ids))
        object.__setattr__(self, "allowed_actions", frozenset(self.allowed_actions))
        object.__setattr__(self, "max_per_type", MappingProxyType(dict(self.max_per_type)))
        if any(limit < 0 for limit in self.max_per_type.values()):
            raise ValueError("per-type budgets cannot be negative")
        if any(action not in self.allowed_actions for action in self.max_per_type):
            raise ValueError("per-type budgets must be for allowed actions")
        _utc_aware(self.expires_at, "expires_at")
        _utc_aware(self.approved_at, "approved_at")
        if self.expires_at <= self.approved_at:
            raise ValueError("envelope expiry must be after approval")


@dataclass(frozen=True, slots=True)
class FeedItem:
    id: str
    creator_id: str
    topics: tuple[str, ...]
    format: str
    language: str
    quality: float
    outrage: float
    novelty: float
    source: str

    def __post_init__(self) -> None:
        for field_name in ("quality", "outrage", "novelty"):
            _bounded(float(getattr(self, field_name)), field_name)


@dataclass(frozen=True, slots=True)
class FeedSample:
    platform: str
    account_id: str
    items: tuple[FeedItem, ...]
    sampled_at: datetime

    def __post_init__(self) -> None:
        _utc_aware(self.sampled_at, "sampled_at")


@dataclass(frozen=True, slots=True)
class AccountObservation:
    platform: str
    account_id: str
    observed_at: datetime
    topic_distribution: Mapping[str, float]
    followed_creators: frozenset[str]
    muted_creators: frozenset[str]
    muted_keywords: frozenset[str]
    sample: FeedSample
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "topic_distribution", _freeze_map(normalize_weights(self.topic_distribution)))
        object.__setattr__(self, "followed_creators", frozenset(self.followed_creators))
        object.__setattr__(self, "muted_creators", frozenset(self.muted_creators))
        object.__setattr__(self, "muted_keywords", frozenset(self.muted_keywords))
        _utc_aware(self.observed_at, "observed_at")
        _bounded(self.confidence, "confidence")


@dataclass(frozen=True, slots=True)
class TranslationLoss:
    field: str
    requested: str
    reason: str
    severity: str
    workaround: str | None = None


@dataclass(frozen=True, slots=True)
class ActionOutcome:
    action: ProposedAction
    status: ActionStatus
    before_state: Mapping[str, Any]
    after_state: Mapping[str, Any]
    executed_at: datetime
    platform_reference: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "before_state", _freeze_map(self.before_state))
        object.__setattr__(self, "after_state", _freeze_map(self.after_state))
        _utc_aware(self.executed_at, "executed_at")


@dataclass(frozen=True, slots=True)
class ActionReceipt:
    id: str
    passport_id: str
    passport_version: int
    destination_id: str
    outcomes: tuple[ActionOutcome, ...]
    issued_at: datetime
    trace_id: str
    previous_checkpoint_id: str | None
    rollback_caveats: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _utc_aware(self.issued_at, "issued_at")

    @property
    def reversible_count(self) -> int:
        return sum(
            1
            for outcome in self.outcomes
            if outcome.status is ActionStatus.EXECUTED and outcome.action.reversible
        )


@dataclass(frozen=True, slots=True)
class TranslationPlan:
    id: str
    passport_id: str
    passport_version: int
    destination_id: str
    capability_level: CapabilityLevel
    actions: tuple[ProposedAction, ...]
    losses: tuple[TranslationLoss, ...]
    created_at: datetime
    estimated_topic_distance: float

    def __post_init__(self) -> None:
        if self.passport_version < 1:
            raise ValueError("passport_version must be positive")
        if any(action.destination_id != self.destination_id for action in self.actions):
            raise ValueError("every planned action must target the plan destination")
        _utc_aware(self.created_at, "created_at")
        _bounded(self.estimated_topic_distance, "estimated_topic_distance")


@dataclass(frozen=True, slots=True)
class AdapterHealth:
    platform: str
    healthy: bool
    mode: str
    checked_at: datetime
    detail: str

    def __post_init__(self) -> None:
        _utc_aware(self.checked_at, "checked_at")


@dataclass(frozen=True, slots=True)
class RollbackOutcome:
    receipt_id: str
    destination_id: str
    restored_actions: tuple[str, ...]
    failed_actions: tuple[str, ...]
    completed_at: datetime
    caveats: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _utc_aware(self.completed_at, "completed_at")


@dataclass(frozen=True, slots=True)
class FeedEvaluation:
    desired_topics: Mapping[str, float]
    observed_topics: Mapping[str, float]
    total_variation_distance: float
    unwanted_rate: float
    serendipity_rate: float
    source_concentration: float
    matched_creator_rate: float
    stop_reason: StopReason
    recommendations: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "desired_topics", _freeze_map(self.desired_topics))
        object.__setattr__(self, "observed_topics", _freeze_map(self.observed_topics))
        for field_name in (
            "total_variation_distance",
            "unwanted_rate",
            "serendipity_rate",
            "source_concentration",
            "matched_creator_rate",
        ):
            _bounded(float(getattr(self, field_name)), field_name)
