from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from strands.models import Model
from strands.models.llamacpp import LlamaCppModel


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


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
