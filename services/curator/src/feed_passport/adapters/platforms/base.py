from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Literal, Mapping

from feed_passport.domain.models import (
    AccountObservation,
    ActionOutcome,
    ActionReceipt,
    ActionStatus,
    ActionType,
    AdapterHealth,
    CapabilityLevel,
    FeedPassport,
    FeedSample,
    PlatformCapabilityManifest,
    ProposedAction,
    RollbackOutcome,
    TranslationLoss,
    TranslationPlan,
)


TopicStrategy = Literal["custom_feed", "guided_more_less", "guided_sliders", "unavailable"]


class PlatformActionError(RuntimeError):
    """Base error for a platform capability boundary."""

    def __init__(self, platform: str, action_type: ActionType, code: str, detail: str) -> None:
        self.platform = platform
        self.action_type = action_type
        self.code = code
        super().__init__(detail)


class UnsupportedPlatformAction(PlatformActionError):
    """Raised when neither an official API nor an honest handoff supports an action."""


class LiveExecutionUnavailable(PlatformActionError):
    """Raised when an official API action has no authorized live transport configured."""


@dataclass(frozen=True, slots=True)
class PlatformProfile:
    """Evidence-backed platform facts used by a credential-free planning adapter."""

    platform: str
    level: CapabilityLevel
    observe: frozenset[str]
    official_actions: frozenset[ActionType]
    verify: frozenset[str]
    rollback: frozenset[ActionType]
    native_handoff_actions: frozenset[ActionType]
    evidence_urls: tuple[str, ...]
    reviewed_on: str
    notes: str
    positive_creator_action: ActionType | None = None
    negative_creator_action: ActionType | None = None
    exclusion_action: ActionType | None = None
    topic_action: ActionType | None = None
    topic_strategy: TopicStrategy = "unavailable"
    creator_action_limit: int = 12
    exclusion_action_limit: int = 8
    topic_action_limit: int = 5
    total_action_limit: int = 24
    handoff_instructions: tuple[tuple[ActionType, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.platform or not self.evidence_urls:
            raise ValueError("platform and at least one evidence URL are required")
        if any(not url.startswith("https://") for url in self.evidence_urls):
            raise ValueError("platform evidence URLs must use HTTPS")
        if not self.rollback <= self.official_actions:
            raise ValueError("rollback actions must be official executable actions")
        if (
            self.creator_action_limit < 1
            or self.exclusion_action_limit < 1
            or self.topic_action_limit < 1
            or self.total_action_limit < 1
        ):
            raise ValueError("compiler action limits must be positive")
        declared = self.official_actions | self.native_handoff_actions
        configured = {
            action
            for action in (
                self.positive_creator_action,
                self.negative_creator_action,
                self.exclusion_action,
                self.topic_action,
            )
            if action is not None
        }
        if not configured <= declared:
            raise ValueError("compiler actions must appear in the capability manifest")
        if self.topic_strategy == "custom_feed" and not {
            ActionType.CREATE_CUSTOM_FEED,
            ActionType.INSTALL_CUSTOM_FEED,
        } <= self.official_actions:
            raise ValueError("custom-feed compilation requires create and install capabilities")
        if self.topic_strategy in {"guided_more_less", "guided_sliders"}:
            if self.topic_action is None or self.topic_action not in self.native_handoff_actions:
                raise ValueError("guided topic compilation requires a guided topic action")

    @property
    def instruction_map(self) -> dict[ActionType, str]:
        return dict(self.handoff_instructions)


class ManifestPlatformAdapter:
    """Compile capability-safe plans without making external calls or holding credentials.

    Profiles describe actions supported by an official interface. This adapter deliberately
    has no live transport: official actions raise ``LiveExecutionUnavailable`` rather than
    returning a fabricated success, while native-UI actions return ``GUIDED``.
    """

    PROFILE: PlatformProfile
    platform: str

    def __init__(self, observations: Mapping[str, AccountObservation] | None = None) -> None:
        self._observations = dict(observations or {})
        for account_id, item in self._observations.items():
            self._validate_account_id(account_id)
            if item.account_id != account_id or item.platform != self.platform:
                raise ValueError("seeded observations must match their account and platform")

    def capabilities(self, account_id: str) -> PlatformCapabilityManifest:
        """Return only the capability certified for this credential-free adapter.

        Documented API actions remain guided until a separate live implementation passes
        authorized conformance. ``documented_capabilities`` exposes that candidate surface
        without causing policy to approve an unavailable mutation.
        """

        self._validate_account_id(account_id)
        profile = self.PROFILE
        return PlatformCapabilityManifest(
            platform=self.platform,
            level=CapabilityLevel.GUIDED,
            observe=frozenset({"user_supplied_snapshot"}),
            execute=frozenset(),
            verify=frozenset(),
            rollback=frozenset(),
            requires_user_handoff=profile.native_handoff_actions,
            evidence_url=profile.evidence_urls[0],
            certified_at=None,
        )

    def documented_capabilities(self, account_id: str) -> PlatformCapabilityManifest:
        """Return the official documented candidate surface, not a live certification."""

        self._validate_account_id(account_id)
        profile = self.PROFILE
        return PlatformCapabilityManifest(
            platform=self.platform,
            level=profile.level,
            observe=profile.observe,
            execute=profile.official_actions,
            verify=profile.verify,
            rollback=profile.rollback,
            requires_user_handoff=profile.native_handoff_actions - profile.official_actions,
            evidence_url=profile.evidence_urls[0],
            certified_at=None,
        )

    def observe(self, account_id: str, *, now: datetime, sample_size: int = 24) -> AccountObservation:
        self._validate_account_id(account_id)
        if sample_size < 1:
            raise ValueError("sample size must be positive")
        existing = self._observations.get(account_id)
        if existing is None:
            sample = FeedSample(platform=self.platform, account_id=account_id, items=(), sampled_at=now)
            return AccountObservation(
                platform=self.platform,
                account_id=account_id,
                observed_at=now,
                topic_distribution={"unobserved": 1.0},
                followed_creators=frozenset(),
                muted_creators=frozenset(),
                muted_keywords=frozenset(),
                sample=sample,
                confidence=0.0,
            )

        sample = FeedSample(
            platform=self.platform,
            account_id=account_id,
            items=existing.sample.items[:sample_size],
            sampled_at=existing.sample.sampled_at,
        )
        return AccountObservation(
            platform=self.platform,
            account_id=account_id,
            observed_at=existing.observed_at,
            topic_distribution=existing.topic_distribution,
            followed_creators=existing.followed_creators,
            muted_creators=existing.muted_creators,
            muted_keywords=existing.muted_keywords,
            sample=sample,
            confidence=existing.confidence,
        )

    def compile(
        self,
        passport: FeedPassport,
        observation: AccountObservation,
        *,
        now: datetime,
    ) -> TranslationPlan:
        if observation.platform != self.platform:
            raise ValueError(
                f"{self.platform} adapter cannot compile an observation from {observation.platform}"
            )
        self._validate_account_id(observation.account_id)
        profile = self.PROFILE
        configured = self.capabilities(observation.account_id)
        effective_topic_strategy = profile.topic_strategy
        if profile.topic_strategy == "custom_feed" and not {
            ActionType.CREATE_CUSTOM_FEED,
            ActionType.INSTALL_CUSTOM_FEED,
        } <= configured.execute:
            effective_topic_strategy = "unavailable"
        actions: list[ProposedAction] = []
        losses: list[TranslationLoss] = []
        seen_actions: set[tuple[ActionType, str]] = set()
        truncated_actions = 0

        def add(
            action_type: ActionType,
            target: str,
            reason: str,
            **parameters: object,
        ) -> None:
            nonlocal truncated_actions
            key_tuple = (action_type, target)
            if key_tuple in seen_actions:
                return
            if len(actions) >= profile.total_action_limit:
                truncated_actions += 1
                return
            seen_actions.add(key_tuple)
            if action_type in configured.requires_user_handoff:
                delivery = "guided_handoff"
                reversible = False
                instruction = profile.instruction_map.get(
                    action_type,
                    "Complete this control in the platform's official user interface.",
                )
                parameters["instruction"] = instruction
                if action_type in profile.official_actions:
                    parameters["documented_delivery"] = "official_api"
                    parameters["certification"] = "live_transport_not_certified"
            elif action_type in configured.execute:
                delivery = "official_api"
                reversible = action_type in configured.rollback
            else:
                raise UnsupportedPlatformAction(
                    self.platform,
                    action_type,
                    "compile_capability_mismatch",
                    f"{self.platform} cannot compile {action_type.value}",
                )
            parameters.update(
                {
                    "delivery": delivery,
                    "evidence_urls": profile.evidence_urls,
                    "capability_reviewed_on": profile.reviewed_on,
                }
            )
            ordinal = len(actions) + 1
            raw_key = (
                f"{self.platform}:{passport.id}:{passport.version}:{observation.account_id}:"
                f"{action_type.value}:{target}:{ordinal}"
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

        if effective_topic_strategy == "custom_feed":
            feed_key = f"feed-passport-{passport.id}-v{passport.version}"
            add(
                ActionType.CREATE_CUSTOM_FEED,
                feed_key,
                "Publish a user-selectable custom feed that implements the Passport policy.",
                topic_targets=dict(passport.topic_targets),
                creator_preferences=dict(passport.creator_preferences),
                format_preferences=dict(passport.format_preferences),
                languages=passport.languages,
                hard_exclusions=tuple(sorted(passport.hard_exclusions)),
                serendipity=passport.serendipity,
                max_outrage=passport.max_outrage,
                max_source_share=passport.max_source_share,
            )
            add(
                ActionType.INSTALL_CUSTOM_FEED,
                feed_key,
                "Save the newly published Feed Passport feed to the account's feed list.",
                source_action=actions[-1].id,
            )

        creator_candidates = [
            (creator, float(preference))
            for creator, preference in sorted(passport.creator_preferences.items())
            if abs(float(preference)) > 0.5
        ]
        for creator, preference in creator_candidates[: profile.creator_action_limit]:
            if preference > 0 and profile.positive_creator_action is not None:
                if creator not in observation.followed_creators:
                    add(
                        profile.positive_creator_action,
                        creator,
                        "Preserve an explicitly preferred creator on the destination account.",
                        preference=preference,
                    )
            elif preference < 0 and profile.negative_creator_action is not None:
                if creator not in observation.muted_creators:
                    add(
                        profile.negative_creator_action,
                        creator,
                        "Apply an explicit negative creator preference without public engagement.",
                        preference=preference,
                    )
            else:
                losses.append(
                    TranslationLoss(
                        field=f"creator_preferences.{creator}",
                        requested=f"preference {preference:+.2f}",
                        reason=f"{self.platform} exposes no authorized matching creator control.",
                        severity="medium",
                        workaround="Retain the preference in the Passport for a future supported adapter.",
                    )
                )
        if len(creator_candidates) > profile.creator_action_limit:
            losses.append(
                TranslationLoss(
                    field="creator_preferences",
                    requested=f"{len(creator_candidates)} explicit creator preferences",
                    reason="The compiler applies a conservative per-plan action limit.",
                    severity="medium",
                    workaround="Apply another separately approved batch after reviewing the first receipt.",
                )
            )

        exclusions = sorted(passport.hard_exclusions)
        for exclusion in exclusions[: profile.exclusion_action_limit]:
            if exclusion in observation.muted_keywords:
                continue
            if profile.exclusion_action is None:
                losses.append(
                    TranslationLoss(
                        field=f"hard_exclusions.{exclusion}",
                        requested="hard exclusion",
                        reason=f"{self.platform} has no authorized arbitrary topic-exclusion control.",
                        severity="high",
                        workaround="Use the closest native control manually and keep the mismatch visible.",
                    )
                )
                continue
            exclusion_parameters: dict[str, object] = {"match": "keyword_or_topic"}
            if profile.exclusion_action is ActionType.SET_TOPIC_PREFERENCE:
                exclusion_parameters.update({"direction": "less", "requested_weight": 0.0})
            add(
                profile.exclusion_action,
                exclusion,
                "Translate a Passport hard exclusion into the closest authorized platform control.",
                **exclusion_parameters,
            )
            if profile.exclusion_action is not ActionType.MUTE_KEYWORD:
                losses.append(
                    TranslationLoss(
                        field=f"hard_exclusions.{exclusion}",
                        requested="hard exclusion",
                        reason="The native control reduces a topic but does not guarantee exclusion.",
                        severity="high",
                        workaround="Verify the feed and repeat the native less-topic control if necessary.",
                    )
                )

        if len(exclusions) > profile.exclusion_action_limit:
            losses.append(
                TranslationLoss(
                    field="hard_exclusions",
                    requested=f"{len(exclusions)} hard exclusions",
                    reason="The compiler applies a conservative per-plan exclusion limit.",
                    severity="medium",
                    workaround=(
                        "Apply another separately approved batch after reviewing the first receipt."
                    ),
                )
            )

        if effective_topic_strategy in {"guided_more_less", "guided_sliders"}:
            for topic, weight in sorted(
                passport.topic_targets.items(), key=lambda item: (-float(item[1]), item[0])
            )[: profile.topic_action_limit]:
                parameters: dict[str, object] = {
                    "direction": "more",
                    "requested_weight": float(weight),
                }
                if effective_topic_strategy == "guided_sliders":
                    parameters["control"] = "manage_topics"
                else:
                    parameters["control"] = "more_less_topics"
                add(
                    profile.topic_action,  # type: ignore[arg-type]
                    topic,
                    "Approximate a Passport topic target with an explicit native topic control.",
                    **parameters,
                )
            losses.append(
                TranslationLoss(
                    field="topic_targets",
                    requested="normalized weighted topic distribution",
                    reason=(
                        "The platform's native topic controls are approximate and do not "
                        "accept exact weights."
                    ),
                    severity="medium",
                    workaround="Use the proposed native controls, then compare a later user-provided sample.",
                )
            )
        elif effective_topic_strategy == "unavailable":
            losses.append(
                TranslationLoss(
                    field="topic_targets",
                    requested="normalized weighted topic distribution",
                    reason=f"{self.platform} exposes no authorized API for personalized topic weights.",
                    severity="high",
                    workaround=(
                        "Preserve the target in the Passport and use guided native controls "
                        "where possible."
                    ),
                )
            )

        if effective_topic_strategy != "custom_feed":
            losses.append(
                TranslationLoss(
                    field="ranking_constraints",
                    requested=(
                        f"serendipity={passport.serendipity:.2f}, max_outrage={passport.max_outrage:.2f}, "
                        f"max_source_share={passport.max_source_share:.2f}"
                    ),
                    reason="The platform does not expose authorized setters for these ranking constraints.",
                    severity="high",
                    workaround=(
                        "Measure these values externally without claiming the live ranker "
                        "was rewritten."
                    ),
                )
            )
        if passport.format_preferences and effective_topic_strategy != "custom_feed":
            losses.append(
                TranslationLoss(
                    field="format_preferences",
                    requested=str(dict(passport.format_preferences)),
                    reason="The available official controls do not accept weighted format preferences.",
                    severity="medium",
                    workaround="Keep format intent in the Passport for platforms with custom-feed support.",
                )
            )
        if observation.confidence == 0:
            losses.append(
                TranslationLoss(
                    field="destination_observation",
                    requested="verified destination state",
                    reason="No authorized account snapshot was supplied to this credential-free adapter.",
                    severity="medium",
                    workaround="Import or fetch an authorized snapshot before approving the plan.",
                )
            )
        if truncated_actions:
            losses.append(
                TranslationLoss(
                    field="action_budget",
                    requested=f"at least {len(actions) + truncated_actions} compiled controls",
                    reason=(
                        f"The credential-free compiler caps one plan at {profile.total_action_limit} actions."
                    ),
                    severity="medium",
                    workaround="Create a separately reviewed and approved follow-up plan.",
                )
            )

        desired = dict(passport.topic_targets)
        observed = dict(observation.topic_distribution)
        keys = set(desired) | set(observed)
        distance = min(
            1.0,
            0.5 * sum(abs(desired.get(key, 0.0) - observed.get(key, 0.0)) for key in keys),
        )
        plan_key = sha256(
            f"{self.platform}:{passport.id}:{passport.version}:{observation.account_id}".encode()
        ).hexdigest()[:12]
        return TranslationPlan(
            id=f"plan-{plan_key}",
            passport_id=passport.id,
            passport_version=passport.version,
            destination_id=observation.account_id,
            capability_level=configured.level,
            actions=tuple(actions),
            losses=tuple(losses),
            created_at=now,
            estimated_topic_distance=distance,
        )

    def execute(self, account_id: str, action: ProposedAction, *, now: datetime) -> ActionOutcome:
        self._validate_account_id(account_id)
        if action.destination_id != account_id:
            raise ValueError("action destination does not match account")
        configured = self.capabilities(account_id)
        if action.action_type in configured.requires_user_handoff:
            return ActionOutcome(
                action=action,
                status=ActionStatus.GUIDED,
                before_state={"delivery": "native_user_interface", "completed": False},
                after_state={"delivery": "native_user_interface", "completed": False},
                executed_at=now,
                platform_reference=f"handoff://{self.platform}/{action.id}",
            )
        if action.action_type in configured.execute:
            raise LiveExecutionUnavailable(
                self.platform,
                action.action_type,
                "live_transport_unavailable",
                (
                    f"{self.platform} documents {action.action_type.value}, but this planning adapter "
                    "has no authorized live transport and will not fabricate execution."
                ),
            )
        raise UnsupportedPlatformAction(
            self.platform,
            action.action_type,
            "capability_unavailable",
            f"{self.platform} does not support {action.action_type.value} through this adapter",
        )

    def sample(self, account_id: str, *, now: datetime, limit: int = 24) -> FeedSample:
        self._validate_account_id(account_id)
        if limit < 1:
            raise ValueError("sample limit must be positive")
        existing = self._observations.get(account_id)
        if existing is None:
            return FeedSample(platform=self.platform, account_id=account_id, items=(), sampled_at=now)
        return FeedSample(
            platform=self.platform,
            account_id=account_id,
            items=existing.sample.items[:limit],
            sampled_at=existing.sample.sampled_at,
        )

    def rollback(self, account_id: str, receipt: ActionReceipt, *, now: datetime) -> RollbackOutcome:
        self._validate_account_id(account_id)
        if receipt.destination_id != account_id:
            raise ValueError("receipt destination does not match account")
        failed = tuple(
            outcome.action.id
            for outcome in reversed(receipt.outcomes)
            if outcome.status is ActionStatus.EXECUTED
            and outcome.action.reversible
            and outcome.action.action_type in self.PROFILE.rollback
        )
        return RollbackOutcome(
            receipt_id=receipt.id,
            destination_id=account_id,
            restored_actions=(),
            failed_actions=failed,
            completed_at=now,
            caveats=(
                "This credential-free adapter cannot confirm or reverse mutations made by "
                "an external client.",
            ),
        )

    def health(self, *, now: datetime) -> AdapterHealth:
        return AdapterHealth(
            platform=self.platform,
            healthy=True,
            mode="planning_only_no_credentials",
            checked_at=now,
            detail=(
                f"Capability plan available; {self.PROFILE.notes} "
                "No live API transport or credentials are configured."
            ),
        )

    @staticmethod
    def _validate_account_id(account_id: str) -> None:
        if not account_id or not account_id.strip():
            raise ValueError("account id is required")
