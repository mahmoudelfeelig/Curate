from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from math import ceil
from typing import Any, Iterable

from feed_passport.domain.models import (
    AccountObservation,
    ActionOutcome,
    ActionReceipt,
    ActionStatus,
    ActionType,
    AdapterHealth,
    CapabilityLevel,
    FeedItem,
    FeedPassport,
    FeedSample,
    PlatformCapabilityManifest,
    ProposedAction,
    RollbackOutcome,
    TranslationPlan,
    normalize_weights,
)


def _canonical_action_request(action: ProposedAction) -> str:
    """Serialize an action independently of tuple/list persistence normalization."""

    return json.dumps(
        {
            "id": action.id,
            "destination_id": action.destination_id,
            "action_type": action.action_type.value,
            "target": action.target,
            "reason": action.reason,
            "idempotency_key": action.idempotency_key,
            "reversible": action.reversible,
            "parameters": dict(action.parameters),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


@dataclass(slots=True)
class LabAccountState:
    id: str
    topic_affinity: dict[str, float]
    following: set[str] = field(default_factory=set)
    muted_creators: set[str] = field(default_factory=set)
    muted_keywords: set[str] = field(default_factory=set)
    subscriptions: set[str] = field(default_factory=set)
    lists: dict[str, set[str]] = field(default_factory=dict)
    custom_feeds: set[str] = field(default_factory=set)
    serendipity: float = 0.1
    source_cap: float = 0.7


def default_lab_candidates() -> tuple[FeedItem, ...]:
    topics = (
        ("research", "paper-lab", "longform", "open-journal", 0.95, 0.01, 0.25),
        ("research", "systems-notes", "essay", "open-journal", 0.9, 0.02, 0.65),
        ("indie", "studio-a", "video", "creator-web", 0.88, 0.02, 0.25),
        ("indie", "tiny-studio", "image", "creator-web", 0.8, 0.01, 0.75),
        ("design", "type-archive", "image", "design-ring", 0.9, 0.01, 0.2),
        ("design", "civic-designer", "essay", "design-ring", 0.86, 0.02, 0.7),
        ("local", "city-zine", "photo", "neighborhood", 0.87, 0.01, 0.3),
        ("local", "museum-desk", "event", "neighborhood", 0.82, 0.02, 0.8),
        ("music", "radio-night", "audio", "public-radio", 0.84, 0.02, 0.8),
        ("nature", "field-log", "photo", "field-notes", 0.85, 0.01, 0.9),
        ("ragebait", "rage-farm", "short_video", "engagement-farm", 0.18, 0.96, 0.05),
        ("ragebait", "outrage-wire", "post", "engagement-farm", 0.22, 0.9, 0.1),
    )
    items: list[FeedItem] = []
    for cycle in range(4):
        for index, (topic, creator, format_name, source, quality, outrage, novelty) in enumerate(topics):
            items.append(
                FeedItem(
                    id=f"lab-{cycle:02d}-{index:02d}",
                    creator_id=creator,
                    topics=(topic,),
                    format=format_name,
                    language="en",
                    quality=max(0.0, quality - cycle * 0.025),
                    outrage=outrage,
                    novelty=min(1.0, novelty + cycle * 0.03),
                    source=source,
                )
            )
    return tuple(items)


def default_lab_accounts() -> tuple[LabAccountState, ...]:
    return (
        LabAccountState(
            id="source-main",
            topic_affinity={"research": 0.4, "indie": 0.25, "design": 0.2, "local": 0.15},
            following={"paper-lab", "studio-a", "type-archive", "city-zine"},
            serendipity=0.2,
            source_cap=0.4,
        ),
        LabAccountState(
            id="destination-new",
            topic_affinity={"ragebait": 0.65, "music": 0.15, "research": 0.1, "design": 0.1},
            following={"rage-farm"},
            serendipity=0.05,
            source_cap=0.8,
        ),
        LabAccountState(
            id="destination-twin",
            topic_affinity={"ragebait": 0.65, "music": 0.15, "research": 0.1, "design": 0.1},
            following={"rage-farm"},
            serendipity=0.05,
            source_cap=0.8,
        ),
    )


class LabAdapter:
    platform = "feed_passport_lab"

    def __init__(
        self,
        *,
        candidates: Iterable[FeedItem] | None = None,
        accounts: Iterable[LabAccountState] | None = None,
    ) -> None:
        self._candidates = tuple(candidates or default_lab_candidates())
        account_values = tuple(accounts or default_lab_accounts())
        self._initial = {item.id: deepcopy(item) for item in account_values}
        self._accounts = deepcopy(self._initial)
        self._outcomes: dict[str, ActionOutcome] = {}

    def clone(self) -> LabAdapter:
        cloned = LabAdapter(candidates=self._candidates, accounts=self._initial.values())
        cloned._accounts = deepcopy(self._accounts)
        # ActionOutcome is a frozen historical value. Its top-level snapshots are
        # MappingProxyType instances, which intentionally reject deepcopy/pickle.
        # The clone only ever adds or replaces outcome entries, so a new mapping
        # with shared immutable values preserves isolation without copying proxies.
        cloned._outcomes = dict(self._outcomes)
        return cloned

    def export_state(self, account_id: str) -> dict[str, Any]:
        state = self._require_account(account_id)
        return {
            "id": state.id,
            "topic_affinity": dict(state.topic_affinity),
            "following": sorted(state.following),
            "muted_creators": sorted(state.muted_creators),
            "muted_keywords": sorted(state.muted_keywords),
            "subscriptions": sorted(state.subscriptions),
            "lists": {key: sorted(value) for key, value in state.lists.items()},
            "custom_feeds": sorted(state.custom_feeds),
            "serendipity": state.serendipity,
            "source_cap": state.source_cap,
        }

    def import_state(self, value: dict[str, Any]) -> None:
        account_id = str(value["id"])
        self._require_account(account_id)
        self._accounts[account_id] = LabAccountState(
            id=account_id,
            topic_affinity={str(key): float(item) for key, item in dict(value["topic_affinity"]).items()},
            following=set(value.get("following", ())),
            muted_creators=set(value.get("muted_creators", ())),
            muted_keywords=set(value.get("muted_keywords", ())),
            subscriptions=set(value.get("subscriptions", ())),
            lists={str(key): set(items) for key, items in dict(value.get("lists", {})).items()},
            custom_feeds=set(value.get("custom_feeds", ())),
            serendipity=float(value.get("serendipity", 0.1)),
            source_cap=float(value.get("source_cap", 0.7)),
        )

    def reset(self, account_id: str | None = None) -> None:
        if account_id is None:
            self._accounts = deepcopy(self._initial)
            self._outcomes.clear()
            return
        self._require_account(account_id)
        self._accounts[account_id] = deepcopy(self._initial[account_id])
        self._outcomes = {
            key: value for key, value in self._outcomes.items() if value.action.destination_id != account_id
        }

    def capabilities(self, account_id: str) -> PlatformCapabilityManifest:
        self._require_account(account_id)
        executable = frozenset(
            {
                ActionType.FOLLOW_CREATOR,
                ActionType.UNFOLLOW_CREATOR,
                ActionType.MUTE_CREATOR,
                ActionType.UNMUTE_CREATOR,
                ActionType.MUTE_KEYWORD,
                ActionType.UNMUTE_KEYWORD,
                ActionType.SUBSCRIBE_CREATOR,
                ActionType.UNSUBSCRIBE_CREATOR,
                ActionType.ADD_TO_LIST,
                ActionType.REMOVE_FROM_LIST,
                ActionType.CREATE_CUSTOM_FEED,
                ActionType.INSTALL_CUSTOM_FEED,
                ActionType.HIDE_TOPIC,
                ActionType.SHOW_TOPIC,
                ActionType.SET_TOPIC_PREFERENCE,
                ActionType.SET_SERENDIPITY,
                ActionType.SET_SOURCE_CAP,
            }
        )
        return PlatformCapabilityManifest(
            platform=self.platform,
            level=CapabilityLevel.LAB,
            observe=frozenset({"feed", "topic_distribution", "following", "mutes", "subscriptions"}),
            execute=executable,
            verify=frozenset({"feed", "topic_distribution", "following", "mutes", "subscriptions"}),
            rollback=executable,
            evidence_url="docs://proof-lab",
        )

    def observe(self, account_id: str, *, now: datetime, sample_size: int = 24) -> AccountObservation:
        state = self._require_account(account_id)
        sample = self.sample(account_id, now=now, limit=sample_size)
        topic_counts: Counter[str] = Counter()
        for item in sample.items:
            for topic in item.topics:
                topic_counts[topic] += 1 / max(1, len(item.topics))
        distribution = normalize_weights(topic_counts) if topic_counts else {"empty": 1.0}
        return AccountObservation(
            platform=self.platform,
            account_id=account_id,
            observed_at=now,
            topic_distribution=distribution,
            followed_creators=frozenset(state.following | state.subscriptions),
            muted_creators=frozenset(state.muted_creators),
            muted_keywords=frozenset(state.muted_keywords),
            sample=sample,
            confidence=min(1.0, len(sample.items) / max(1, sample_size)),
        )

    def compile(
        self,
        passport: FeedPassport,
        observation: AccountObservation,
        *,
        now: datetime,
    ) -> TranslationPlan:
        state = self._require_account(observation.account_id)
        actions: list[ProposedAction] = []

        def add(action_type: ActionType, target: str, reason: str, reversible: bool = True, **parameters: Any) -> None:
            ordinal = len(actions) + 1
            action_fingerprint = json.dumps(
                {"parameters": parameters, "reason": reason, "reversible": reversible},
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            raw_key = (
                f"{passport.id}:{passport.version}:{observation.account_id}:"
                f"{action_type}:{target}:{ordinal}:{action_fingerprint}"
            )
            key = sha256(raw_key.encode("utf-8")).hexdigest()[:24]
            actions.append(
                ProposedAction(
                    id=f"action-{key[:12]}",
                    destination_id=observation.account_id,
                    action_type=action_type,
                    target=target,
                    reason=reason,
                    idempotency_key=key,
                    reversible=reversible,
                    parameters=parameters,
                )
            )

        for topic, desired in passport.topic_targets.items():
            current = state.topic_affinity.get(topic, 0.0)
            if abs(current - desired) >= 0.035:
                add(
                    ActionType.SET_TOPIC_PREFERENCE,
                    topic,
                    f"Move {topic} from {current:.0%} toward the Passport target of {desired:.0%}.",
                    weight=desired,
                )
        for topic in passport.hard_exclusions:
            if topic not in state.muted_keywords:
                add(ActionType.HIDE_TOPIC, topic, "Apply a hard exclusion from the Passport.")
        for creator, preference in passport.creator_preferences.items():
            if preference > 0.5 and creator not in state.following:
                add(ActionType.FOLLOW_CREATOR, creator, "Preserve a positively selected creator across accounts.")
            if preference < -0.5 and creator not in state.muted_creators:
                add(ActionType.MUTE_CREATOR, creator, "Apply an explicit negative creator preference.")
        if abs(state.serendipity - passport.serendipity) >= 0.02:
            add(
                ActionType.SET_SERENDIPITY,
                "exploration",
                "Match the Passport's exploration budget.",
                value=passport.serendipity,
            )
        if abs(state.source_cap - passport.max_source_share) >= 0.02:
            add(
                ActionType.SET_SOURCE_CAP,
                "source_concentration",
                "Limit the share of the feed supplied by one source.",
                value=passport.max_source_share,
            )

        desired = dict(passport.topic_targets)
        observed = dict(observation.topic_distribution)
        keys = set(desired) | set(observed)
        distance = min(1.0, 0.5 * sum(abs(desired.get(key, 0) - observed.get(key, 0)) for key in keys))
        return TranslationPlan(
            id=f"plan-{sha256(f'{passport.id}:{passport.version}:{observation.account_id}'.encode()).hexdigest()[:12]}",
            passport_id=passport.id,
            passport_version=passport.version,
            destination_id=observation.account_id,
            capability_level=CapabilityLevel.LAB,
            actions=tuple(actions),
            losses=(),
            created_at=now,
            estimated_topic_distance=distance,
        )

    def execute(self, account_id: str, action: ProposedAction, *, now: datetime) -> ActionOutcome:
        state = self._require_account(account_id)
        if action.destination_id != account_id:
            raise ValueError("action destination does not match account")
        previous = self._outcomes.get(action.idempotency_key)
        if previous is not None:
            if _canonical_action_request(previous.action) != _canonical_action_request(action):
                raise ValueError("idempotency key collision")
            return previous
        if action.action_type not in self.capabilities(account_id).execute:
            raise ValueError(f"unsupported Lab action: {action.action_type}")

        before, after = self._apply(state, action)
        outcome = ActionOutcome(
            action=action,
            status=ActionStatus.EXECUTED,
            before_state=before,
            after_state=after,
            executed_at=now,
            platform_reference=f"lab://{account_id}/{action.id}",
        )
        self._outcomes[action.idempotency_key] = outcome
        return outcome

    def sample(self, account_id: str, *, now: datetime, limit: int = 24) -> FeedSample:
        state = self._require_account(account_id)
        if limit < 1:
            raise ValueError("sample limit must be positive")

        ranked: list[tuple[float, FeedItem]] = []
        for item in self._candidates:
            if item.creator_id in state.muted_creators or set(item.topics) & state.muted_keywords:
                continue
            affinity = sum(state.topic_affinity.get(topic, 0.0) for topic in item.topics)
            followed = 1.0 if item.creator_id in state.following | state.subscriptions else 0.0
            score = affinity * 3.2 + followed * 1.35 + item.quality * 0.7 - item.outrage * 0.45
            ranked.append((score, item))
        ranked.sort(key=lambda pair: (-pair[0], pair[1].id))

        novel_target = min(limit, round(limit * state.serendipity))
        novel = [item for _, item in ranked if item.novelty >= 0.6]
        familiar = [item for _, item in ranked if item.novelty < 0.6]
        selected: list[FeedItem] = []
        source_counts: Counter[str] = Counter()
        source_limit = max(1, ceil(limit * state.source_cap))

        def take(pool: list[FeedItem], wanted: int) -> None:
            for item in pool:
                if len(selected) >= limit or wanted <= 0:
                    return
                if item in selected or source_counts[item.source] >= source_limit:
                    continue
                selected.append(item)
                source_counts[item.source] += 1
                wanted -= 1

        take(novel, novel_target)
        take(familiar, limit - len(selected))
        take([item for _, item in ranked], limit - len(selected))
        return FeedSample(platform=self.platform, account_id=account_id, items=tuple(selected), sampled_at=now)

    def rollback(self, account_id: str, receipt: ActionReceipt, *, now: datetime) -> RollbackOutcome:
        state = self._require_account(account_id)
        if receipt.destination_id != account_id:
            raise ValueError("receipt destination does not match account")
        restored: list[str] = []
        failed: list[str] = []
        for outcome in reversed(receipt.outcomes):
            if outcome.status is not ActionStatus.EXECUTED or not outcome.action.reversible:
                continue
            try:
                self._restore(state, outcome)
                self._outcomes[outcome.action.idempotency_key] = ActionOutcome(
                    action=outcome.action,
                    status=ActionStatus.ROLLED_BACK,
                    before_state=outcome.after_state,
                    after_state=outcome.before_state,
                    executed_at=now,
                    platform_reference=outcome.platform_reference,
                )
                restored.append(outcome.action.id)
            except (KeyError, TypeError, ValueError):
                failed.append(outcome.action.id)
        return RollbackOutcome(
            receipt_id=receipt.id,
            destination_id=account_id,
            restored_actions=tuple(restored),
            failed_actions=tuple(failed),
            completed_at=now,
            caveats=receipt.rollback_caveats,
        )

    def health(self, *, now: datetime) -> AdapterHealth:
        return AdapterHealth(
            platform=self.platform,
            healthy=bool(self._accounts and self._candidates),
            mode="deterministic_lab",
            checked_at=now,
            detail=f"{len(self._accounts)} seeded accounts and {len(self._candidates)} candidates loaded",
        )

    def _require_account(self, account_id: str) -> LabAccountState:
        try:
            return self._accounts[account_id]
        except KeyError as exc:
            raise KeyError(f"unknown Lab account: {account_id}") from exc

    @staticmethod
    def _snapshot(field_name: str, value: Any) -> dict[str, Any]:
        if isinstance(value, set):
            value = sorted(value)
        if isinstance(value, dict):
            value = {key: sorted(item) if isinstance(item, set) else item for key, item in value.items()}
        return {"field": field_name, "value": value}

    def _apply(self, state: LabAccountState, action: ProposedAction) -> tuple[dict[str, Any], dict[str, Any]]:
        action_type = action.action_type
        target = action.target
        if action_type in {ActionType.FOLLOW_CREATOR, ActionType.UNFOLLOW_CREATOR}:
            before = self._snapshot("following", state.following)
            state.following.add(target) if action_type is ActionType.FOLLOW_CREATOR else state.following.discard(target)
            return before, self._snapshot("following", state.following)
        if action_type in {ActionType.MUTE_CREATOR, ActionType.UNMUTE_CREATOR}:
            before = self._snapshot("muted_creators", state.muted_creators)
            state.muted_creators.add(target) if action_type is ActionType.MUTE_CREATOR else state.muted_creators.discard(target)
            return before, self._snapshot("muted_creators", state.muted_creators)
        if action_type in {ActionType.MUTE_KEYWORD, ActionType.HIDE_TOPIC, ActionType.UNMUTE_KEYWORD, ActionType.SHOW_TOPIC}:
            before = self._snapshot("muted_keywords", state.muted_keywords)
            if action_type in {ActionType.MUTE_KEYWORD, ActionType.HIDE_TOPIC}:
                state.muted_keywords.add(target)
            else:
                state.muted_keywords.discard(target)
            return before, self._snapshot("muted_keywords", state.muted_keywords)
        if action_type in {ActionType.SUBSCRIBE_CREATOR, ActionType.UNSUBSCRIBE_CREATOR}:
            before = self._snapshot("subscriptions", state.subscriptions)
            state.subscriptions.add(target) if action_type is ActionType.SUBSCRIBE_CREATOR else state.subscriptions.discard(target)
            return before, self._snapshot("subscriptions", state.subscriptions)
        if action_type in {ActionType.ADD_TO_LIST, ActionType.REMOVE_FROM_LIST}:
            list_name = str(action.parameters.get("list", "Feed Passport"))
            before = self._snapshot("lists", state.lists)
            members = state.lists.setdefault(list_name, set())
            members.add(target) if action_type is ActionType.ADD_TO_LIST else members.discard(target)
            return before, self._snapshot("lists", state.lists)
        if action_type in {ActionType.CREATE_CUSTOM_FEED, ActionType.INSTALL_CUSTOM_FEED}:
            before = self._snapshot("custom_feeds", state.custom_feeds)
            state.custom_feeds.add(target)
            return before, self._snapshot("custom_feeds", state.custom_feeds)
        if action_type is ActionType.SET_TOPIC_PREFERENCE:
            before = self._snapshot("topic_affinity", state.topic_affinity)
            state.topic_affinity[target] = float(action.parameters["weight"])
            state.topic_affinity = normalize_weights(state.topic_affinity)
            return before, self._snapshot("topic_affinity", state.topic_affinity)
        if action_type is ActionType.SET_SERENDIPITY:
            before = self._snapshot("serendipity", state.serendipity)
            state.serendipity = float(action.parameters["value"])
            return before, self._snapshot("serendipity", state.serendipity)
        if action_type is ActionType.SET_SOURCE_CAP:
            before = self._snapshot("source_cap", state.source_cap)
            state.source_cap = float(action.parameters["value"])
            return before, self._snapshot("source_cap", state.source_cap)
        raise ValueError(f"Lab action has no implementation: {action_type}")

    @staticmethod
    def _restore(state: LabAccountState, outcome: ActionOutcome) -> None:
        field_name = str(outcome.before_state["field"])
        value = outcome.before_state["value"]
        if field_name in {"following", "muted_creators", "muted_keywords", "subscriptions", "custom_feeds"}:
            setattr(state, field_name, set(value))
            return
        if field_name == "lists":
            state.lists = {key: set(items) for key, items in dict(value).items()}
            return
        if field_name == "topic_affinity":
            state.topic_affinity = {str(key): float(item) for key, item in dict(value).items()}
            return
        if field_name in {"serendipity", "source_cap"}:
            setattr(state, field_name, float(value))
            return
        raise KeyError(field_name)
