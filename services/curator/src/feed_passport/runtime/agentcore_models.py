from __future__ import annotations

import base64
import binascii
import json
import math
import os
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


AgentCoreRequest = Annotated[
    HealthRequest | PlanFeatureRequest,
    Field(discriminator="kind"),
]
AGENTCORE_REQUEST_ADAPTER = TypeAdapter(AgentCoreRequest)


class ActorIdentityResolver(Protocol):
    def resolve(self, context: Any) -> str: ...


class RuntimeJwtSubjectResolver:
    """Reads ``sub`` only after AgentCore Runtime's CUSTOM_JWT authorizer.

    Signature, issuer, audience, expiry, and scope verification belong to the
    Runtime authorizer configured in CDK. This resolver deliberately performs no
    local signature verification; it requires the exact configured issuer as a
    second fail-closed binding and must not be used on an unauthenticated server.
    Local tests inject a synthetic RequestContext and explicit expected issuer.
    """

    def __init__(self, *, expected_issuer: str | None = None) -> None:
        self._expected_issuer = expected_issuer

    def resolve(self, context: Any) -> str:
        headers = getattr(context, "request_headers", None)
        if not isinstance(headers, Mapping):
            raise PermissionError("AgentCore Runtime JWT context is required")
        normalized = {str(key).lower(): str(value) for key, value in headers.items()}
        authorization = normalized.get("authorization", "")
        scheme, separator, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not token or " " in token:
            raise PermissionError("a Runtime-validated bearer token is required")

        expected_issuer = (
            self._expected_issuer
            or os.getenv("FEED_PASSPORT_JWT_ISSUER", "").strip()
        )
        if not expected_issuer:
            raise RuntimeError(
                "FEED_PASSPORT_JWT_ISSUER is required before authenticated planning"
            )
        claims = self._decode_runtime_validated_claims(token)
        if claims.get("iss") != expected_issuer:
            raise PermissionError("the Runtime-validated token issuer does not match this deployment")
        subject = claims.get("sub")
        if (
            not isinstance(subject, str)
            or not subject.strip()
            or len(subject.strip()) > 160
            or "\0" in subject
        ):
            raise PermissionError("the Runtime-validated token has no usable subject")
        return subject.strip()

    @staticmethod
    def _decode_runtime_validated_claims(token: str) -> dict[str, Any]:
        if len(token) > 16_384:
            raise PermissionError("the bearer token exceeds the accepted size")
        parts = token.split(".")
        if len(parts) != 3:
            raise PermissionError("the bearer token is not a compact JWT")
        segment = parts[1]
        padding = "=" * (-len(segment) % 4)
        try:
            raw = base64.b64decode(
                segment + padding,
                altchars=b"-_",
                validate=True,
            )
            claims = json.loads(raw.decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PermissionError("the Runtime-validated JWT claims are malformed") from exc
        if not isinstance(claims, dict):
            raise PermissionError("the Runtime-validated JWT claims must be an object")
        return claims
