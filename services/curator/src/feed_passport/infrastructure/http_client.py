from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx


class HttpxNoAmbientClient:
    """HTTP infrastructure with ambient proxies and credentials disabled."""

    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        if not 0 < timeout_seconds <= 120:
            raise ValueError("HTTP timeout must be between 0 and 120 seconds")
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            trust_env=False,
            follow_redirects=False,
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        data: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        return self._client.request(
            method,
            url,
            headers=dict(headers or {}),
            params=dict(params) if params is not None else None,
            json=json,
            data=dict(data or {}) if data is not None else None,
        )

    def close(self) -> None:
        self._client.close()
