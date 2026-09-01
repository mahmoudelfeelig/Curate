from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from strands.models import BedrockModel, Model
from strands.models.llamacpp import LlamaCppModel


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_AWS_REGION = re.compile(r"^[a-z]{2}(?:-gov)?-[a-z0-9-]+-\d$")

ModelEndpointScope = Literal[
    "loopback_only",
    "scripted_no_network",
    "aws_bedrock",
]


@dataclass(frozen=True, slots=True)
class ModelExecutionProfile:
    """Evidence contract for where one Strands planner invocation executes."""

    provider: str
    model_id: str
    endpoint_scope: ModelEndpointScope
    external_model_calls: bool
    paid_model_calls: bool

    def __post_init__(self) -> None:
        provider = self.provider.strip()
        model_id = self.model_id.strip()
        if not provider or len(provider) > 80:
            raise ValueError("provider must contain between one and 80 characters")
        if not model_id or len(model_id) > 256:
            raise ValueError("model_id must contain between one and 256 characters")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "model_id", model_id)

        is_external = self.endpoint_scope == "aws_bedrock"
        if is_external != self.external_model_calls:
            raise ValueError(
                "aws_bedrock evidence must declare external calls, and local evidence must not"
            )
        if self.paid_model_calls != is_external:
            raise ValueError(
                "aws_bedrock calls are potentially billable; local calls must not be marked paid"
            )

    @classmethod
    def local(
        cls,
        *,
        provider: str,
        model_id: str,
        endpoint_scope: Literal["loopback_only", "scripted_no_network"],
    ) -> ModelExecutionProfile:
        return cls(
            provider=provider,
            model_id=model_id,
            endpoint_scope=endpoint_scope,
            external_model_calls=False,
            paid_model_calls=False,
        )


class LoopbackLlamaCppModel(LlamaCppModel):
    """Strands llama.cpp provider with proxy inheritance disabled.

    The upstream provider already speaks the OpenAI-compatible llama.cpp API.
    Replacing its unopened client ensures a loopback request cannot be routed
    through an environment-configured HTTP proxy.
    """

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        model_id: str,
    ) -> None:
        super().__init__(
            base_url=base_url,
            timeout=timeout,
            model_id=model_id,
            params={
                "temperature": 0.0,
                "seed": 42,
                "max_tokens": 600,
                "cache_prompt": True,
            },
            use_native_token_count=False,
        )
        self._initial_client = self.client
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
            transport=httpx.AsyncHTTPTransport(),
            trust_env=False,
        )

    def _format_request(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        request = super()._format_request(*args, **kwargs)
        # The planner protocol is sequential by construction. Asking llama.cpp
        # for a single call per assistant turn prevents a model from batching a
        # duplicate proposal alongside the accepted submission.
        request["parallel_tool_calls"] = False
        return request

    async def aclose(self) -> None:
        await self.client.aclose()
        await self._initial_client.aclose()


@dataclass(frozen=True, slots=True)
class LocalModelProviderConfig:
    provider: str
    base_url: str
    model_id: str
    timeout_seconds: float
    configured: bool
    endpoint_scope: str
    reason: str

    @classmethod
    def from_env(cls) -> LocalModelProviderConfig:
        return cls.from_values(
            provider=os.getenv("FEED_PASSPORT_MODEL_PROVIDER", "disabled"),
            base_url=os.getenv(
                "FEED_PASSPORT_LLAMACPP_BASE_URL",
                "http://127.0.0.1:8080",
            ),
            model_id=os.getenv(
                "FEED_PASSPORT_LLAMACPP_MODEL_ID",
                "feed-passport-local-qwen3-1.7b",
            ),
            timeout_seconds=float(
                os.getenv("FEED_PASSPORT_LOCAL_MODEL_TIMEOUT_SECONDS", "120")
            ),
        )

    @classmethod
    def from_values(
        cls,
        *,
        provider: str,
        base_url: str,
        model_id: str,
        timeout_seconds: float = 120,
    ) -> LocalModelProviderConfig:
        normalized_provider = provider.strip().lower() or "disabled"
        if normalized_provider == "disabled":
            return cls(
                provider="disabled",
                base_url="",
                model_id="",
                timeout_seconds=float(timeout_seconds),
                configured=False,
                endpoint_scope="none",
                reason=(
                    "No local model provider is configured. Feed Passport will not fall through "
                    "to Bedrock or any paid/external model."
                ),
            )
        if normalized_provider != "llamacpp":
            raise ValueError(
                "FEED_PASSPORT_MODEL_PROVIDER must be 'disabled' or the local-only 'llamacpp'"
            )
        if not model_id.strip():
            raise ValueError("the local llama.cpp model id is required")
        timeout = float(timeout_seconds)
        if not 1 <= timeout <= 300:
            raise ValueError("the local model timeout must be between 1 and 300 seconds")

        normalized_url = cls._validate_loopback_url(base_url)
        return cls(
            provider="llamacpp",
            base_url=normalized_url,
            model_id=model_id.strip(),
            timeout_seconds=timeout,
            configured=True,
            endpoint_scope="loopback_only",
            reason="Explicit local llama.cpp provider configured; external model calls are disabled.",
        )

    @staticmethod
    def _validate_loopback_url(value: str) -> str:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme != "http"
            or parsed.hostname not in _LOOPBACK_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError(
                "the llama.cpp provider URL must be plain HTTP on 127.0.0.1, localhost, or ::1"
            )
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("the llama.cpp provider URL contains an invalid port") from exc
        return value.strip().rstrip("/")

    def create_model(self) -> Model:
        if not self.configured:
            raise RuntimeError("the local model provider is disabled")
        return LoopbackLlamaCppModel(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            model_id=self.model_id,
        )

    @property
    def execution_profile(self) -> ModelExecutionProfile:
        if not self.configured:
            raise RuntimeError("the local model provider is disabled")
        return ModelExecutionProfile.local(
            provider=self.provider,
            model_id=self.model_id,
            endpoint_scope="loopback_only",
        )

    def summary(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "provider": self.provider,
            "model_id": self.model_id or None,
            "endpoint_scope": self.endpoint_scope,
            "mode": "local_only",
            "external_model_calls": False,
            "paid_model_calls": False,
            "reason": self.reason,
        }

    async def status(self, *, probe: bool = False) -> dict[str, Any]:
        value = self.summary()
        value["online"] = False
        if not self.configured or not probe:
            value["readiness"] = "disabled" if not self.configured else "configured_unprobed"
            return value

        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(1.5),
                transport=httpx.AsyncHTTPTransport(),
                trust_env=False,
            ) as client:
                response = await client.get("/health")
                response.raise_for_status()
        except (httpx.HTTPError, OSError):
            value["readiness"] = "offline"
            value["reason"] = "The configured loopback llama.cpp server is not responding."
            return value

        value["online"] = True
        value["readiness"] = "ready"
        value["reason"] = "The configured loopback llama.cpp server is responding."
        return value


@dataclass(frozen=True, slots=True)
class AgentCoreBedrockModelConfig:
    """Explicit, fail-closed Bedrock provider for the AgentCore Runtime only.

    This configuration intentionally has no default model or region. Constructing
    it does not make an AWS call; ``create_model`` creates the Strands Bedrock
    client and any subsequent planner invocation is external and potentially
    billable even when credits happen to cover it.
    """

    model_id: str
    region_name: str
    timeout_seconds: float
    endpoint_scope: Literal["aws_bedrock"] = "aws_bedrock"

    @classmethod
    def from_env(cls) -> AgentCoreBedrockModelConfig:
        model_id = os.getenv("FEED_PASSPORT_BEDROCK_MODEL_ID", "").strip()
        region_name = os.getenv("FEED_PASSPORT_BEDROCK_REGION", "").strip()
        missing = [
            name
            for name, value in (
                ("FEED_PASSPORT_BEDROCK_MODEL_ID", model_id),
                ("FEED_PASSPORT_BEDROCK_REGION", region_name),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "AgentCore Bedrock planning is disabled until these explicit settings exist: "
                + ", ".join(missing)
            )
        raw_timeout = os.getenv("FEED_PASSPORT_BEDROCK_TIMEOUT_SECONDS", "60")
        try:
            timeout_seconds = float(raw_timeout)
        except ValueError as exc:
            raise ValueError(
                "FEED_PASSPORT_BEDROCK_TIMEOUT_SECONDS must be a number"
            ) from exc
        return cls.from_values(
            model_id=model_id,
            region_name=region_name,
            timeout_seconds=timeout_seconds,
        )

    @classmethod
    def from_values(
        cls,
        *,
        model_id: str,
        region_name: str,
        timeout_seconds: float = 60,
    ) -> AgentCoreBedrockModelConfig:
        normalized_model_id = model_id.strip()
        normalized_region = region_name.strip()
        if not normalized_model_id or len(normalized_model_id) > 256:
            raise ValueError("an explicit Bedrock model ID of at most 256 characters is required")
        if any(character.isspace() for character in normalized_model_id):
            raise ValueError("the Bedrock model ID must not contain whitespace")
        if not _AWS_REGION.fullmatch(normalized_region):
            raise ValueError("an explicit valid AWS Bedrock region is required")
        timeout = float(timeout_seconds)
        if not 1 <= timeout <= 300:
            raise ValueError("the Bedrock timeout must be between 1 and 300 seconds")
        return cls(
            model_id=normalized_model_id,
            region_name=normalized_region,
            timeout_seconds=timeout,
        )

    @property
    def execution_profile(self) -> ModelExecutionProfile:
        return ModelExecutionProfile(
            provider="bedrock",
            model_id=self.model_id,
            endpoint_scope=self.endpoint_scope,
            external_model_calls=True,
            paid_model_calls=True,
        )

    def create_model(self) -> Model:
        return BedrockModel(
            region_name=self.region_name,
            model_id=self.model_id,
            max_tokens=600,
            temperature=0.0,
            streaming=True,
        )

    def summary(self) -> dict[str, Any]:
        profile = self.execution_profile
        return {
            "configured": True,
            "provider": profile.provider,
            "model_id": profile.model_id,
            "region": self.region_name,
            "endpoint_scope": profile.endpoint_scope,
            "mode": "agentcore_bedrock",
            "external_model_calls": profile.external_model_calls,
            "paid_model_calls": profile.paid_model_calls,
            "reason": (
                "Explicit Bedrock provider configured. Invocations are external and potentially "
                "billable; deployment scripts never invoke it without a separate apply gate."
            ),
        }
