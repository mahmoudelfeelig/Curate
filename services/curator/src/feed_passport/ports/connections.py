from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping, Protocol

from feed_passport.domain.connections import (
    ConnectionStatus,
    ExternalConnection,
    OAuthTransaction,
    OAuthTransactionStart,
)


class ExternalConnectionRepository(Protocol):
    def register_connection(
        self,
        *,
        owner_id: str,
        platform: str,
        external_subject: str,
        credential_ref: str,
        metadata: Mapping[str, Any],
        now: datetime,
        status: ConnectionStatus = ConnectionStatus.ACTIVE,
    ) -> ExternalConnection: ...

    def get_connection(self, connection_id: str, *, owner_id: str) -> ExternalConnection: ...

    def list_connections(
        self,
        *,
        owner_id: str,
        platform: str | None = None,
    ) -> tuple[ExternalConnection, ...]: ...

    def list_runtime_connections(self, *, platform: str) -> tuple[ExternalConnection, ...]: ...

    def update_connection(
        self,
        connection_id: str,
        *,
        owner_id: str,
        expected_version: int,
        now: datetime,
        status: ConnectionStatus | None = None,
        credential_ref: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        expected_external_subject: str | None = None,
    ) -> ExternalConnection: ...

    def revoke_connection(
        self,
        connection_id: str,
        *,
        owner_id: str,
        expected_version: int,
        now: datetime,
    ) -> ExternalConnection: ...

    def begin_oauth_transaction(
        self,
        *,
        owner_id: str,
        platform: str,
        redirect_uri: str,
        metadata: Mapping[str, Any],
        now: datetime,
        ttl: timedelta = timedelta(minutes=10),
    ) -> OAuthTransactionStart: ...

    def consume_oauth_transaction(
        self,
        state: str,
        *,
        owner_id: str,
        platform: str,
        now: datetime,
    ) -> OAuthTransaction: ...
