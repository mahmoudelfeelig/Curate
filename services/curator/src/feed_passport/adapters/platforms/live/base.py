from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, ClassVar

import httpx

from feed_passport.adapters.platforms.base import (
    ManifestPlatformAdapter,
    UnsupportedPlatformAction,
)
from feed_passport.domain.models import (
    AccountObservation,
    ActionOutcome,
    ActionReceipt,
    ActionStatus,
    ActionType,
    AdapterHealth,
    CapabilityLevel,
    FeedSample,
    PlatformCapabilityManifest,
    ProposedAction,
    RollbackOutcome,
)
from feed_passport.ports.credentials import (
    ConnectedAccount,
    CredentialProvider,
    CredentialScopeDenied,
    OAuthCredentialLease,
)
from feed_passport.ports.live_platform import (
    HttpClient,
    LiveAuthenticationError,
    LivePermissionError,
    LivePlatformError,
    LiveProtocolError,
    LiveRateLimited,
    LiveTargetNotFound,
    LiveTransientError,
    PreparedRemoteAction,
    PUBLIC_ENGAGEMENT_ACTIONS,
    RemoteOutcomeUnknown,
    ValidatedLiveCertification,
    require_active_connection,
)


class CertifiedLivePlatformAdapter(ManifestPlatformAdapter):
    """Shared fail-closed runtime for separately certified live transports."""

    ACTION_SCOPES: ClassVar[Mapping[ActionType, frozenset[str]]] = {}
    OBSERVE_SCOPES: ClassVar[frozenset[str]] = frozenset()
    CANDIDATE_ACTIONS: ClassVar[frozenset[ActionType] | None] = None
    MAX_PAGES: ClassVar[int] = 50

    def __init__(
        self,
        *,
        connections: Mapping[str, ConnectedAccount],
        credential_provider: CredentialProvider,
        http_client: HttpClient,
        certification: ValidatedLiveCertification | None = None,
        observations: Mapping[str, AccountObservation] | None = None,
        request_user_agent: str = "feed-passport/0.1",
    ) -> None:
        super().__init__(observations=observations)
        normalized_user_agent = request_user_agent.strip()
        if not 10 <= len(normalized_user_agent) <= 256 or any(
            ord(character) < 32 or ord(character) > 126
            for character in normalized_user_agent
        ):
            raise ValueError("live transport user agents must be 10-256 printable ASCII characters")
        self._connections = dict(connections)
        self._credential_provider = credential_provider
        self._http = http_client
        self._certification = certification
        self._request_user_agent = normalized_user_agent
        for connection_id, connection in self._connections.items():
            if connection.id != connection_id:
                raise ValueError("connected account key must match its id")
            require_active_connection(connection, platform=self.platform)
        if certification is not None:
            if certification.platform != self.platform:
                raise ValueError("live certification belongs to a different platform")
            candidates = self._candidate_actions()
            if not certification.execute <= candidates:
                raise ValueError("live certification exceeds the adapter's candidate action surface")
            if certification.execute & PUBLIC_ENGAGEMENT_ACTIONS:
                raise ValueError("live adapters cannot certify public engagement")

    def bind_connection(self, connection: ConnectedAccount) -> None:
        """Bind an owner-validated, token-free connection after OAuth completes.

        The application must first resolve the opaque ID through its
        owner-scoped connection registry.  This method deliberately accepts no
        token and performs the platform/status checks again at the adapter
        boundary.
        """

        require_active_connection(connection, platform=self.platform)
        self._connections[connection.id] = connection

    def unbind_connection(self, connection_id: str) -> None:
        """Forget in-memory routing metadata after revocation."""

        self._connections.pop(connection_id, None)

    def _candidate_actions(self) -> frozenset[ActionType]:
        return self.CANDIDATE_ACTIONS or self.PROFILE.official_actions

    def _connection(self, account_id: str) -> ConnectedAccount:
        self._validate_account_id(account_id)
        try:
            connection = self._connections[account_id]
        except KeyError as exc:
            raise LiveAuthenticationError(
                platform=self.platform,
                code="connection_not_found",
                detail="No active connected account is registered for this destination.",
            ) from exc
        require_active_connection(connection, platform=self.platform)
        return connection

    def _certified_actions_for(self, connection: ConnectedAccount) -> frozenset[ActionType]:
        if self._certification is None:
            return frozenset()
        granted = frozenset(connection.granted_scopes)
        return frozenset(
            action
            for action in self._certification.execute
            if self.ACTION_SCOPES.get(action, frozenset()) <= granted
        )

    def capabilities(self, account_id: str) -> PlatformCapabilityManifest:
        connection = self._connections.get(account_id)
        if connection is None or self._certification is None or connection.status != "active":
            return super().capabilities(account_id)
        require_active_connection(connection, platform=self.platform)
        execute = self._certified_actions_for(connection)
        if not execute:
            return super().capabilities(account_id)
        observe = (
            self._certification.observe
            if self.OBSERVE_SCOPES <= frozenset(connection.granted_scopes)
            else frozenset()
        )
        rollback = self._certification.rollback & execute
        return PlatformCapabilityManifest(
            platform=self.platform,
            level=CapabilityLevel.EXECUTABLE,
            observe=observe,
            execute=execute,
            verify=self._certification.verify,
            rollback=rollback,
            requires_user_handoff=self.PROFILE.native_handoff_actions - execute,
            evidence_url=self.PROFILE.evidence_urls[0],
            certified_at=self._certification.certified_at,
        )

    def observe(self, account_id: str, *, now: datetime, sample_size: int = 24) -> AccountObservation:
        if sample_size < 1:
            raise ValueError("sample size must be positive")
        manifest = self.capabilities(account_id)
        if manifest.level is CapabilityLevel.GUIDED or not manifest.observe:
            return super().observe(account_id, now=now, sample_size=sample_size)
        return self._observe_controls(self._connection(account_id), now=now, sample_size=sample_size)

    def sample(self, account_id: str, *, now: datetime, limit: int = 24) -> FeedSample:
        if limit < 1:
            raise ValueError("sample limit must be positive")
        if self.capabilities(account_id).level is CapabilityLevel.GUIDED:
            return super().sample(account_id, now=now, limit=limit)
        return FeedSample(platform=self.platform, account_id=account_id, items=(), sampled_at=now)

    def execute(self, account_id: str, action: ProposedAction, *, now: datetime) -> ActionOutcome:
        if action.action_type not in self.capabilities(account_id).execute:
            return super().execute(account_id, action, now=now)
        prepared = self.prepare_remote_action(account_id, action, now=now)
        return self.apply_prepared_action(prepared, now=now)

    def prepare_remote_action(
        self,
        account_id: str,
        action: ProposedAction,
        *,
        now: datetime,
    ) -> PreparedRemoteAction:
        connection = self._connection(account_id)
        manifest = self.capabilities(account_id)
        if action.destination_id != account_id:
            raise ValueError("action destination does not match connected account")
        if action.action_type not in manifest.execute:
            raise UnsupportedPlatformAction(
                self.platform,
                action.action_type,
                "live_capability_unavailable",
                f"{self.platform} has no certified live transport for this action",
            )
        if action.action_type in PUBLIC_ENGAGEMENT_ACTIONS:
            raise UnsupportedPlatformAction(
                self.platform,
                action.action_type,
                "public_engagement_forbidden",
                "Public engagement cannot be used as feed-training automation.",
            )
        if action.reversible and action.action_type not in manifest.rollback:
            raise ValueError("action claims reversibility outside the certified rollback surface")
        before_state = self._read_action_state(connection, action, now=now)
        desired_state = self._desired_state(action, before_state)
        return PreparedRemoteAction(
            platform=self.platform,
            connection_id=account_id,
            action=action,
            before_state=before_state,
            desired_state=desired_state,
            prepared_at=now,
        )

    def apply_prepared_action(
        self,
        prepared: PreparedRemoteAction,
        *,
        now: datetime,
    ) -> ActionOutcome:
        self._validate_prepared(prepared)
        connection = self._connection(prepared.connection_id)
        if self._state_matches(prepared.before_state, prepared.desired_state):
            return ActionOutcome(
                action=prepared.action,
                status=ActionStatus.SKIPPED,
                before_state=prepared.before_state,
                after_state=prepared.before_state,
                executed_at=now,
                platform_reference=self._state_reference(prepared.before_state),
            )
        reference = self._mutate_to_state(
            connection,
            prepared.action,
            prepared.desired_state,
            current_state=prepared.before_state,
            now=now,
        )
        try:
            after_state = self._read_action_state(connection, prepared.action, now=now)
        except LivePlatformError as exc:
            raise RemoteOutcomeUnknown(
                platform=self.platform,
                code="post_write_verification_unavailable",
                detail="The platform write completed without a trustworthy verification result.",
                retryable=False,
                outcome_unknown=True,
                http_status=exc.http_status,
            ) from exc
        if not self._state_matches(after_state, prepared.desired_state):
            raise RemoteOutcomeUnknown(
                platform=self.platform,
                code="post_write_verification_mismatch",
                detail="The platform response could not be reconciled with the requested state.",
                retryable=False,
                outcome_unknown=True,
            )
        return ActionOutcome(
            action=prepared.action,
            status=ActionStatus.EXECUTED,
            before_state=prepared.before_state,
            after_state=after_state,
            executed_at=now,
            platform_reference=reference or self._state_reference(after_state),
        )

    def reconcile_remote_action(
        self,
        prepared: PreparedRemoteAction,
        *,
        now: datetime,
    ) -> ActionOutcome:
        self._validate_prepared(prepared)
        connection = self._connection(prepared.connection_id)
        observed = self._read_action_state(connection, prepared.action, now=now)
        if self._state_matches(observed, prepared.desired_state):
            return ActionOutcome(
                action=prepared.action,
                status=ActionStatus.EXECUTED,
                before_state=prepared.before_state,
                after_state=observed,
                executed_at=now,
                platform_reference=self._state_reference(observed),
            )
        if self._state_matches(observed, prepared.before_state):
            return ActionOutcome(
                action=prepared.action,
                status=ActionStatus.FAILED,
                before_state=prepared.before_state,
                after_state=observed,
                executed_at=now,
                platform_reference=self._state_reference(observed),
                error_code="remote_action_not_applied",
            )
        raise RemoteOutcomeUnknown(
            platform=self.platform,
            code="remote_state_ambiguous",
            detail="The remote state matches neither the recorded baseline nor the requested state.",
            outcome_unknown=True,
        )

    def rollback(self, account_id: str, receipt: ActionReceipt, *, now: datetime) -> RollbackOutcome:
        connection = self._connection(account_id)
        if receipt.destination_id != account_id:
            raise ValueError("receipt destination does not match connected account")
        manifest = self.capabilities(account_id)
        restored: list[str] = []
        failed: list[str] = []
        caveats: list[str] = []
        for outcome in reversed(receipt.outcomes):
            action = outcome.action
            if outcome.status is not ActionStatus.EXECUTED or not action.reversible:
                continue
            if action.action_type not in manifest.rollback:
                failed.append(action.id)
                continue
            try:
                current = self._read_action_state(connection, action, now=now)
                if not self._state_matches(current, outcome.before_state):
                    self._mutate_to_state(
                        connection,
                        action,
                        outcome.before_state,
                        current_state=current,
                        now=now,
                    )
                verified = self._read_action_state(connection, action, now=now)
                if self._state_matches(verified, outcome.before_state):
                    restored.append(action.id)
                else:
                    failed.append(action.id)
            except LivePlatformError as exc:
                failed.append(action.id)
                caveats.append(f"{action.id}: {exc.code}")
        return RollbackOutcome(
            receipt_id=receipt.id,
            destination_id=account_id,
            restored_actions=tuple(restored),
            failed_actions=tuple(failed),
            completed_at=now,
            caveats=tuple(caveats),
        )

    def health(self, *, now: datetime) -> AdapterHealth:
        if self._certification is None:
            return super().health(now=now)
        active = sum(connection.status == "active" for connection in self._connections.values())
        return AdapterHealth(
            platform=self.platform,
            healthy=True,
            mode="authorized_live" if active else "certified_transport_no_connection",
            checked_at=now,
            detail=(
                f"Validated live certification {self._certification.receipt_ref}; "
                f"{active} active connected account(s)."
            ),
        )

    def _validate_prepared(self, prepared: PreparedRemoteAction) -> None:
        if prepared.platform != self.platform:
            raise ValueError("prepared action belongs to a different platform")
        manifest = self.capabilities(prepared.connection_id)
        if prepared.action.action_type not in manifest.execute:
            raise UnsupportedPlatformAction(
                self.platform,
                prepared.action.action_type,
                "live_capability_unavailable",
                "Prepared action is outside the current certified capability subset.",
            )

    def _lease(
        self,
        connection: ConnectedAccount,
        scopes: frozenset[str],
        *,
        now: datetime,
        force_refresh: bool = False,
    ) -> OAuthCredentialLease:
        if not scopes <= frozenset(connection.granted_scopes):
            raise CredentialScopeDenied(
                "credential_scope_denied",
                "The connected account did not grant every required platform scope.",
            )
        lease = self._credential_provider.lease(
            connection,
            required_scopes=scopes,
            now=now,
            force_refresh=force_refresh,
        )
        if not scopes <= lease.scopes:
            raise CredentialScopeDenied(
                "credential_scope_denied",
                "The credential broker returned an insufficient scope set.",
            )
        if lease.expires_at <= now:
            raise LiveAuthenticationError(
                platform=self.platform,
                code="credential_expired",
                detail="The connected account credential has expired.",
            )
        return lease

    def _request_json(
        self,
        connection: ConnectedAccount,
        scopes: frozenset[str],
        method: str,
        url: str,
        *,
        now: datetime,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        data: Mapping[str, Any] | None = None,
        mutation: bool = False,
        allow_empty: bool = False,
    ) -> Any:
        for refresh_attempt in range(2):
            lease = self._lease(
                connection,
                scopes,
                now=now,
                force_refresh=refresh_attempt == 1,
            )
            headers = {
                "Accept": "application/json",
                "User-Agent": self._request_user_agent,
                **lease.authorization_headers(method=method, url=url),
            }
            try:
                response = self._http.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    data=data,
                )
            except (httpx.TimeoutException, httpx.NetworkError, TimeoutError, OSError) as exc:
                error_type = RemoteOutcomeUnknown if mutation else LiveTransientError
                raise error_type(
                    platform=self.platform,
                    code="transport_interrupted",
                    detail="The platform request was interrupted before a trustworthy result was received.",
                    retryable=not mutation,
                    outcome_unknown=mutation,
                ) from exc
            if response.status_code != 401 or refresh_attempt == 1:
                break
        status = response.status_code
        if status == 401:
            raise LiveAuthenticationError(
                platform=self.platform,
                code="platform_authentication_failed",
                detail="The platform rejected the connected account credential.",
                http_status=status,
            )
        if status == 403:
            raise LivePermissionError(
                platform=self.platform,
                code="platform_permission_denied",
                detail="The platform denied the requested account operation.",
                http_status=status,
            )
        if status == 404:
            raise LiveTargetNotFound(
                platform=self.platform,
                code="platform_target_not_found",
                detail="The requested platform target was not found.",
                http_status=status,
            )
        if status == 429:
            retry_value = response.headers.get("Retry-After")
            retry_after = int(retry_value) if retry_value and retry_value.isdigit() else None
            raise LiveRateLimited(
                platform=self.platform,
                code="platform_rate_limited",
                detail="The platform rate limit was reached.",
                retryable=True,
                http_status=status,
                retry_after_seconds=retry_after,
            )
        if status >= 500:
            error_type = RemoteOutcomeUnknown if mutation else LiveTransientError
            raise error_type(
                platform=self.platform,
                code="platform_unavailable",
                detail="The platform did not return a trustworthy result.",
                retryable=not mutation,
                outcome_unknown=mutation,
                http_status=status,
            )
        if status >= 400:
            raise LiveProtocolError(
                platform=self.platform,
                code="platform_request_rejected",
                detail=f"The platform rejected the request with HTTP {status}.",
                http_status=status,
            )
        if status in {202, 204} or (allow_empty and not response.text.strip()):
            return {}
        try:
            value = response.json()
        except (TypeError, ValueError) as exc:
            raise LiveProtocolError(
                platform=self.platform,
                code="platform_response_invalid",
                detail="The platform returned an invalid JSON response.",
                http_status=status,
            ) from exc
        if not isinstance(value, (dict, list)):
            raise LiveProtocolError(
                platform=self.platform,
                code="platform_response_invalid",
                detail="The platform returned an unexpected response shape.",
                http_status=status,
            )
        return value

    @staticmethod
    def _state_matches(observed: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
        return all(observed.get(key) == value for key, value in expected.items() if key != "remote_ref")

    @staticmethod
    def _state_reference(state: Mapping[str, Any]) -> str | None:
        value = state.get("remote_ref")
        return str(value) if value else None

    def _observe_controls(
        self,
        connection: ConnectedAccount,
        *,
        now: datetime,
        sample_size: int,
    ) -> AccountObservation:
        raise NotImplementedError

    def _read_action_state(
        self,
        connection: ConnectedAccount,
        action: ProposedAction,
        *,
        now: datetime,
    ) -> Mapping[str, Any]:
        raise NotImplementedError

    def _desired_state(
        self,
        action: ProposedAction,
        before_state: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        raise NotImplementedError

    def _mutate_to_state(
        self,
        connection: ConnectedAccount,
        action: ProposedAction,
        desired_state: Mapping[str, Any],
        *,
        current_state: Mapping[str, Any],
        now: datetime,
    ) -> str | None:
        raise NotImplementedError
