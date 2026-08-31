from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from typing import Any

from feed_passport.adapters.lab import LabAdapter
from feed_passport.adapters.platforms.base import PlatformProfile, UnsupportedPlatformAction
from feed_passport.domain import (
    AccountObservation,
    ActionOutcome,
    ActionReceipt,
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


PUBLIC_ENGAGEMENT_ACTIONS = frozenset(
    {
        ActionType.LIKE,
        ActionType.COMMENT,
        ActionType.POST,
        ActionType.REPOST,
        ActionType.SEND_MESSAGE,
    }
)

SIMULATION_DISCLAIMER = (
    "This is an isolated deterministic local control simulation. It does not connect to, "
    "observe, or mutate a real social-media account."
)
RANKING_DISCLAIMER = (
    "The deterministic fixture feed is not evidence of platform ranking behavior or ranking fidelity."
)
LOCAL_TWIN_STATE_FINGERPRINT_SCHEMA = "local-twin-control-state/v1"


def twin_platform_id(platform: str) -> str:
    """Return the stable registry id for a local platform control twin."""

    normalized = platform.strip().lower()
    if normalized.startswith("twin:"):
        normalized = normalized.removeprefix("twin:").strip()
    if not normalized:
        raise ValueError("a source platform is required")
    return f"twin:{normalized}"


class LocalPlatformTwinAdapter:
    """Execute declared account-control semantics against isolated Lab state.

    The profile limits which controls are simulated. ``LabAdapter`` supplies deterministic
    state transitions, samples, idempotency, cloning, and rollback. Neither the candidate
    feed nor its response to a control models a real platform ranker.
    """

    def __init__(self, *, profile: PlatformProfile, lab: LabAdapter | None = None) -> None:
        self.profile = profile
        self.PROFILE = profile
        self.platform = twin_platform_id(profile.platform)
        self._lab = lab or LabAdapter()

    @property
    def _accounts(self) -> dict[str, Any]:
        """Expose known fixture ids to the existing application adapter registry."""

        return self._lab._accounts  # noqa: SLF001 - this class is an intentional Lab facade

    def clone(self) -> LocalPlatformTwinAdapter:
        return type(self)(profile=self.profile, lab=self._lab.clone())

    def export_state(self, account_id: str) -> dict[str, Any]:
        value = self._lab.export_state(account_id)
        value["_local_twin"] = {
            "platform": self.platform,
            "simulation_only": True,
            "ranking_fidelity": "not_claimed",
        }
        return value

    def state_fingerprint(self, account_id: str) -> dict[str, str]:
        """Hash deterministic local control state without exposing the state itself."""

        canonical = json.dumps(
            self.export_state(account_id),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        namespaced = f"{LOCAL_TWIN_STATE_FINGERPRINT_SCHEMA}\0{canonical}".encode("utf-8")
        return {
            "kind": "local_twin_control_state",
            "schema": LOCAL_TWIN_STATE_FINGERPRINT_SCHEMA,
            "algorithm": "sha256",
            "digest": sha256(namespaced).hexdigest(),
            "scope": "deterministic_local_twin_only",
        }

    def import_state(self, value: dict[str, Any]) -> None:
        metadata = value.get("_local_twin")
        if metadata is not None and dict(metadata).get("platform") != self.platform:
            raise ValueError("local twin state belongs to a different platform")
        self._lab.import_state(value)

    def reset(self, account_id: str | None = None) -> None:
        self._lab.reset(account_id)

    def capabilities(self, account_id: str) -> PlatformCapabilityManifest:
        lab_manifest = self._lab.capabilities(account_id)
        declared_controls = self.profile.official_actions | self.profile.native_handoff_actions
        simulated_controls = (declared_controls & lab_manifest.execute) - PUBLIC_ENGAGEMENT_ACTIONS
        return PlatformCapabilityManifest(
            platform=self.platform,
            level=CapabilityLevel.LAB,
            observe=frozenset({"deterministic_fixture_feed", "simulated_account_control_state"}),
            execute=simulated_controls,
            verify=frozenset({"deterministic_fixture_feed", "simulated_account_control_state"}),
            rollback=simulated_controls,
            requires_user_handoff=frozenset(),
            evidence_url=(
                f"docs://local-platform-control-twin/{self.profile.platform}/"
                "simulation-only-no-ranking-fidelity"
            ),
            certified_at=None,
        )

    def observe(self, account_id: str, *, now: datetime, sample_size: int = 24) -> AccountObservation:
        observation = self._lab.observe(account_id, now=now, sample_size=sample_size)
        sample = replace(observation.sample, platform=self.platform)
        return replace(observation, platform=self.platform, sample=sample)

    def compile(
        self,
        passport: FeedPassport,
        observation: AccountObservation,
        *,
        now: datetime,
    ) -> TranslationPlan:
        if observation.platform != self.platform:
            raise ValueError(
                f"{self.platform} cannot compile an observation from {observation.platform}"
            )
        if observation.account_id != observation.sample.account_id:
            raise ValueError("observation and sample account ids must match")

        account_id = observation.account_id
        manifest = self.capabilities(account_id)
        state = self._lab.export_state(account_id)
        lab_observation = replace(
            observation,
            platform=self._lab.platform,
            sample=replace(observation.sample, platform=self._lab.platform),
        )
        lab_plan = self._lab.compile(passport, lab_observation, now=now)
        actions: list[ProposedAction] = []
        losses: list[TranslationLoss] = [
            TranslationLoss(
                field="simulation_boundary",
                requested=f"exercise {self.profile.platform} control semantics without an account",
                reason=SIMULATION_DISCLAIMER,
                severity="info",
                workaround=(
                    "Run separately authorized conformance tests before adding any real account transport."
                ),
            ),
            TranslationLoss(
                field="ranking_fidelity",
                requested=f"predict the personalized {self.profile.platform} feed",
                reason=RANKING_DISCLAIMER,
                severity="high",
                workaround=(
                    "Treat samples as reproducible agent-workflow fixtures, never platform predictions."
                ),
            ),
        ]
        seen: set[tuple[ActionType, str]] = set()
        truncated = 0

        def add(
            action_type: ActionType | None,
            target: str,
            reason: str,
            **parameters: Any,
        ) -> ProposedAction | None:
            nonlocal truncated
            if action_type is None or action_type not in manifest.execute:
                return None
            if action_type in PUBLIC_ENGAGEMENT_ACTIONS:
                raise AssertionError("public engagement cannot enter a local twin plan")
            key_tuple = (action_type, target)
            if key_tuple in seen:
                return None
            if len(actions) >= self.profile.total_action_limit:
                truncated += 1
                return None
            seen.add(key_tuple)
            parameters.update(
                {
                    "simulation": True,
                    "control_twin_for": self.profile.platform,
                    "evidence_scope": "control_semantics_only",
                    "ranking_fidelity": "not_claimed",
                    "profile_evidence_urls": self.profile.evidence_urls,
                    "profile_reviewed_on": self.profile.reviewed_on,
                }
            )
            ordinal = len(actions) + 1
            fingerprint = json.dumps(
                {
                    "action_type": action_type.value,
                    "target": target,
                    "parameters": parameters,
                    "reason": reason,
                },
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            key = sha256(
                (
                    f"{self.platform}:{passport.id}:{passport.version}:{account_id}:"
                    f"{ordinal}:{fingerprint}"
                ).encode("utf-8")
            ).hexdigest()[:24]
            action = ProposedAction(
                id=f"twin-action-{key[:12]}",
                destination_id=account_id,
                action_type=action_type,
                target=target,
                reason=reason,
                idempotency_key=key,
                reversible=True,
                parameters=parameters,
            )
            actions.append(action)
            return action

        if self.profile.topic_strategy == "custom_feed":
            feed_key = f"feed-passport-{passport.id}-v{passport.version}"
            created = add(
                ActionType.CREATE_CUSTOM_FEED,
                feed_key,
                "Exercise creation of a local custom-feed control artifact.",
                topic_targets=dict(passport.topic_targets),
                creator_preferences=dict(passport.creator_preferences),
                format_preferences=dict(passport.format_preferences),
                languages=passport.languages,
                hard_exclusions=tuple(sorted(passport.hard_exclusions)),
            )
            add(
                ActionType.INSTALL_CUSTOM_FEED,
                feed_key,
                "Exercise installation of the local custom-feed control artifact.",
                source_action=created.id if created is not None else None,
            )
        elif self.profile.topic_strategy in {"guided_more_less", "guided_sliders"}:
            for topic, weight in sorted(
                passport.topic_targets.items(), key=lambda item: (-float(item[1]), item[0])
            )[: self.profile.topic_action_limit]:
                add(
                    self.profile.topic_action,
                    topic,
                    "Exercise the profile's declared topic-preference control in the local twin.",
                    weight=float(weight),
                    requested_weight=float(weight),
                    simulated_precision="fixture_only",
                    control=self.profile.topic_strategy,
                )
        else:
            losses.append(
                TranslationLoss(
                    field="topic_targets",
                    requested="weighted topic controls",
                    reason=(
                        f"The {self.profile.platform} profile declares no compilable topic-control "
                        "strategy; the twin will not invent one."
                    ),
                    severity="high",
                    workaround="Retain topic intent in the Passport while testing supported controls.",
                )
            )

        creator_candidates = [
            (creator, float(preference))
            for creator, preference in sorted(passport.creator_preferences.items())
            if abs(float(preference)) > 0.5
        ]
        for creator, preference in creator_candidates[: self.profile.creator_action_limit]:
            if preference > 0:
                action_type = self.profile.positive_creator_action
                collection = (
                    state["subscriptions"]
                    if action_type is ActionType.SUBSCRIBE_CREATOR
                    else state["following"]
                )
                if creator not in collection:
                    add(
                        action_type,
                        creator,
                        "Exercise a declared positive creator control in isolated twin state.",
                        preference=preference,
                    )
            else:
                action_type = self.profile.negative_creator_action
                if creator not in state["muted_creators"]:
                    add(
                        action_type,
                        creator,
                        "Exercise a declared negative creator control in isolated twin state.",
                        preference=preference,
                    )

        for exclusion in sorted(passport.hard_exclusions)[: self.profile.exclusion_action_limit]:
            action_type = self.profile.exclusion_action
            if action_type is None:
                losses.append(
                    TranslationLoss(
                        field=f"hard_exclusions.{exclusion}",
                        requested="hard exclusion",
                        reason=(
                            f"The {self.profile.platform} profile declares no equivalent exclusion "
                            "control; the twin will not fabricate one."
                        ),
                        severity="high",
                        workaround="Keep the exclusion visible as an unsupported intent.",
                    )
                )
                continue
            if action_type is ActionType.MUTE_KEYWORD and exclusion in state["muted_keywords"]:
                continue
            parameters: dict[str, Any] = {"match": "keyword_or_topic"}
            if action_type is ActionType.SET_TOPIC_PREFERENCE:
                parameters.update(
                    {
                        "weight": 0.0,
                        "requested_weight": 0.0,
                        "simulated_precision": "fixture_only",
                    }
                )
            add(
                action_type,
                exclusion,
                "Exercise the closest declared exclusion control in isolated twin state.",
                **parameters,
            )

        if passport.format_preferences and self.profile.topic_strategy != "custom_feed":
            losses.append(
                TranslationLoss(
                    field="format_preferences",
                    requested=str(dict(passport.format_preferences)),
                    reason="The profile declares no weighted format-preference control.",
                    severity="medium",
                    workaround="Retain format intent in the Passport for a supported adapter.",
                )
            )
        if len(creator_candidates) > self.profile.creator_action_limit or truncated:
            losses.append(
                TranslationLoss(
                    field="action_budget",
                    requested="all requested local control changes",
                    reason="The profile's conservative deterministic action budget was reached.",
                    severity="medium",
                    workaround="Prepare a separately approved follow-up twin run.",
                )
            )

        plan_key = sha256(
            f"{self.platform}:{passport.id}:{passport.version}:{account_id}".encode("utf-8")
        ).hexdigest()[:12]
        return TranslationPlan(
            id=f"twin-plan-{plan_key}",
            passport_id=passport.id,
            passport_version=passport.version,
            destination_id=account_id,
            capability_level=CapabilityLevel.LAB,
            actions=tuple(actions),
            losses=tuple(losses),
            created_at=now,
            estimated_topic_distance=lab_plan.estimated_topic_distance,
        )

    def execute(self, account_id: str, action: ProposedAction, *, now: datetime) -> ActionOutcome:
        if action.destination_id != account_id:
            raise ValueError("action destination does not match account")
        if action.action_type in PUBLIC_ENGAGEMENT_ACTIONS:
            raise UnsupportedPlatformAction(
                self.platform,
                action.action_type,
                "public_engagement_forbidden",
                "Local platform twins never simulate public engagement actions.",
            )
        manifest = self.capabilities(account_id)
        if action.action_type not in manifest.execute:
            raise UnsupportedPlatformAction(
                self.platform,
                action.action_type,
                "twin_control_unavailable",
                (
                    f"{self.profile.platform} does not declare {action.action_type.value} "
                    "as an allowed local control-twin action."
                ),
            )

        parameters = dict(action.parameters)
        parameters.setdefault("simulation", True)
        parameters.setdefault("control_twin_for", self.profile.platform)
        parameters.setdefault("evidence_scope", "control_semantics_only")
        parameters.setdefault("ranking_fidelity", "not_claimed")
        parameters.setdefault("profile_evidence_urls", self.profile.evidence_urls)
        parameters.setdefault("profile_reviewed_on", self.profile.reviewed_on)
        if action.action_type is ActionType.SET_TOPIC_PREFERENCE and "weight" not in parameters:
            if "requested_weight" not in parameters:
                raise ValueError("a simulated topic preference requires a numeric weight")
            parameters["weight"] = float(parameters["requested_weight"])
        decorated = replace(action, parameters=parameters)

        outcome = self._lab.execute(account_id, decorated, now=now)
        if outcome.platform_reference and outcome.platform_reference.startswith("twin://"):
            return outcome
        wrapped = replace(
            outcome,
            platform_reference=(
                f"twin://{self.profile.platform}/{account_id}/{action.id}/"
                "simulation-only-no-ranking-fidelity"
            ),
        )
        self._lab._outcomes[action.idempotency_key] = wrapped  # noqa: SLF001
        return wrapped

    def sample(self, account_id: str, *, now: datetime, limit: int = 24) -> FeedSample:
        sample = self._lab.sample(account_id, now=now, limit=limit)
        return replace(sample, platform=self.platform)

    def rollback(self, account_id: str, receipt: ActionReceipt, *, now: datetime) -> RollbackOutcome:
        outcome = self._lab.rollback(account_id, receipt, now=now)
        caveats = tuple(
            dict.fromkeys(
                (
                    *outcome.caveats,
                    SIMULATION_DISCLAIMER,
                    RANKING_DISCLAIMER,
                )
            )
        )
        return replace(outcome, caveats=caveats)

    def health(self, *, now: datetime) -> AdapterHealth:
        lab_health = self._lab.health(now=now)
        return AdapterHealth(
            platform=self.platform,
            healthy=lab_health.healthy,
            mode="deterministic_local_control_twin",
            checked_at=now,
            detail=(
                f"{SIMULATION_DISCLAIMER} {RANKING_DISCLAIMER} "
                f"Allowed controls are limited to the {self.profile.platform} profile reviewed "
                f"on {self.profile.reviewed_on}."
            ),
        )
