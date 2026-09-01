from __future__ import annotations

import copy
import json
import pickle
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

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
from feed_passport.domain import ActionType, CapabilityLevel
from feed_passport.domain.connections import AuthorizedConnection, ConnectionStatus
from feed_passport.ports.credentials import OAuthCredentialLease
from feed_passport.ports.live_platform import ValidatedLiveCertification


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


def certification(platform: str, actions: frozenset[ActionType]) -> ValidatedLiveCertification:
    return ValidatedLiveCertification(
        platform=platform,
        certified_at=NOW,
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
