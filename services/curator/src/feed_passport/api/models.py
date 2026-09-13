from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PassportCreate(StrictModel):
    owner_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    intent: str = Field(min_length=1, max_length=1200)
    topic_targets: dict[str, float]
    creator_preferences: dict[str, float] = Field(default_factory=dict)
    format_preferences: dict[str, float] = Field(default_factory=dict)
    languages: list[str] = Field(default_factory=lambda: ["en"])
    hard_exclusions: list[str] = Field(default_factory=list)
    serendipity: float = Field(default=0.2, ge=0, le=1)
    max_outrage: float = Field(default=0.05, ge=0, le=1)
    max_source_share: float = Field(default=0.25, gt=0, le=1)


class PassportRevision(StrictModel):
    actor_id: str = Field(min_length=1)
    changes: dict[str, Any]


class InstagramImportApply(StrictModel):
    actor_id: str = Field(min_length=1, max_length=160)
    passport_id: str = Field(min_length=1, max_length=160)
    expected_passport_version: int = Field(ge=1)
    selected_handles: list[str] = Field(min_length=1, max_length=500)

    @field_validator("selected_handles")
    @classmethod
    def require_unique_handles(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("selected Instagram handles must be unique")
        return value


class PassportImport(StrictModel):
    actor_id: str = Field(min_length=1)
    format: str = Field(pattern=r"^feed-passport/v1$")
    passport: dict[str, Any]


class AccountCapture(StrictModel):
    platform: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=160)
    intent: str = Field(min_length=1, max_length=1200)


class CheckpointCreate(StrictModel):
    actor_id: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=160)


class OverlayCreate(StrictModel):
    actor_id: str = Field(min_length=1)
    passport_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    topic_adjustments: dict[str, float]
    add_exclusions: list[str] = Field(default_factory=list)
    remove_exclusions: list[str] = Field(default_factory=list)
    starts_at: datetime
    expires_at: datetime
    mode: str = "isolated"
    serendipity: float | None = Field(default=None, ge=0, le=1)
    max_outrage: float | None = Field(default=None, ge=0, le=1)


class TemplateOverlayCreate(StrictModel):
    actor_id: str = Field(min_length=1)
    passport_id: str = Field(min_length=1)
    starts_at: datetime | None = None


class ShareCreate(StrictModel):
    actor_id: str = Field(min_length=1)
    passport_id: str = Field(min_length=1)
    topic_names: list[str]
    creator_ids: list[str] = Field(default_factory=list)
    include_serendipity: bool = False
    include_formats: bool = False
    include_exclusions: bool = False
    expires_at: datetime
    scope: Literal["snapshot", "continuous"] = "snapshot"
    refresh_on_revision: bool = False
    target_passport_ids: list[str] = Field(default_factory=list, max_length=20)
    pair_id: str | None = Field(default=None, min_length=3, max_length=160)
    counterparty_owner_id: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_scope(self) -> ShareCreate:
        if self.scope == "continuous":
            if not self.refresh_on_revision:
                raise ValueError("continuous consent must explicitly refresh on revision")
            if not self.target_passport_ids:
                raise ValueError("continuous consent must opt in at least one target Passport")
            if not self.pair_id or not self.counterparty_owner_id:
                raise ValueError("continuous consent must bind a pair and counterparty")
            if self.counterparty_owner_id == self.actor_id:
                raise ValueError("continuous consent counterparty must be a different principal")
        elif (
            self.refresh_on_revision
            or self.target_passport_ids
            or self.pair_id is not None
            or self.counterparty_owner_id is not None
        ):
            raise ValueError("snapshot consent cannot bind continuous sync fields")
        return self


class ActorRequest(StrictModel):
    actor_id: str = Field(min_length=1)


class OAuthStart(StrictModel):
    actor_id: str = Field(min_length=1, max_length=120)
    redirect_uri: str | None = Field(default=None, min_length=10, max_length=2048)
    handle: str | None = Field(default=None, min_length=3, max_length=254)

    @model_validator(mode="after")
    def validate_authorization_input(self) -> OAuthStart:
        if (self.redirect_uri is None) == (self.handle is None):
            raise ValueError("provide exactly one of redirect_uri or handle")
        return self


class OAuthCallback(StrictModel):
    actor_id: str = Field(min_length=1, max_length=120)
    state: str | None = Field(default=None, min_length=32, max_length=512)
    code: str | None = Field(default=None, min_length=1, max_length=4096)
    query: str | None = Field(default=None, min_length=1, max_length=16_384)

    @model_validator(mode="after")
    def validate_callback_input(self) -> OAuthCallback:
        if self.query is not None:
            if self.state is not None or self.code is not None:
                raise ValueError("AT Protocol callback query cannot be combined with state or code")
            return self
        if self.state is None or self.code is None:
            raise ValueError("OAuth callback requires state and code")
        return self


class ConnectionRevoke(StrictModel):
    actor_id: str = Field(min_length=1, max_length=120)
    expected_version: int = Field(ge=1)


class CompanionCreate(StrictModel):
    actor_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    slice_ids: list[str] = Field(min_length=2)
    weights: dict[str, float]
    strategy: str = "bridge"
    expires_at: datetime
    scope: Literal["snapshot", "continuous"] = "snapshot"

    @model_validator(mode="after")
    def validate_scope(self) -> CompanionCreate:
        if self.scope == "continuous" and len(self.slice_ids) != 2:
            raise ValueError("continuous companion sync requires exactly two consent slices")
        return self


class MigrationPrepare(StrictModel):
    actor_id: str = Field(min_length=1)
    passport_id: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    destination_account_id: str = Field(min_length=1)
    overlay_id: str | None = None


class ApprovalCreate(StrictModel):
    actor_id: str = Field(min_length=1)
    max_total_actions: int | None = Field(default=None, ge=1)
    ttl_seconds: int = Field(default=600, ge=1, le=900)


class MigrationExecute(StrictModel):
    actor_id: str = Field(min_length=1)
    approval_token: str = Field(min_length=20)


class GuidedStepResolve(StrictModel):
    actor_id: str = Field(min_length=1, max_length=160)
    resolution: Literal[
        "completed_by_user",
        "skipped_by_user",
        "control_not_found",
    ]


class RollbackApprovalCreate(StrictModel):
    actor_id: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    ttl_seconds: int = Field(default=600, ge=1, le=900)


class RollbackExecute(StrictModel):
    actor_id: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    approval_token: str = Field(min_length=20)


class AgentMissionBudgetModel(StrictModel):
    total_actions: int = Field(default=8, ge=1, le=100)
    per_iteration_actions: int = Field(default=3, ge=1, le=100)
    max_iterations: int = Field(default=3, ge=1, le=20)


class AgentMissionAcceptanceModel(StrictModel):
    max_total_variation_distance: float = Field(default=0.18, ge=0, le=1)
    max_unwanted_rate: float = Field(default=0.05, ge=0, le=1)
    max_source_concentration: float = Field(default=0.4, ge=0, le=1)
    min_serendipity_rate: float = Field(default=0.05, ge=0, le=1)
    max_serendipity_rate: float = Field(default=1.0, ge=0, le=1)


class AgentMissionAcceptanceInput(StrictModel):
    max_topic_distance: float = Field(default=0.18, ge=0, le=1)
    max_unwanted_rate: float = Field(default=0.05, ge=0, le=1)
    max_source_concentration: float = Field(default=0.4, ge=0, le=1)
    min_serendipity: float = Field(default=0.05, ge=0, le=1)
    max_serendipity: float = Field(default=1.0, ge=0, le=1)


class AgentMissionPreview(StrictModel):
    actor_id: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=1200)
    passport_id: str = Field(min_length=1)
    destination_twin: str | None = Field(
        default=None,
        pattern=r"^twin:[a-z0-9][a-z0-9_-]*$",
    )
    platform: str | None = Field(default=None, pattern=r"^twin:[a-z0-9][a-z0-9_-]*$")
    destination_account_id: str | None = Field(default=None, min_length=1, max_length=160)
    account_id: str | None = Field(default=None, min_length=1, max_length=160)
    budget: AgentMissionBudgetModel | None = None
    max_iterations: int = Field(default=3, ge=1, le=20)
    max_total_actions: int = Field(default=8, ge=1, le=100)
    max_actions_per_iteration: int = Field(default=3, ge=1, le=100)
    acceptance_thresholds: AgentMissionAcceptanceModel | None = None
    acceptance: AgentMissionAcceptanceInput | None = None
    allowed_actions: list[str] = Field(default_factory=list)
    min_improvement: float = Field(default=0.01, ge=0, le=1)

    @model_validator(mode="after")
    def validate_mission_envelope(self) -> AgentMissionPreview:
        if not self.destination_twin and not self.platform:
            raise ValueError("destination_twin or platform is required")
        if self.destination_twin and self.platform and self.destination_twin != self.platform:
            raise ValueError("destination_twin and platform must identify the same local twin")
        if not self.destination_account_id and not self.account_id:
            raise ValueError("destination_account_id or account_id is required")
        if (
            self.destination_account_id
            and self.account_id
            and self.destination_account_id != self.account_id
        ):
            raise ValueError("destination_account_id and account_id must identify the same fixture")
        budget = self.resolved_budget()
        if budget.per_iteration_actions > budget.total_actions:
            raise ValueError("max actions per iteration cannot exceed the total mission budget")
        acceptance = self.resolved_acceptance()
        if acceptance.min_serendipity_rate > acceptance.max_serendipity_rate:
            raise ValueError("minimum serendipity cannot exceed maximum serendipity")
        return self

    def resolved_destination_twin(self) -> str:
        return str(self.destination_twin or self.platform)

    def resolved_account_id(self) -> str:
        return str(self.destination_account_id or self.account_id)

    def resolved_budget(self) -> AgentMissionBudgetModel:
        return self.budget or AgentMissionBudgetModel(
            total_actions=self.max_total_actions,
            per_iteration_actions=self.max_actions_per_iteration,
            max_iterations=self.max_iterations,
        )

    def resolved_acceptance(self) -> AgentMissionAcceptanceModel:
        if self.acceptance_thresholds is not None:
            return self.acceptance_thresholds
        value = self.acceptance or AgentMissionAcceptanceInput()
        return AgentMissionAcceptanceModel(
            max_total_variation_distance=value.max_topic_distance,
            max_unwanted_rate=value.max_unwanted_rate,
            max_source_concentration=value.max_source_concentration,
            min_serendipity_rate=value.min_serendipity,
            max_serendipity_rate=value.max_serendipity,
        )


class AgentMissionApprovalCreate(StrictModel):
    actor_id: str = Field(min_length=1, max_length=120)
    ttl_seconds: int = Field(default=600, ge=1, le=900)


class AgentMissionExecute(StrictModel):
    actor_id: str = Field(min_length=1, max_length=120)
    approval_token: str = Field(min_length=20)


class AgentLiveCommissionPreview(StrictModel):
    actor_id: str = Field(min_length=1, max_length=120)
    priority_mode: Literal[
        "balanced",
        "protective_controls_first",
        "creator_continuity_first",
    ] = "balanced"
    passport_id: str = Field(min_length=1, max_length=160)
    platform: Literal["youtube", "bluesky"]
    destination_connection_id: str = Field(min_length=1, max_length=160)
    max_total_actions: int = Field(default=8, ge=1, le=20)


class AgentLiveCommissionApprovalCreate(StrictModel):
    actor_id: str = Field(min_length=1, max_length=120)
    ttl_seconds: int = Field(default=600, ge=1, le=900)


class AgentLiveCommissionExecute(StrictModel):
    actor_id: str = Field(min_length=1, max_length=120)
    approval_token: str = Field(min_length=20)


class FeatureIntentPlan(StrictModel):
    actor_id: str = Field(min_length=1, max_length=120)
    passport_id: str = Field(min_length=1, max_length=160)
    request: str = Field(min_length=1, max_length=1200)


class FeedEvidenceLinkInput(StrictModel):
    url: str = Field(min_length=12, max_length=2048)
    note: str = Field(default="", max_length=600)


class FeedEvidenceAnalyze(StrictModel):
    actor_id: str = Field(min_length=1, max_length=120)
    passport_id: str = Field(min_length=1, max_length=160)
    goal: str = Field(min_length=1, max_length=1200)
    stage: Literal["before", "after"] = "before"
    links: list[FeedEvidenceLinkInput] = Field(min_length=1, max_length=12)
    youtube_connection_id: str | None = Field(default=None, min_length=1, max_length=160)
    baseline_snapshot_id: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def require_baseline_only_for_after(self) -> FeedEvidenceAnalyze:
        if self.stage == "before" and self.baseline_snapshot_id is not None:
            raise ValueError("a baseline snapshot is valid only for after evidence")
        if self.stage == "after" and self.baseline_snapshot_id is None:
            raise ValueError("after evidence requires a baseline snapshot")
        return self


class FeedEvidenceApply(StrictModel):
    actor_id: str = Field(min_length=1, max_length=120)
    expected_passport_version: int = Field(ge=1)


class FeedEvidenceAgentPlan(StrictModel):
    actor_id: str = Field(min_length=1, max_length=120)
    expected_passport_version: int = Field(ge=1)


class DriftRequest(StrictModel):
    actor_id: str = Field(min_length=1)
    passport_id: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    account_id: str = Field(min_length=1)


class CreatorPreserve(StrictModel):
    actor_id: str = Field(min_length=1)
    passport_id: str = Field(min_length=1)
    creator_id: str = Field(min_length=1)
    destination_platform: str = Field(min_length=1)


class DriftMonitorCreate(StrictModel):
    actor_id: str = Field(min_length=1)
    passport_id: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    interval_minutes: int = Field(default=60, ge=15, le=1440)
    expires_at: datetime
    mode: str = "alert_only"
    allowed_actions: list[str] = Field(default_factory=list)
    max_actions_per_run: int = Field(default=3, ge=1, le=20)
    minimum_confidence: float = Field(default=0.8, ge=0.5, le=1)


class StrandsMessage(StrictModel):
    actor_id: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=8000)
    invocation_state: dict[str, Any] = Field(default_factory=dict)
