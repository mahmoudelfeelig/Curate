from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

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
    X_MUTE_WRITE,
    X_USER_READ,
    XLiveAdapter,
)
from feed_passport.adapters.platforms.live.youtube import YOUTUBE_SCOPE, YouTubeLiveAdapter
from feed_passport.domain import (
    ActionReceipt,
    ActionStatus,
    ActionType,
    ProposedAction,
)
from feed_passport.ports.credentials import OAuthCredentialLease
from feed_passport.ports.live_platform import (
    LivePermissionError,
    LiveProtocolError,
    LiveRateLimited,
    RemoteOutcomeUnknown,
    ValidatedLiveCertification,
)


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


@dataclass
class FakeResponse:
    status_code: int
    payload: Any = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    raw_text: str | None = None

    @property
    def text(self) -> str:
        if self.raw_text is not None:
            return self.raw_text
        return "" if self.status_code in {202, 204} else "json"

    def json(self) -> Any:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class QueueHttpClient:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected HTTP call: {method} {url}")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FakeCredentials:
    def __init__(self, *, dpop: bool = False, token: str = "super-secret-token") -> None:
        self.dpop = dpop
        self.token = token
        self.refreshes: list[bool] = []

    def lease(
        self,
        connection: FakeConnection,
        *,
        required_scopes: frozenset[str],
        now: datetime,
        force_refresh: bool = False,
    ) -> OAuthCredentialLease:
        self.refreshes.append(force_refresh)
        return OAuthCredentialLease(
            access_token=f"{self.token}-refreshed" if force_refresh else self.token,
            credential_ref=connection.credential_ref,
            scopes=connection.granted_scopes,
            expires_at=now + timedelta(minutes=10),
            resource_server="https://pds.example.test" if self.dpop else None,
            scheme="DPoP" if self.dpop else "Bearer",
            proof_factory=(lambda method, url, token: "proof-for-request") if self.dpop else None,
        )


def certification(platform: str, actions: frozenset[ActionType]) -> ValidatedLiveCertification:
    return ValidatedLiveCertification(
        platform=platform,
        certified_at=NOW,
        execute=actions,
        observe=frozenset({"authorized_controls"}),
        verify=frozenset({"authorized_controls"}),
        rollback=actions,
        receipt_ref=f"conformance:{platform}:001",
        evidence_sha256="c" * 64,
    )


def action(
    action_type: ActionType,
    connection_id: str,
    target: str,
    *,
    reversible: bool = True,
) -> ProposedAction:
    return ProposedAction(
        id=f"action:{action_type.value}",
        destination_id=connection_id,
        action_type=action_type,
        target=target,
        reason="Authorized transport contract test.",
        idempotency_key=f"idempotency:{action_type.value}:{target}",
        reversible=reversible,
    )


def youtube_adapter(http: QueueHttpClient) -> tuple[YouTubeLiveAdapter, FakeConnection]:
    bound = FakeConnection(
        id="connection:youtube:one",
        owner_id="owner-one",
        platform="youtube",
        external_subject_id="youtube-owner",
        granted_scopes=frozenset({YOUTUBE_SCOPE}),
    )
    adapter = YouTubeLiveAdapter(
        connections={bound.id: bound},
        credential_provider=FakeCredentials(),
        http_client=http,
        certification=certification(
            "youtube",
            frozenset({ActionType.SUBSCRIBE_CREATOR, ActionType.UNSUBSCRIBE_CREATOR}),
        ),
    )
    return adapter, bound


def test_youtube_subscription_prepare_apply_verify_and_inverse_rollback() -> None:
    channel_id = "UC12345678901234567890"
    http = QueueHttpClient(
        [
            FakeResponse(200, {"items": []}),
            FakeResponse(200, {"id": "subscription-one"}),
            FakeResponse(
                200,
                {
                    "items": [
                        {
                            "id": "subscription-one",
                            "snippet": {"resourceId": {"channelId": channel_id}},
                        }
                    ]
                },
            ),
            FakeResponse(
                200,
                {
                    "items": [
                        {
                            "id": "subscription-one",
                            "snippet": {"resourceId": {"channelId": channel_id}},
                        }
                    ]
                },
            ),
            FakeResponse(204),
            FakeResponse(200, {"items": []}),
        ]
    )
    adapter, bound = youtube_adapter(http)
    proposed = action(ActionType.SUBSCRIBE_CREATOR, bound.id, channel_id)

    prepared = adapter.prepare_remote_action(bound.id, proposed, now=NOW)
    assert prepared.before_state["present"] is False
    assert len(prepared.idempotency_fingerprint) == 64
    outcome = adapter.apply_prepared_action(prepared, now=NOW)
    assert outcome.status is ActionStatus.EXECUTED
    assert outcome.after_state["present"] is True
    receipt = ActionReceipt(
        id="receipt:youtube:one",
        passport_id="passport-one",
        passport_version=1,
        destination_id=bound.id,
        outcomes=(outcome,),
        issued_at=NOW,
        trace_id="trace-one",
        previous_checkpoint_id=None,
    )
    rolled_back = adapter.rollback(bound.id, receipt, now=NOW)

    assert rolled_back.restored_actions == (proposed.id,)
    assert not rolled_back.failed_actions
    assert [call["method"] for call in http.calls] == ["GET", "POST", "GET", "GET", "DELETE", "GET"]
    assert http.calls[1]["url"].endswith("/subscriptions")
    assert http.calls[1]["params"] == {"part": "snippet"}
    assert http.calls[1]["json"]["snippet"]["resourceId"]["channelId"] == channel_id
    assert http.calls[4]["params"] == {"id": "subscription-one"}
    assert not http.responses


def test_youtube_observation_follows_pagination_without_exposing_token() -> None:
    secret = "token-that-must-never-be-in-output"
    http = QueueHttpClient(
        [
            FakeResponse(
                200,
                {
                    "items": [
                        {"id": "sub-1", "snippet": {"resourceId": {"channelId": "UC-one"}}}
                    ],
                    "nextPageToken": "page-two",
                },
            ),
            FakeResponse(
                200,
                {
                    "items": [
                        {"id": "sub-2", "snippet": {"resourceId": {"channelId": "UC-two"}}}
                    ]
                },
            ),
        ]
    )
    bound = FakeConnection(
        id="connection:youtube:paged",
        owner_id="owner-one",
        platform="youtube",
        external_subject_id="youtube-owner",
        granted_scopes=frozenset({YOUTUBE_SCOPE}),
    )
    adapter = YouTubeLiveAdapter(
        connections={bound.id: bound},
        credential_provider=FakeCredentials(token=secret),
        http_client=http,
        certification=certification("youtube", frozenset({ActionType.SUBSCRIBE_CREATOR})),
    )

    observed = adapter.observe(bound.id, now=NOW)

    assert observed.followed_creators == frozenset({"UC-one", "UC-two"})
    assert http.calls[1]["params"]["pageToken"] == "page-two"
    assert secret not in repr(observed)


def test_x_follow_uses_exact_v2_paths_and_reconciles() -> None:
    scopes = frozenset({X_USER_READ, X_FOLLOWS_READ, X_FOLLOWS_WRITE, X_MUTE_READ})
    bound = FakeConnection(
        id="connection:x:one",
        owner_id="owner-one",
        platform="x",
        external_subject_id="111",
        granted_scopes=scopes,
    )
    http = QueueHttpClient(
        [
            FakeResponse(200, {"data": [], "meta": {}}),
            FakeResponse(200, {"data": {"following": True}}),
            FakeResponse(200, {"data": [{"id": "222"}], "meta": {}}),
        ]
    )
    adapter = XLiveAdapter(
        connections={bound.id: bound},
        credential_provider=FakeCredentials(),
        http_client=http,
        certification=certification("x", frozenset({ActionType.FOLLOW_CREATOR})),
    )

    outcome = adapter.execute(
        bound.id,
        action(ActionType.FOLLOW_CREATOR, bound.id, "222"),
        now=NOW,
    )

    assert outcome.status is ActionStatus.EXECUTED
    assert http.calls[0]["url"] == "https://api.x.com/2/users/111/following"
    assert http.calls[1]["url"] == "https://api.x.com/2/users/111/following"
    assert http.calls[1]["json"] == {"target_user_id": "222"}


@pytest.mark.parametrize(
    ("action_type", "relationship", "present", "method", "suffix"),
    (
        (ActionType.FOLLOW_CREATOR, "following", True, "POST", "/users/111/following"),
        (ActionType.UNFOLLOW_CREATOR, "following", False, "DELETE", "/users/111/following/222"),
        (ActionType.MUTE_CREATOR, "muting", True, "POST", "/users/111/muting"),
        (ActionType.UNMUTE_CREATOR, "muting", False, "DELETE", "/users/111/muting/222"),
    ),
)
def test_x_relationship_mutation_paths(
    action_type: ActionType,
    relationship: str,
    present: bool,
    method: str,
    suffix: str,
) -> None:
    scopes = frozenset(
        {
            X_USER_READ,
            X_FOLLOWS_READ,
            X_FOLLOWS_WRITE,
            X_MUTE_READ,
            X_MUTE_WRITE,
        }
    )
    bound = FakeConnection(
        id="connection:x:paths",
        owner_id="owner-one",
        platform="x",
        external_subject_id="111",
        granted_scopes=scopes,
    )
    http = QueueHttpClient([FakeResponse(200, {"data": {}})])
    adapter = XLiveAdapter(
        connections={bound.id: bound},
        credential_provider=FakeCredentials(),
        http_client=http,
        certification=certification("x", frozenset({action_type})),
    )

    adapter._mutate_to_state(
        bound,
        action(action_type, bound.id, "222"),
        {"present": present, "target_id": "222", "relationship": relationship},
        current_state={
            "present": not present,
            "target_id": "222",
            "relationship": relationship,
            "remote_ref": "x:user:222",
        },
        now=NOW,
    )

    assert http.calls[0]["method"] == method
    assert http.calls[0]["url"].endswith(suffix)
    if method == "POST":
        assert http.calls[0]["json"] == {"target_user_id": "222"}


def test_reddit_subscription_is_approval_gated_and_uses_form_transport() -> None:
    scopes = frozenset({REDDIT_READ_SCOPE, REDDIT_WRITE_SCOPE})
    bound = FakeConnection(
        id="connection:reddit:one",
        owner_id="owner-one",
        platform="reddit",
        external_subject_id="reddit-owner",
        granted_scopes=scopes,
    )
    http = QueueHttpClient(
        [
            FakeResponse(200, {"data": {"children": [], "after": None}}),
            FakeResponse(200, raw_text=""),
            FakeResponse(
                200,
                {"data": {"children": [{"data": {"display_name": "Python"}}], "after": None}},
            ),
        ]
    )
    adapter = RedditLiveAdapter(
        connections={bound.id: bound},
        credential_provider=FakeCredentials(),
        http_client=http,
        certification=certification("reddit", frozenset({ActionType.SUBSCRIBE_CREATOR})),
        approval_verified=True,
        user_agent="windows:feed-passport:test (by /u/dummy-operator)",
    )

    outcome = adapter.execute(
        bound.id,
        action(ActionType.SUBSCRIBE_CREATOR, bound.id, "r/Python"),
        now=NOW,
    )

    assert outcome.status is ActionStatus.EXECUTED
    assert http.calls[1]["url"] == "https://oauth.reddit.com/api/subscribe"
    assert http.calls[1]["data"] == {"action": "sub", "sr_name": "python"}
    assert all(
        call["headers"]["User-Agent"]
        == "windows:feed-passport:test (by /u/dummy-operator)"
        for call in http.calls
    )


def test_reddit_unsubscribe_uses_the_approved_private_account_control() -> None:
    scopes = frozenset({REDDIT_READ_SCOPE, REDDIT_WRITE_SCOPE})
    bound = FakeConnection(
        id="connection:reddit:unsub",
        owner_id="owner-one",
        platform="reddit",
        external_subject_id="reddit-owner",
        granted_scopes=scopes,
    )
    http = QueueHttpClient([FakeResponse(200, raw_text="")])
    adapter = RedditLiveAdapter(
        connections={bound.id: bound},
        credential_provider=FakeCredentials(),
        http_client=http,
        certification=certification("reddit", frozenset({ActionType.UNSUBSCRIBE_CREATOR})),
        approval_verified=True,
        user_agent="windows:feed-passport:test (by /u/dummy-operator)",
    )

    adapter._mutate_to_state(
        bound,
        action(ActionType.UNSUBSCRIBE_CREATOR, bound.id, "r/python"),
        {"present": False, "target_id": "python"},
        current_state={"present": True, "target_id": "python", "remote_ref": "reddit:subreddit:python"},
        now=NOW,
    )

    assert http.calls[0]["url"] == "https://oauth.reddit.com/api/subscribe"
    assert http.calls[0]["data"] == {"action": "unsub", "sr_name": "python"}


def test_bluesky_muted_word_preserves_preferences_and_uses_dpop_xrpc_paths() -> None:
    bound = FakeConnection(
        id="connection:bluesky:one",
        owner_id="owner-one",
        platform="bluesky",
        external_subject_id="did:plc:owner",
        granted_scopes=ATPROTO_SCOPE,
    )
    initial_preferences = [
        {"$type": "app.bsky.actor.defs#adultContentPref", "enabled": False}
    ]
    resulting_preferences = [
        *initial_preferences,
        {
            "$type": "app.bsky.actor.defs#mutedWordsPref",
            "items": [
                {
                    "$type": "app.bsky.actor.defs#mutedWord",
                    "id": "word-id",
                    "value": "spoilers",
                    "targets": ["content", "tag"],
                    "actorTarget": "all",
                }
            ],
        },
    ]
    http = QueueHttpClient(
        [
            FakeResponse(200, {"preferences": initial_preferences}),
            FakeResponse(200, {"preferences": initial_preferences}),
            FakeResponse(200, raw_text=""),
            FakeResponse(200, {"preferences": resulting_preferences}),
        ]
    )
    adapter = LegacyBlueskyTokenTestAdapter(
        connections={bound.id: bound},
        credential_provider=FakeCredentials(dpop=True),
        http_client=http,
        certification=certification("bluesky", frozenset({ActionType.MUTE_KEYWORD})),
    )

    outcome = adapter.execute(
        bound.id,
        action(ActionType.MUTE_KEYWORD, bound.id, "spoilers"),
        now=NOW,
    )

    assert outcome.status is ActionStatus.EXECUTED
    put_call = http.calls[2]
    assert put_call["url"].endswith("/xrpc/app.bsky.actor.putPreferences")
    assert put_call["headers"]["Authorization"].startswith("DPoP ")
    assert put_call["headers"]["DPoP"] == "proof-for-request"
    sent_preferences = put_call["json"]["preferences"]
    assert initial_preferences[0] in sent_preferences
    muted_pref = next(
        item
        for item in sent_preferences
        if item["$type"] == "app.bsky.actor.defs#mutedWordsPref"
    )
    assert muted_pref["items"][0]["value"] == "spoilers"


@pytest.mark.parametrize(
    ("action_type", "relationship", "present", "method_name", "response", "remote_ref"),
    (
        (
            ActionType.FOLLOW_CREATOR,
            "follow",
            True,
            "com.atproto.repo.createRecord",
            {"uri": "at://did:plc:owner/app.bsky.graph.follow/follow-one", "cid": "cid-one"},
            None,
        ),
        (
            ActionType.UNFOLLOW_CREATOR,
            "follow",
            False,
            "com.atproto.repo.deleteRecord",
            {},
            "at://did:plc:owner/app.bsky.graph.follow/follow-one",
        ),
        (ActionType.MUTE_CREATOR, "mute", True, "app.bsky.graph.muteActor", {}, None),
        (ActionType.UNMUTE_CREATOR, "mute", False, "app.bsky.graph.unmuteActor", {}, None),
    ),
)
def test_bluesky_graph_mutation_paths(
    action_type: ActionType,
    relationship: str,
    present: bool,
    method_name: str,
    response: dict[str, Any],
    remote_ref: str | None,
) -> None:
    bound = FakeConnection(
        id="connection:bluesky:paths",
        owner_id="owner-one",
        platform="bluesky",
        external_subject_id="did:plc:owner",
        granted_scopes=ATPROTO_SCOPE,
    )
    http = QueueHttpClient([FakeResponse(200, response, raw_text="" if not response else None)])
    adapter = LegacyBlueskyTokenTestAdapter(
        connections={bound.id: bound},
        credential_provider=FakeCredentials(dpop=True),
        http_client=http,
        certification=certification("bluesky", frozenset({action_type})),
    )

    adapter._mutate_to_state(
        bound,
        action(action_type, bound.id, "did:plc:target"),
        {"present": present, "target_id": "did:plc:target", "relationship": relationship},
        current_state={
            "present": not present,
            "target_id": "did:plc:target",
            "relationship": relationship,
            "remote_ref": remote_ref,
        },
        now=NOW,
    )

    assert http.calls[0]["url"].endswith(f"/xrpc/{method_name}")
    assert http.calls[0]["headers"]["Authorization"].startswith("DPoP ")
    if action_type is ActionType.UNFOLLOW_CREATOR:
        assert http.calls[0]["json"]["rkey"] == "follow-one"


def test_bluesky_unmute_word_preserves_unrelated_preferences() -> None:
    bound = FakeConnection(
        id="connection:bluesky:unmute-word",
        owner_id="owner-one",
        platform="bluesky",
        external_subject_id="did:plc:owner",
        granted_scopes=ATPROTO_SCOPE,
    )
    preferences = [
        {"$type": "app.bsky.actor.defs#adultContentPref", "enabled": False},
        {
            "$type": "app.bsky.actor.defs#mutedWordsPref",
            "items": [
                {"id": "remove-me", "value": "spoilers", "targets": ["content"]},
                {"id": "keep-me", "value": "sports", "targets": ["content"]},
            ],
        },
    ]
    http = QueueHttpClient(
        [FakeResponse(200, {"preferences": preferences}), FakeResponse(200, raw_text="")]
    )
    adapter = LegacyBlueskyTokenTestAdapter(
        connections={bound.id: bound},
        credential_provider=FakeCredentials(dpop=True),
        http_client=http,
        certification=certification("bluesky", frozenset({ActionType.UNMUTE_KEYWORD})),
    )

    adapter._mutate_to_state(
        bound,
        action(ActionType.UNMUTE_KEYWORD, bound.id, "spoilers"),
        {"present": False, "target_id": "spoilers", "relationship": "muted_word"},
        current_state={
            "present": True,
            "target_id": "spoilers",
            "relationship": "muted_word",
            "remote_ref": "remove-me",
        },
        now=NOW,
    )

    sent = http.calls[1]["json"]["preferences"]
    assert sent[0] == preferences[0]
    muted = next(item for item in sent if item["$type"] == "app.bsky.actor.defs#mutedWordsPref")
    assert [item["value"] for item in muted["items"]] == ["sports"]


def test_authentication_refreshes_once_and_errors_are_sanitized() -> None:
    http = QueueHttpClient([FakeResponse(401), FakeResponse(403, {"token": "echoed-secret"})])
    credentials = FakeCredentials(token="top-secret")
    adapter, bound = youtube_adapter(http)
    adapter._credential_provider = credentials

    with pytest.raises(LivePermissionError) as caught:
        adapter._request_json(
            bound,
            frozenset({YOUTUBE_SCOPE}),
            "GET",
            "https://www.googleapis.com/youtube/v3/subscriptions",
            now=NOW,
        )

    assert credentials.refreshes == [False, True]
    assert "top-secret" not in str(caught.value)
    assert "echoed-secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("response", "error_type", "mutation"),
    (
        (FakeResponse(429, headers={"Retry-After": "7"}), LiveRateLimited, False),
        (FakeResponse(503), RemoteOutcomeUnknown, True),
        (FakeResponse(200, payload=ValueError("invalid")), LiveProtocolError, False),
    ),
)
def test_http_error_taxonomy(response: FakeResponse, error_type: type[Exception], mutation: bool) -> None:
    http = QueueHttpClient([response])
    adapter, bound = youtube_adapter(http)

    with pytest.raises(error_type):
        adapter._request_json(
            bound,
            frozenset({YOUTUBE_SCOPE}),
            "POST" if mutation else "GET",
            "https://www.googleapis.com/youtube/v3/subscriptions",
            now=NOW,
            mutation=mutation,
        )


def test_reconcile_confirms_no_mutation_without_reissuing_write() -> None:
    channel_id = "UC12345678901234567890"
    http = QueueHttpClient(
        [
            FakeResponse(200, {"items": []}),
            FakeResponse(200, {"items": []}),
        ]
    )
    adapter, bound = youtube_adapter(http)
    prepared = adapter.prepare_remote_action(
        bound.id,
        action(ActionType.SUBSCRIBE_CREATOR, bound.id, channel_id),
        now=NOW,
    )

    reconciled = adapter.reconcile_remote_action(prepared, now=NOW)

    assert reconciled.status is ActionStatus.FAILED
    assert reconciled.error_code == "remote_action_not_applied"
    assert [call["method"] for call in http.calls] == ["GET", "GET"]


def test_failed_post_write_verification_is_an_unknown_outcome() -> None:
    channel_id = "UC12345678901234567890"
    http = QueueHttpClient(
        [
            FakeResponse(200, {"items": []}),
            FakeResponse(200, {"id": "subscription-one"}),
            FakeResponse(503),
        ]
    )
    adapter, bound = youtube_adapter(http)
    prepared = adapter.prepare_remote_action(
        bound.id,
        action(ActionType.SUBSCRIBE_CREATOR, bound.id, channel_id),
        now=NOW,
    )

    with pytest.raises(RemoteOutcomeUnknown) as caught:
        adapter.apply_prepared_action(prepared, now=NOW)

    assert caught.value.code == "post_write_verification_unavailable"
    assert caught.value.outcome_unknown is True
    assert [call["method"] for call in http.calls] == ["GET", "POST", "GET"]
