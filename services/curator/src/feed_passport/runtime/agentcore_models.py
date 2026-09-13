from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
)

from feed_passport.agent.feed_goal_planner import SanitizedEvidenceItem
from feed_passport.domain import FeedPassport


BoundedId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=160),
]
BoundedLabel = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=160),
]


class StrictRuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, validate_default=True)


class HealthRequest(StrictRuntimeModel):
    kind: Literal["health"] = "health"


class PassportSnapshot(StrictRuntimeModel):
    """Versioned preference policy supplied to the stateless proposal boundary."""

    id: BoundedId
    owner_id: BoundedId
    name: BoundedLabel
    version: int = Field(strict=True, ge=1, le=2_147_483_647)
    intent: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=1200),
    ]
    topic_targets: dict[str, float] = Field(min_length=1, max_length=50)
    creator_preferences: dict[str, float] = Field(default_factory=dict, max_length=100)
    format_preferences: dict[str, float] = Field(default_factory=dict, max_length=50)
    languages: list[Annotated[str, StringConstraints(min_length=1, max_length=24)]] = Field(
        default_factory=lambda: ["en"],
        min_length=1,
        max_length=12,
    )
    hard_exclusions: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    ] = Field(default_factory=list, max_length=50)
    serendipity: float = Field(default=0.2, ge=0, le=1)
    max_outrage: float = Field(default=0.05, ge=0, le=1)
    max_source_share: float = Field(default=0.25, gt=0, le=1)

    @field_validator("topic_targets", "creator_preferences", "format_preferences")
    @classmethod
    def validate_preference_map(
        cls,
        value: dict[str, float],
        info: Any,
    ) -> dict[str, float]:
        for key, number in value.items():
            if not key.strip() or len(key) > 120 or "\0" in key:
                raise ValueError(f"{info.field_name} contains an invalid key")
            numeric = float(number)
            if not math.isfinite(numeric):
                raise ValueError(f"{info.field_name} values must be finite")
            if info.field_name == "topic_targets" and numeric <= 0:
                raise ValueError("topic target weights must be positive")
            if info.field_name != "topic_targets" and not -1 <= numeric <= 1:
                raise ValueError(f"{info.field_name} values must be between -1 and 1")
        return value

    @field_validator("languages", "hard_exclusions")
    @classmethod
    def require_unique_values(cls, value: list[str], info: Any) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError(f"{info.field_name} must not contain duplicates")
        return value

    def to_domain(self) -> FeedPassport:
        now = datetime.now(timezone.utc)
        return FeedPassport(
            id=self.id,
            owner_id=self.owner_id,
            name=self.name,
            version=self.version,
            intent=self.intent,
            topic_targets=self.topic_targets,
            creator_preferences=self.creator_preferences,
            format_preferences=self.format_preferences,
            languages=tuple(self.languages),
            hard_exclusions=frozenset(self.hard_exclusions),
            serendipity=self.serendipity,
            max_outrage=self.max_outrage,
            max_source_share=self.max_source_share,
            created_at=now,
            updated_at=now,
        )


class PlanFeatureRequest(StrictRuntimeModel):
    kind: Literal["plan_feature"]
    passport: PassportSnapshot
    request: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=1200),
    ]


class PlanFeedRequest(StrictRuntimeModel):
    kind: Literal["plan_feed"]
    passport: PassportSnapshot
    request: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=1200),
    ]
    evidence: list[SanitizedEvidenceItem] = Field(min_length=1, max_length=12)


AgentCoreRequest = Annotated[
    HealthRequest | PlanFeatureRequest | PlanFeedRequest,
    Field(discriminator="kind"),
]
AGENTCORE_REQUEST_ADAPTER = TypeAdapter(AgentCoreRequest)


class ActorIdentityResolver(Protocol):
    def resolve(self, context: Any) -> str: ...


class RuntimeGatewaySubjectResolver:
    """Reads the subject injected by the authenticated Gateway interceptor.

    The Runtime's IAM resource policy permits only the Gateway role. The Gateway
    validates the Cognito JWT and invoke scope before its interceptor overwrites
    this header, so a client-supplied value cannot reach this resolver unchanged.
    """

    HEADER_NAME = "x-feed-passport-actor"

    def resolve(self, context: Any) -> str:
        headers = getattr(context, "request_headers", None)
        if not isinstance(headers, Mapping):
            raise PermissionError("AgentCore Gateway identity context is required")
        normalized = {str(key).lower(): str(value) for key, value in headers.items()}
        subject = normalized.get(self.HEADER_NAME)
        if (
            not isinstance(subject, str)
            or not subject.strip()
            or len(subject.strip()) > 160
            or "\0" in subject
        ):
            raise PermissionError("the Gateway-validated request has no usable subject")
        return subject.strip()
