from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, Mapping

from .models import ActionStatus, ActionType, ProposedAction, TranslationPlan


class GuidedHandoffState(StrEnum):
    PREVIEWED = "previewed"
    CONSENTED = "consented"
    AWAITING_HANDOFF = "awaiting_handoff"
    USER_RESOLVED = "user_resolved"
    FINALIZED = "finalized"


class GuidedStepResolution(StrEnum):
    COMPLETED_BY_USER = "completed_by_user"
    SKIPPED_BY_USER = "skipped_by_user"
    CONTROL_NOT_FOUND = "control_not_found"


class GuidedHandoffError(RuntimeError):
    pass


class GuidedHandoffTransitionError(GuidedHandoffError):
    pass


class GuidedHandoffConflict(GuidedHandoffError):
    pass


class GuidedHandoffOwnerError(GuidedHandoffError, PermissionError):
    pass


def _require_text(value: object, field_name: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a non-empty trimmed string")
    if len(value) > maximum:
        raise ValueError(f"{field_name} exceeds its {maximum}-character limit")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field_name} cannot contain control characters")
    return value


def _aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _resolution_status(resolution: GuidedStepResolution) -> ActionStatus:
    if resolution is GuidedStepResolution.COMPLETED_BY_USER:
        return ActionStatus.GUIDED
    return ActionStatus.SKIPPED


@dataclass(frozen=True, slots=True)
class GuidedHandoffStep:
    id: str
    ordinal: int
    action_type: ActionType
    target: str
    instruction: str
    resolution: GuidedStepResolution | None = None
    resolved_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_text(self.id, "step id", maximum=128)
        if self.ordinal < 1:
            raise ValueError("step ordinal must be positive")
        object.__setattr__(self, "action_type", ActionType(self.action_type))
        _require_text(self.target, "step target")
        _require_text(self.instruction, "step instruction", maximum=1_000)
        if self.resolution is None:
            if self.resolved_at is not None:
                raise ValueError("an unresolved step cannot have resolved_at")
            return
        object.__setattr__(self, "resolution", GuidedStepResolution(self.resolution))
        if self.resolved_at is None:
            raise ValueError("a resolved step requires resolved_at")
        _aware(self.resolved_at, "step resolved_at")

    @property
    def outcome_status(self) -> ActionStatus | None:
        if self.resolution is None:
            return None
        return _resolution_status(self.resolution)


def _step_definition(step: GuidedHandoffStep) -> dict[str, object]:
    return {
        "id": step.id,
        "ordinal": step.ordinal,
        "action_type": step.action_type.value,
        "target": step.target,
        "instruction": step.instruction,
    }


def guided_steps_sha256(
    *,
    platform: str,
    plan_id: str,
    steps: tuple[GuidedHandoffStep, ...],
) -> str:
    canonical = json.dumps(
        {
            "platform": platform,
            "plan_id": plan_id,
            "steps": [_step_definition(step) for step in steps],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class GuidedHandoffOutcome:
    step_id: str
    action_type: ActionType
    target: str
    resolution: GuidedStepResolution
    status: ActionStatus

    def __post_init__(self) -> None:
        _require_text(self.step_id, "outcome step id", maximum=128)
        object.__setattr__(self, "action_type", ActionType(self.action_type))
        _require_text(self.target, "outcome target")
        resolution = GuidedStepResolution(self.resolution)
        status = ActionStatus(self.status)
        object.__setattr__(self, "resolution", resolution)
        object.__setattr__(self, "status", status)
        if status not in {ActionStatus.GUIDED, ActionStatus.SKIPPED}:
            raise ValueError("guided handoff outcomes may only be GUIDED or SKIPPED")
        if status is not _resolution_status(resolution):
            raise ValueError("guided handoff outcome status does not match its user resolution")


@dataclass(frozen=True, slots=True)
class GuidedHandoffSummary:
    total_steps: int
    completed_by_user: int
    skipped_by_user: int
    control_not_found: int
    api_writes: int = field(default=0, init=False)
    recommendation_outcomes_verified: int = field(default=0, init=False)
    platform_verified: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.total_steps < 1:
            raise ValueError("guided handoff summary requires at least one step")
        if min(self.completed_by_user, self.skipped_by_user, self.control_not_found) < 0:
            raise ValueError("guided handoff summary counts cannot be negative")
        if (
            self.completed_by_user + self.skipped_by_user + self.control_not_found
            != self.total_steps
        ):
            raise ValueError("guided handoff summary counts must account for every step")


@dataclass(frozen=True, slots=True)
class GuidedHandoffReceipt:
    id: str
    session_id: str
    platform: str
    plan_id: str
    issued_at: datetime
    outcomes: tuple[GuidedHandoffOutcome, ...]
    summary: GuidedHandoffSummary

    def __post_init__(self) -> None:
        _require_text(self.id, "receipt id", maximum=160)
        _require_text(self.session_id, "receipt session id", maximum=128)
        _require_text(self.platform, "receipt platform", maximum=64)
        _require_text(self.plan_id, "receipt plan id", maximum=128)
        _aware(self.issued_at, "receipt issued_at")
        object.__setattr__(self, "outcomes", tuple(self.outcomes))
        if not self.outcomes:
            raise ValueError("guided handoff receipt requires outcomes")
        if len({outcome.step_id for outcome in self.outcomes}) != len(self.outcomes):
            raise ValueError("guided handoff receipt outcomes require unique step ids")
        completed = sum(
            outcome.resolution is GuidedStepResolution.COMPLETED_BY_USER
            for outcome in self.outcomes
        )
        skipped = sum(
            outcome.resolution is GuidedStepResolution.SKIPPED_BY_USER for outcome in self.outcomes
        )
        missing = sum(
            outcome.resolution is GuidedStepResolution.CONTROL_NOT_FOUND
            for outcome in self.outcomes
        )
        if (
            self.summary.total_steps != len(self.outcomes)
            or self.summary.completed_by_user != completed
            or self.summary.skipped_by_user != skipped
            or self.summary.control_not_found != missing
        ):
            raise ValueError("guided handoff receipt summary does not match its outcomes")


@dataclass(frozen=True, slots=True)
class GuidedHandoffSession:
    id: str
    owner_id: str
    platform: str
    plan_id: str
    passport_id: str
    passport_version: int
    state: GuidedHandoffState
    steps: tuple[GuidedHandoffStep, ...]
    steps_sha256: str
    created_at: datetime
    updated_at: datetime
    revision: int
    consent_reference: str | None = None
    consented_at: datetime | None = None
    handoff_started_at: datetime | None = None
    user_resolved_at: datetime | None = None
    finalized_at: datetime | None = None
    receipt: GuidedHandoffReceipt | None = None

    def __post_init__(self) -> None:
        _require_text(self.id, "session id", maximum=128)
        _require_text(self.owner_id, "owner id", maximum=256)
        _require_text(self.platform, "platform", maximum=64)
        _require_text(self.plan_id, "plan id", maximum=128)
        _require_text(self.passport_id, "passport id", maximum=128)
        if self.passport_version < 1:
            raise ValueError("passport version must be positive")
        state = GuidedHandoffState(self.state)
        object.__setattr__(self, "state", state)
        steps = tuple(self.steps)
        object.__setattr__(self, "steps", steps)
        if not steps:
            raise ValueError("guided handoff session requires at least one step")
        if tuple(step.ordinal for step in steps) != tuple(range(1, len(steps) + 1)):
            raise ValueError("guided handoff steps must have contiguous one-based ordinals")
        if len({step.id for step in steps}) != len(steps):
            raise ValueError("guided handoff steps require unique ids")
        expected_digest = guided_steps_sha256(
            platform=self.platform,
            plan_id=self.plan_id,
            steps=steps,
        )
        if self.steps_sha256 != expected_digest:
            raise ValueError("steps_sha256 does not match the immutable handoff steps")
        if self.revision < 1:
            raise ValueError("guided handoff revision must be positive")
        _aware(self.created_at, "session created_at")
        _aware(self.updated_at, "session updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("session updated_at cannot precede created_at")
        for field_name in (
            "consented_at",
            "handoff_started_at",
            "user_resolved_at",
            "finalized_at",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _aware(value, field_name)
                if value < self.created_at or value > self.updated_at:
                    raise ValueError(f"{field_name} must fall within the session lifecycle")
        if self.consent_reference is not None:
            _require_text(self.consent_reference, "consent reference", maximum=128)
        resolved = tuple(step for step in steps if step.resolution is not None)
        for step in resolved:
            resolved_at = step.resolved_at
            if self.handoff_started_at is None or resolved_at is None:
                raise ValueError("step resolution requires a handoff start and timestamp")
            if resolved_at < self.handoff_started_at:
                raise ValueError("step resolution cannot precede handoff start")
            if resolved_at > self.updated_at:
                raise ValueError("step resolution cannot follow session updated_at")

        if (
            self.consented_at is not None
            and self.handoff_started_at is not None
            and self.handoff_started_at < self.consented_at
        ):
            raise ValueError("handoff start cannot precede consent")
        if (
            self.user_resolved_at is not None
            and self.handoff_started_at is not None
            and self.user_resolved_at < self.handoff_started_at
        ):
            raise ValueError("user resolution cannot precede handoff start")
        if self.user_resolved_at is not None and resolved:
            latest_resolution = max(
                step.resolved_at for step in resolved if step.resolved_at is not None
            )
            if latest_resolution != self.user_resolved_at:
                raise ValueError("user_resolved_at must equal the final step resolution time")
        if (
            self.finalized_at is not None
            and self.user_resolved_at is not None
            and self.finalized_at < self.user_resolved_at
        ):
            raise ValueError("finalization cannot precede user resolution")

        has_consent = self.consent_reference is not None and self.consented_at is not None
        all_resolved = len(resolved) == len(steps)
        if state is GuidedHandoffState.PREVIEWED:
            if any(
                value is not None
                for value in (
                    self.consent_reference,
                    self.consented_at,
                    self.handoff_started_at,
                    self.user_resolved_at,
                    self.finalized_at,
                    self.receipt,
                )
            ) or resolved:
                raise ValueError("previewed handoff cannot contain later lifecycle state")
        elif state is GuidedHandoffState.CONSENTED:
            if not has_consent or any(
                value is not None
                for value in (
                    self.handoff_started_at,
                    self.user_resolved_at,
                    self.finalized_at,
                    self.receipt,
                )
            ) or resolved:
                raise ValueError("consented handoff has inconsistent lifecycle state")
        elif state is GuidedHandoffState.AWAITING_HANDOFF:
            if (
                not has_consent
                or self.handoff_started_at is None
                or self.user_resolved_at is not None
                or self.finalized_at is not None
                or self.receipt is not None
                or all_resolved
            ):
                raise ValueError("awaiting handoff has inconsistent lifecycle state")
        elif state is GuidedHandoffState.USER_RESOLVED:
            if (
                not has_consent
                or self.handoff_started_at is None
                or self.user_resolved_at is None
                or self.finalized_at is not None
                or self.receipt is not None
                or not all_resolved
            ):
                raise ValueError("user-resolved handoff has inconsistent lifecycle state")
        elif (
            not has_consent
            or self.handoff_started_at is None
            or self.user_resolved_at is None
            or self.finalized_at is None
            or self.receipt is None
            or not all_resolved
        ):
            raise ValueError("finalized handoff has inconsistent lifecycle state")
        if self.receipt is not None:
            expected_outcomes = tuple(
                GuidedHandoffOutcome(
                    step_id=step.id,
                    action_type=step.action_type,
                    target=step.target,
                    resolution=step.resolution,  # type: ignore[arg-type]
                    status=step.outcome_status,  # type: ignore[arg-type]
                )
                for step in self.steps
            )
            if (
                self.receipt.id != f"guided-receipt:{self.id}"
                or self.receipt.session_id != self.id
                or self.receipt.platform != self.platform
                or self.receipt.plan_id != self.plan_id
                or self.receipt.issued_at != self.finalized_at
                or self.receipt.outcomes != expected_outcomes
            ):
                raise ValueError("guided handoff receipt is not bound to its session")

    @classmethod
    def preview(
        cls,
        *,
        session_id: str,
        owner_id: str,
        platform: str,
        plan: TranslationPlan,
        now: datetime,
    ) -> GuidedHandoffSession:
        _aware(now, "now")
        guided_actions = tuple(
            action for action in plan.actions if action.parameters.get("delivery") == "guided_handoff"
        )
        if not guided_actions:
            raise ValueError("a handoff preview requires at least one guided migration action")
        if len({action.id for action in guided_actions}) != len(guided_actions):
            raise ValueError("duplicate guided action id")
        steps = tuple(
            _step_from_action(action, ordinal=ordinal)
            for ordinal, action in enumerate(guided_actions, start=1)
        )
        platform = _require_text(platform, "platform", maximum=64)
        return cls(
            id=_require_text(session_id, "session id", maximum=128),
            owner_id=_require_text(owner_id, "owner id", maximum=256),
            platform=platform,
            plan_id=plan.id,
            passport_id=plan.passport_id,
            passport_version=plan.passport_version,
            state=GuidedHandoffState.PREVIEWED,
            steps=steps,
            steps_sha256=guided_steps_sha256(
                platform=platform,
                plan_id=plan.id,
                steps=steps,
            ),
            created_at=now,
            updated_at=now,
            revision=1,
        )

    def assert_owner(self, actor_id: str) -> None:
        if actor_id != self.owner_id:
            raise GuidedHandoffOwnerError("guided handoff belongs to another owner")

    def consent(
        self,
        *,
        actor_id: str,
        consent_reference: str,
        expected_steps_sha256: str,
        now: datetime,
    ) -> GuidedHandoffSession:
        self.assert_owner(actor_id)
        consent_reference = _require_text(
            consent_reference,
            "consent reference",
            maximum=128,
        )
        if expected_steps_sha256 != self.steps_sha256:
            raise GuidedHandoffConflict("consent steps digest does not match the preview")
        if self.state is not GuidedHandoffState.PREVIEWED:
            if self.consent_reference == consent_reference:
                return self
            raise GuidedHandoffConflict("guided handoff already has different consent")
        self._check_transition_time(now)
        return replace(
            self,
            state=GuidedHandoffState.CONSENTED,
            consent_reference=consent_reference,
            consented_at=now,
            updated_at=now,
            revision=self.revision + 1,
        )

    def begin_handoff(self, *, actor_id: str, now: datetime) -> GuidedHandoffSession:
        self.assert_owner(actor_id)
        if self.state in {
            GuidedHandoffState.AWAITING_HANDOFF,
            GuidedHandoffState.USER_RESOLVED,
            GuidedHandoffState.FINALIZED,
        }:
            return self
        if self.state is not GuidedHandoffState.CONSENTED:
            raise GuidedHandoffTransitionError("guided handoff must be consented before it begins")
        self._check_transition_time(now)
        return replace(
            self,
            state=GuidedHandoffState.AWAITING_HANDOFF,
            handoff_started_at=now,
            updated_at=now,
            revision=self.revision + 1,
        )

    def resolve_step(
        self,
        *,
        actor_id: str,
        step_id: str,
        resolution: GuidedStepResolution | str,
        now: datetime,
    ) -> GuidedHandoffSession:
        self.assert_owner(actor_id)
        step_id = _require_text(step_id, "step id", maximum=128)
        try:
            resolution = GuidedStepResolution(resolution)
        except ValueError as error:
            raise ValueError("unsupported guided handoff step resolution") from error
        index = next((index for index, step in enumerate(self.steps) if step.id == step_id), None)
        if index is None:
            raise KeyError(f"unknown guided handoff step {step_id}")
        step = self.steps[index]
        if step.resolution is not None:
            if step.resolution is resolution:
                return self
            raise GuidedHandoffConflict(f"guided handoff step {step_id} is already resolved")
        if self.state is not GuidedHandoffState.AWAITING_HANDOFF:
            raise GuidedHandoffTransitionError(
                "guided handoff must be awaiting_handoff before a step can be resolved"
            )
        self._check_transition_time(now)
        updated_steps = list(self.steps)
        updated_steps[index] = replace(step, resolution=resolution, resolved_at=now)
        all_resolved = all(item.resolution is not None for item in updated_steps)
        return replace(
            self,
            state=(
                GuidedHandoffState.USER_RESOLVED
                if all_resolved
                else GuidedHandoffState.AWAITING_HANDOFF
            ),
            steps=tuple(updated_steps),
            user_resolved_at=now if all_resolved else None,
            updated_at=now,
            revision=self.revision + 1,
        )

    def finalize(self, *, actor_id: str, now: datetime) -> GuidedHandoffSession:
        self.assert_owner(actor_id)
        if self.state is GuidedHandoffState.FINALIZED:
            return self
        if any(step.resolution is None for step in self.steps):
            raise GuidedHandoffTransitionError(
                "every handoff step must be resolved before finalization"
            )
        if self.state is not GuidedHandoffState.USER_RESOLVED:
            raise GuidedHandoffTransitionError(
                "guided handoff must be user_resolved before finalization"
            )
        self._check_transition_time(now)
        outcomes = tuple(
            GuidedHandoffOutcome(
                step_id=step.id,
                action_type=step.action_type,
                target=step.target,
                resolution=step.resolution,  # type: ignore[arg-type]
                status=step.outcome_status,  # type: ignore[arg-type]
            )
            for step in self.steps
        )
        summary = GuidedHandoffSummary(
            total_steps=len(outcomes),
            completed_by_user=sum(
                outcome.resolution is GuidedStepResolution.COMPLETED_BY_USER
                for outcome in outcomes
            ),
            skipped_by_user=sum(
                outcome.resolution is GuidedStepResolution.SKIPPED_BY_USER
                for outcome in outcomes
            ),
            control_not_found=sum(
                outcome.resolution is GuidedStepResolution.CONTROL_NOT_FOUND
                for outcome in outcomes
            ),
        )
        receipt = GuidedHandoffReceipt(
            id=f"guided-receipt:{self.id}",
            session_id=self.id,
            platform=self.platform,
            plan_id=self.plan_id,
            issued_at=now,
            outcomes=outcomes,
            summary=summary,
        )
        return replace(
            self,
            state=GuidedHandoffState.FINALIZED,
            finalized_at=now,
            receipt=receipt,
            updated_at=now,
            revision=self.revision + 1,
        )

    def _check_transition_time(self, now: datetime) -> None:
        _aware(now, "now")
        if now < self.updated_at:
            raise ValueError("transition time cannot precede the current session state")


def _step_from_action(action: ProposedAction, *, ordinal: int) -> GuidedHandoffStep:
    instruction = action.parameters.get("instruction")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError(f"guided action {action.id} requires an instruction")
    return GuidedHandoffStep(
        id=action.id,
        ordinal=ordinal,
        action_type=action.action_type,
        target=action.target,
        instruction=instruction,
    )


_SESSION_FIELDS = frozenset(
    {
        "schema",
        "id",
        "owner_id",
        "platform",
        "plan_id",
        "passport_id",
        "passport_version",
        "state",
        "steps",
        "steps_sha256",
        "created_at",
        "updated_at",
        "revision",
        "consent_reference",
        "consented_at",
        "handoff_started_at",
        "user_resolved_at",
        "finalized_at",
        "receipt",
    }
)
_STEP_FIELDS = frozenset(
    {"id", "ordinal", "action_type", "target", "instruction", "resolution", "resolved_at"}
)
_RECEIPT_FIELDS = frozenset(
    {"id", "session_id", "platform", "plan_id", "issued_at", "outcomes", "summary"}
)
_OUTCOME_FIELDS = frozenset({"step_id", "action_type", "target", "resolution", "status"})
_SUMMARY_FIELDS = frozenset(
    {
        "total_steps",
        "completed_by_user",
        "skipped_by_user",
        "control_not_found",
        "api_writes",
        "recommendation_outcomes_verified",
        "platform_verified",
    }
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def guided_handoff_to_record(session: GuidedHandoffSession) -> dict[str, object]:
    receipt: dict[str, object] | None = None
    if session.receipt is not None:
        receipt = {
            "id": session.receipt.id,
            "session_id": session.receipt.session_id,
            "platform": session.receipt.platform,
            "plan_id": session.receipt.plan_id,
            "issued_at": session.receipt.issued_at.isoformat(),
            "outcomes": [
                {
                    "step_id": outcome.step_id,
                    "action_type": outcome.action_type.value,
                    "target": outcome.target,
                    "resolution": outcome.resolution.value,
                    "status": outcome.status.value,
                }
                for outcome in session.receipt.outcomes
            ],
            "summary": {
                "total_steps": session.receipt.summary.total_steps,
                "completed_by_user": session.receipt.summary.completed_by_user,
                "skipped_by_user": session.receipt.summary.skipped_by_user,
                "control_not_found": session.receipt.summary.control_not_found,
                "api_writes": 0,
                "recommendation_outcomes_verified": 0,
                "platform_verified": False,
            },
        }
    return {
        "schema": "feed-passport/guided-handoff/v1",
        "id": session.id,
        "owner_id": session.owner_id,
        "platform": session.platform,
        "plan_id": session.plan_id,
        "passport_id": session.passport_id,
        "passport_version": session.passport_version,
        "state": session.state.value,
        "steps": [
            {
                **_step_definition(step),
                "resolution": step.resolution.value if step.resolution is not None else None,
                "resolved_at": _iso(step.resolved_at),
            }
            for step in session.steps
        ],
        "steps_sha256": session.steps_sha256,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "revision": session.revision,
        "consent_reference": session.consent_reference,
        "consented_at": _iso(session.consented_at),
        "handoff_started_at": _iso(session.handoff_started_at),
        "user_resolved_at": _iso(session.user_resolved_at),
        "finalized_at": _iso(session.finalized_at),
        "receipt": receipt,
    }


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


def _exact_fields(value: Mapping[str, Any], expected: frozenset[str], field_name: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        unexpected = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise ValueError(
            f"{field_name} has unexpected fields {unexpected} or missing fields {missing}"
        )


def _datetime(value: object, field_name: str, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field_name} must be an ISO timestamp") from error
    _aware(parsed, field_name)
    return parsed


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def guided_handoff_from_record(record: Mapping[str, Any]) -> GuidedHandoffSession:
    record = _mapping(record, "guided handoff record")
    _exact_fields(record, _SESSION_FIELDS, "guided handoff record")
    if record["schema"] != "feed-passport/guided-handoff/v1":
        raise ValueError("unsupported guided handoff record schema")
    raw_steps = record["steps"]
    if not isinstance(raw_steps, list):
        raise ValueError("guided handoff steps must be an array")
    steps: list[GuidedHandoffStep] = []
    for index, raw_step in enumerate(raw_steps):
        item = _mapping(raw_step, f"steps[{index}]")
        _exact_fields(item, _STEP_FIELDS, f"steps[{index}]")
        steps.append(
            GuidedHandoffStep(
                id=item["id"],
                ordinal=_integer(item["ordinal"], f"steps[{index}].ordinal"),
                action_type=item["action_type"],
                target=item["target"],
                instruction=item["instruction"],
                resolution=item["resolution"],
                resolved_at=_datetime(
                    item["resolved_at"],
                    f"steps[{index}].resolved_at",
                    optional=True,
                ),
            )
        )

    receipt: GuidedHandoffReceipt | None = None
    if record["receipt"] is not None:
        raw_receipt = _mapping(record["receipt"], "receipt")
        _exact_fields(raw_receipt, _RECEIPT_FIELDS, "receipt")
        raw_outcomes = raw_receipt["outcomes"]
        if not isinstance(raw_outcomes, list):
            raise ValueError("receipt outcomes must be an array")
        outcomes: list[GuidedHandoffOutcome] = []
        for index, raw_outcome in enumerate(raw_outcomes):
            item = _mapping(raw_outcome, f"receipt.outcomes[{index}]")
            _exact_fields(item, _OUTCOME_FIELDS, f"receipt.outcomes[{index}]")
            outcomes.append(
                GuidedHandoffOutcome(
                    step_id=item["step_id"],
                    action_type=item["action_type"],
                    target=item["target"],
                    resolution=item["resolution"],
                    status=item["status"],
                )
            )
        raw_summary = _mapping(raw_receipt["summary"], "receipt.summary")
        _exact_fields(raw_summary, _SUMMARY_FIELDS, "receipt.summary")
        if (
            _integer(raw_summary["api_writes"], "summary.api_writes") != 0
            or _integer(
                raw_summary["recommendation_outcomes_verified"],
                "summary.recommendation_outcomes_verified",
            )
            != 0
            or raw_summary["platform_verified"] is not False
        ):
            raise ValueError("guided handoff summary cannot claim API or platform verification")
        receipt = GuidedHandoffReceipt(
            id=raw_receipt["id"],
            session_id=raw_receipt["session_id"],
            platform=raw_receipt["platform"],
            plan_id=raw_receipt["plan_id"],
            issued_at=_datetime(raw_receipt["issued_at"], "receipt.issued_at"),  # type: ignore[arg-type]
            outcomes=tuple(outcomes),
            summary=GuidedHandoffSummary(
                total_steps=_integer(raw_summary["total_steps"], "summary.total_steps"),
                completed_by_user=_integer(
                    raw_summary["completed_by_user"],
                    "summary.completed_by_user",
                ),
                skipped_by_user=_integer(
                    raw_summary["skipped_by_user"],
                    "summary.skipped_by_user",
                ),
                control_not_found=_integer(
                    raw_summary["control_not_found"],
                    "summary.control_not_found",
                ),
            ),
        )

    return GuidedHandoffSession(
        id=record["id"],
        owner_id=record["owner_id"],
        platform=record["platform"],
        plan_id=record["plan_id"],
        passport_id=record["passport_id"],
        passport_version=_integer(record["passport_version"], "passport_version"),
        state=record["state"],
        steps=tuple(steps),
        steps_sha256=record["steps_sha256"],
        created_at=_datetime(record["created_at"], "created_at"),  # type: ignore[arg-type]
        updated_at=_datetime(record["updated_at"], "updated_at"),  # type: ignore[arg-type]
        revision=_integer(record["revision"], "revision"),
        consent_reference=record["consent_reference"],
        consented_at=_datetime(record["consented_at"], "consented_at", optional=True),
        handoff_started_at=_datetime(
            record["handoff_started_at"],
            "handoff_started_at",
            optional=True,
        ),
        user_resolved_at=_datetime(
            record["user_resolved_at"],
            "user_resolved_at",
            optional=True,
        ),
        finalized_at=_datetime(record["finalized_at"], "finalized_at", optional=True),
        receipt=receipt,
    )


__all__ = [
    "GuidedHandoffConflict",
    "GuidedHandoffError",
    "GuidedHandoffOutcome",
    "GuidedHandoffOwnerError",
    "GuidedHandoffReceipt",
    "GuidedHandoffSession",
    "GuidedHandoffState",
    "GuidedHandoffStep",
    "GuidedHandoffSummary",
    "GuidedHandoffTransitionError",
    "GuidedStepResolution",
    "guided_handoff_from_record",
    "guided_handoff_to_record",
    "guided_steps_sha256",
]
