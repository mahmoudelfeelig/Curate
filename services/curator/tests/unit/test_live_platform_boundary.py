from __future__ import annotations

import copy
import json
import pickle
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from feed_passport.adapters.platforms.live.bluesky import (
    ATPROTO_SCOPE,
    LegacyBlueskyTokenTestAdapter,
)
from feed_passport.adapters.platforms.live.reddit import (
    REDDIT_READ_SCOPE,
    REDDIT_WRITE_SCOPE,
    RedditLiveAdapter,
)
from feed_passport.adapters.platforms.live.x import (
    X_FOLLOWS_READ,
    X_FOLLOWS_WRITE,
    X_MUTE_READ,
    X_USER_READ,
    XLiveAdapter,
)
from feed_passport.adapters.platforms.live.youtube import YOUTUBE_SCOPE, YouTubeLiveAdapter
from feed_passport.adapters.platforms.base import UnsupportedPlatformAction
from feed_passport.domain import (
    ActionOutcome,
    ActionReceipt,
    ActionStatus,
    ActionType,
    CapabilityLevel,
    ProposedAction,
)
from feed_passport.domain.connections import AuthorizedConnection, ConnectionStatus
from feed_passport.ports.credentials import OAuthCredentialLease
from feed_passport.ports.live_platform import HttpxNoAmbientClient, ValidatedLiveCertification


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class FakeConnection:
    id: str
    owner_id: str
    platform: str
    external_subject_id: str
    granted_scopes: frozenset[str]
    credential_ref: str = "credential:test"
    status: str = "active"


class UnusedCredentials:
    def lease(self, *_args, **_kwargs):
        raise AssertionError("credential broker must not be called")


class NoNetwork:
    def request(self, *_args, **_kwargs):
        raise AssertionError("network must not be called")


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


class MutationProbeYouTube(YouTubeLiveAdapter):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.mutation_calls = 0
        self.read_present = False
        self.expire_during_read = False
        self.expire_during_state_match = False

    def _read_action_state(self, _connection, action, *, now: datetime):
        state = {
            "present": self.read_present,
            "target_id": action.target,
            "remote_ref": None,
        }
        if self.expire_during_read:
            self.expire_during_read = False
            self._clock.value = self.validated_live_certification.expires_at
        return state

    def _state_matches(self, observed, expected):
        if self.expire_during_state_match:
            self.expire_during_state_match = False
            self._clock.value = self.validated_live_certification.expires_at
        return super()._state_matches(observed, expected)

    def _mutate_to_state(self, *_args, **_kwargs):
        self.mutation_calls += 1
        return "probe-write"


def proposed_action(
    bound: FakeConnection,
    action_type: ActionType,
) -> ProposedAction:
    return ProposedAction(
        id=f"action:{action_type.value}",
        destination_id=bound.id,
        action_type=action_type,
        target="creator.example",
        reason="Apply an explicitly approved private account control.",
        idempotency_key=f"idempotency:{action_type.value}",
        reversible=True,
    )


def certification(platform: str, actions: frozenset[ActionType]) -> ValidatedLiveCertification:
    return ValidatedLiveCertification(
        platform=platform,
        certified_at=NOW,
        expires_at=NOW + timedelta(days=14),
        code_revision="a" * 40,
        execute=actions,
        observe=frozenset({"authorized_controls"}),
        verify=frozenset({"authorized_controls"}),
        rollback=actions,
        receipt_ref=f"conformance:{platform}:001",
        evidence_sha256="a" * 64,
    )


def connection(platform: str, scopes: frozenset[str]) -> FakeConnection:
    return FakeConnection(
        id=f"connection:{platform}:one",
        owner_id="owner-one",
        platform=platform,
        external_subject_id="12345" if platform == "x" else "subject-one",
        granted_scopes=scopes,
    )


def test_oauth_credential_lease_is_redacted_and_nonserializable() -> None:
    secret = "secret-access-token-never-print"
    lease = OAuthCredentialLease(
        access_token=secret,
        credential_ref="credential:one",
        scopes=frozenset({"read"}),
        expires_at=NOW + timedelta(minutes=5),
    )

    assert secret not in repr(lease)
    assert secret not in str(lease)
    assert lease.authorization_headers(method="GET", url="https://example.test/resource") == {
        "Authorization": f"Bearer {secret}"
    }
    with pytest.raises(TypeError):
        json.dumps(lease)
    with pytest.raises(TypeError):
        pickle.dumps(lease)
    with pytest.raises(TypeError):
        copy.copy(lease)
    with pytest.raises(ValueError, match="HTTPS"):
        lease.authorization_headers(method="GET", url="http://example.test/resource")


def test_http_client_preserves_query_embedded_in_url_when_params_are_omitted() -> None:
    requested_urls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, json={"items": [{"id": "channel-one"}]})

    client = HttpxNoAmbientClient()
    client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
    try:
        client.request(
            "GET",
            "https://www.googleapis.com/youtube/v3/channels?part=id&mine=true",
        )
    finally:
        client.close()

    assert requested_urls == [
        "https://www.googleapis.com/youtube/v3/channels?part=id&mine=true"
    ]


def test_dpop_lease_requires_and_uses_request_bound_proof() -> None:
    calls: list[tuple[str, str, str]] = []

    def proof(method: str, url: str, token: str) -> str:
        calls.append((method, url, token))
        return "signed-dpop-proof"

    lease = OAuthCredentialLease(
        access_token="dpop-access-token",
        credential_ref="credential:bluesky",
        scopes=ATPROTO_SCOPE,
        expires_at=NOW + timedelta(minutes=5),
        resource_server="https://pds.example.test/",
        scheme="DPoP",
        proof_factory=proof,
    )
    headers = lease.authorization_headers(
        method="post",
        url="https://pds.example.test/xrpc/app.bsky.graph.muteActor",
    )

    assert headers == {
        "Authorization": "DPoP dpop-access-token",
        "DPoP": "signed-dpop-proof",
    }
    assert calls == [
        (
            "POST",
            "https://pds.example.test/xrpc/app.bsky.graph.muteActor",
            "dpop-access-token",
        )
    ]


def test_certification_rejects_public_engagement_and_uncovered_rollback() -> None:
    with pytest.raises(ValueError, match="public engagement"):
        certification("x", frozenset({ActionType.LIKE}))
    with pytest.raises(ValueError, match="rollback"):
        ValidatedLiveCertification(
            platform="x",
            certified_at=NOW,
            expires_at=NOW + timedelta(days=14),
            code_revision="a" * 40,
            execute=frozenset({ActionType.FOLLOW_CREATOR}),
            observe=frozenset(),
            verify=frozenset(),
            rollback=frozenset({ActionType.UNFOLLOW_CREATOR}),
            receipt_ref="conformance:x:bad",
            evidence_sha256="b" * 64,
        )


@pytest.mark.parametrize(
    ("adapter_type", "platform", "scopes", "action"),
    (
        (YouTubeLiveAdapter, "youtube", frozenset({YOUTUBE_SCOPE}), ActionType.SUBSCRIBE_CREATOR),
        (
            XLiveAdapter,
            "x",
            frozenset({X_USER_READ, X_FOLLOWS_READ, X_FOLLOWS_WRITE, X_MUTE_READ}),
            ActionType.FOLLOW_CREATOR,
        ),
        (LegacyBlueskyTokenTestAdapter, "bluesky", ATPROTO_SCOPE, ActionType.MUTE_CREATOR),
    ),
)
def test_live_candidates_remain_guided_without_validated_certification(
    adapter_type,
    platform: str,
    scopes: frozenset[str],
    action: ActionType,
) -> None:
    bound = connection(platform, scopes)
    adapter = adapter_type(
        connections={bound.id: bound},
        credential_provider=UnusedCredentials(),
        http_client=NoNetwork(),
    )

    manifest = adapter.capabilities(bound.id)

    assert manifest.level is CapabilityLevel.GUIDED
    assert not manifest.execute
    assert manifest.certified_at is None
    assert action not in manifest.execute


def test_certification_promotes_only_covered_and_granted_safe_actions() -> None:
    scopes = frozenset({X_USER_READ, X_FOLLOWS_READ, X_FOLLOWS_WRITE, X_MUTE_READ})
    bound = connection("x", scopes)
    adapter = XLiveAdapter(
        connections={bound.id: bound},
        credential_provider=UnusedCredentials(),
        http_client=NoNetwork(),
        certification=certification(
            "x",
            frozenset({ActionType.FOLLOW_CREATOR, ActionType.UNFOLLOW_CREATOR}),
        ),
    )

    manifest = adapter.capabilities(bound.id)

    assert manifest.level is CapabilityLevel.EXECUTABLE
    assert manifest.execute == frozenset(
        {ActionType.FOLLOW_CREATOR, ActionType.UNFOLLOW_CREATOR}
    )
    assert manifest.rollback == manifest.execute
    assert not manifest.execute & {
        ActionType.LIKE,
        ActionType.COMMENT,
        ActionType.POST,
        ActionType.REPOST,
        ActionType.SEND_MESSAGE,
    }


def test_missing_granted_scope_fails_closed_to_guided() -> None:
    bound = connection("youtube", frozenset())
    adapter = YouTubeLiveAdapter(
        connections={bound.id: bound},
        credential_provider=UnusedCredentials(),
        http_client=NoNetwork(),
        certification=certification("youtube", frozenset({ActionType.SUBSCRIBE_CREATOR})),
    )

    assert adapter.capabilities(bound.id).level is CapabilityLevel.GUIDED


def test_runtime_authorized_connection_shape_binds_without_credentials() -> None:
    adapter = YouTubeLiveAdapter(
        connections={},
        credential_provider=UnusedCredentials(),
        http_client=NoNetwork(),
        certification=certification("youtube", frozenset({ActionType.SUBSCRIBE_CREATOR})),
    )
    bound = AuthorizedConnection(
        id="connection:youtube:runtime",
        owner_id="owner-one",
        platform="youtube",
        status=ConnectionStatus.ACTIVE,
        external_subject="youtube-subject",
        credential_ref="credential:youtube:runtime",
        metadata={"granted_scopes": [YOUTUBE_SCOPE]},
        created_at=NOW,
        updated_at=NOW,
    )

    adapter.bind_connection(bound)
    assert adapter.capabilities(bound.id).level is CapabilityLevel.EXECUTABLE
    adapter.unbind_connection(bound.id)
    assert adapter.capabilities(bound.id).level is CapabilityLevel.GUIDED


def test_bluesky_custom_feed_creation_cannot_be_certified_by_account_transport() -> None:
    bound = connection("bluesky", ATPROTO_SCOPE)
    with pytest.raises(ValueError, match="candidate action"):
        LegacyBlueskyTokenTestAdapter(
            connections={bound.id: bound},
            credential_provider=UnusedCredentials(),
            http_client=NoNetwork(),
            certification=certification(
                "bluesky",
                frozenset({ActionType.CREATE_CUSTOM_FEED}),
            ),
        )


def test_reddit_remains_guided_until_api_approval_is_explicitly_verified() -> None:
    bound = connection("reddit", frozenset({REDDIT_READ_SCOPE, REDDIT_WRITE_SCOPE}))
    adapter = RedditLiveAdapter(
        connections={bound.id: bound},
        credential_provider=UnusedCredentials(),
        http_client=NoNetwork(),
        certification=certification("reddit", frozenset({ActionType.SUBSCRIBE_CREATOR})),
        approval_verified=False,
    )

    assert adapter.capabilities(bound.id).level is CapabilityLevel.GUIDED
    assert adapter.health(now=NOW).mode == "approval_required"
    action = proposed_action(bound, ActionType.SUBSCRIBE_CREATOR)
    outcome = adapter.execute(bound.id, action, now=NOW)
    assert outcome.status is ActionStatus.GUIDED
    with pytest.raises(UnsupportedPlatformAction, match="no certified live transport"):
        adapter.prepare_remote_action(bound.id, action, now=NOW)


def test_certification_expiry_fails_closed_between_prepare_and_apply() -> None:
    bound = connection("youtube", frozenset({YOUTUBE_SCOPE}))
    clock = MutableClock(NOW)
    adapter = MutationProbeYouTube(
        connections={bound.id: bound},
        credential_provider=UnusedCredentials(),
        http_client=NoNetwork(),
        certification=certification(
            "youtube",
            frozenset({ActionType.SUBSCRIBE_CREATOR}),
        ),
        clock=clock,
    )
    action = proposed_action(bound, ActionType.SUBSCRIBE_CREATOR)
    prepared = adapter.prepare_remote_action(bound.id, action, now=NOW)

    clock.value = adapter.validated_live_certification.expires_at
    assert adapter.capabilities(bound.id).level is CapabilityLevel.GUIDED
    assert adapter.health(now=clock.value).mode == "live_certification_expired"
    with pytest.raises(UnsupportedPlatformAction, match="current validated certification"):
        adapter.apply_prepared_action(prepared, now=NOW)
    with pytest.raises(UnsupportedPlatformAction, match="current validated certification"):
        adapter.prepare_remote_action(bound.id, action, now=NOW)
    receipt = ActionReceipt(
        id="receipt:expired-authority",
        passport_id="passport-one",
        passport_version=1,
        destination_id=bound.id,
        outcomes=(
            ActionOutcome(
                action=action,
                status=ActionStatus.EXECUTED,
                before_state=prepared.before_state,
                after_state=prepared.desired_state,
                executed_at=NOW,
            ),
        ),
        issued_at=NOW,
        trace_id="trace-expired-authority",
        previous_checkpoint_id=None,
    )
    with pytest.raises(UnsupportedPlatformAction, match="current validated certification"):
        adapter.rollback(bound.id, receipt, now=NOW)
    assert adapter.mutation_calls == 0


def test_certification_expiry_at_the_provider_write_boundary_fails_closed() -> None:
    bound = connection("youtube", frozenset({YOUTUBE_SCOPE}))
    clock = MutableClock(NOW)
    adapter = MutationProbeYouTube(
        connections={bound.id: bound},
        credential_provider=UnusedCredentials(),
        http_client=NoNetwork(),
        certification=certification(
            "youtube",
            frozenset({ActionType.SUBSCRIBE_CREATOR}),
        ),
        clock=clock,
    )
    action = proposed_action(bound, ActionType.SUBSCRIBE_CREATOR)
    prepared = adapter.prepare_remote_action(bound.id, action, now=NOW)

    adapter.expire_during_state_match = True
    with pytest.raises(UnsupportedPlatformAction, match="current validated certification"):
        adapter.apply_prepared_action(prepared, now=NOW)

    clock.value = NOW
    adapter.read_present = True
    adapter.expire_during_read = True
    receipt = ActionReceipt(
        id="receipt:expires-during-rollback-read",
        passport_id="passport-one",
        passport_version=1,
        destination_id=bound.id,
        outcomes=(
            ActionOutcome(
                action=action,
                status=ActionStatus.EXECUTED,
                before_state=prepared.before_state,
                after_state=prepared.desired_state,
                executed_at=NOW,
            ),
        ),
        issued_at=NOW,
        trace_id="trace-expires-during-rollback-read",
        previous_checkpoint_id=None,
    )
    with pytest.raises(UnsupportedPlatformAction, match="current validated certification"):
        adapter.rollback(bound.id, receipt, now=NOW)

    assert adapter.mutation_calls == 0
