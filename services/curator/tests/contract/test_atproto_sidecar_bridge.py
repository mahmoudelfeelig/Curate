from __future__ import annotations

import copy
import json
import pickle
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from feed_passport.adapters.platforms.base import UnsupportedPlatformAction
from feed_passport.adapters.platforms.live.atproto_sidecar import (
    AtprotoSidecarClient,
    AtprotoSidecarLiveAdapter,
    AtprotoSidecarOperation,
)
from feed_passport.domain import (
    ActionOutcome,
    ActionReceipt,
    ActionStatus,
    ActionType,
    CapabilityLevel,
    ProposedAction,
)
from feed_passport.ports.live_platform import (
    LiveAuthenticationError,
    LivePermissionError,
    LiveProtocolError,
    LiveRateLimited,
    LiveTargetNotFound,
    LiveTransientError,
    RemoteOutcomeUnknown,
    ValidatedLiveCertification,
)


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
SECRET = "internal-sidecar-secret-with-at-least-32-characters"
OWNER_DID = "did:plc:owner123"
TARGET_DID = "did:plc:target456"
CONNECTION_REF = "c" * 43


@dataclass(frozen=True)
class FakeConnection:
    id: str = "connection:bluesky:one"
    owner_id: str = "owner-one"
    platform: str = "bluesky"
    external_subject_id: str = OWNER_DID
    granted_scopes: frozenset[str] = frozenset()
    credential_ref: str = CONNECTION_REF
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeResponse:
    status_code: int
    payload: Any
    headers: dict[str, str] = field(default_factory=dict)
    text: str = "json"

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
            raise AssertionError(f"unexpected sidecar request: {method} {url}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


class ExpiringRestoreHttpClient(QueueHttpClient):
    def __init__(
        self,
        responses: list[FakeResponse | Exception],
        *,
        clock: MutableClock,
        expires_at: datetime,
    ) -> None:
        super().__init__(responses)
        self.clock = clock
        self.expires_at = expires_at

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        response = super().request(method, url, **kwargs)
        if url.endswith("/v1/oauth/atproto/sessions/restore"):
            self.clock.value = self.expires_at
        return response


def restore_response(character: str = "l", *, subject: str = OWNER_DID) -> FakeResponse:
    return FakeResponse(
        200,
        {
            "platform": "bluesky",
            "connection_ref": CONNECTION_REF,
            "lease_ref": character * 43,
            "external_subject": subject,
            "expires_at": (NOW + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            "authorization_scheme": "DPoP",
        },
    )


def operation_response(operation: AtprotoSidecarOperation, result: dict[str, Any]) -> FakeResponse:
    return FakeResponse(
        200,
        {"platform": "bluesky", "operation": operation.value, "result": result},
    )


def client(http: QueueHttpClient, *, base_url: str = "https://sidecar.example") -> AtprotoSidecarClient:
    return AtprotoSidecarClient(
        base_url=base_url,
        internal_service_secret=SECRET,
        http_client=http,
    )


def certification(actions: frozenset[ActionType]) -> ValidatedLiveCertification:
    return ValidatedLiveCertification(
        platform="bluesky",
        certified_at=NOW,
        expires_at=NOW + timedelta(days=14),
        code_revision="a" * 40,
        execute=actions,
        observe=frozenset({"authorized_controls"}),
        verify=frozenset({"authorized_controls"}),
        rollback=actions,
        receipt_ref="conformance:bluesky:sidecar-001",
        evidence_sha256="a" * 64,
    )


def proposed(
    action_type: ActionType,
    *,
    target: str = TARGET_DID,
    connection_id: str = "connection:bluesky:one",
) -> ProposedAction:
    return ProposedAction(
        id=f"action:{action_type.value}",
        destination_id=connection_id,
        action_type=action_type,
        target=target,
        reason="Authorized sidecar bridge contract test.",
        idempotency_key=f"idempotency:{action_type.value}:{target}",
        reversible=True,
    )


def adapter(
    http: QueueHttpClient,
    *,
    actions: frozenset[ActionType],
    connection: FakeConnection | None = None,
    certified: bool = True,
) -> tuple[AtprotoSidecarLiveAdapter, FakeConnection]:
    bound = connection or FakeConnection()
    return (
        AtprotoSidecarLiveAdapter(
            connections={bound.id: bound},
            sidecar_client=client(http),
            certification=certification(actions) if certified else None,
        ),
        bound,
    )


@pytest.mark.parametrize(
    "base_url",
    (
        "http://sidecar.example",
        "http://127.0.0.1.evil.example",
        "https://user:password@sidecar.example",
        "https://sidecar.example/arbitrary/path",
        "https://sidecar.example?target=https://evil.example",
        "ftp://sidecar.example",
    ),
)
def test_client_rejects_non_https_non_loopback_and_non_origin_urls(base_url: str) -> None:
    with pytest.raises(ValueError):
        client(QueueHttpClient([]), base_url=base_url)


@pytest.mark.parametrize(
    "base_url",
    (
        "https://sidecar.example",
        "http://localhost:4310",
        "http://127.0.0.1:4310",
        "http://[::1]:4310",
    ),
)
def test_client_accepts_only_https_or_loopback_origins(base_url: str) -> None:
    assert "internal_auth=<redacted>" in repr(client(QueueHttpClient([]), base_url=base_url))


def test_restore_is_owner_bound_fixed_route_and_credential_redacted() -> None:
    http = QueueHttpClient([restore_response("r")])
    bridge = client(http)
    bound = FakeConnection()

    lease = bridge.restore(bound, now=NOW)

    call = http.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://sidecar.example/v1/oauth/atproto/sessions/restore"
    assert call["json"] == {"connection_ref": CONNECTION_REF}
    assert call["headers"] == {
        "Accept": "application/json",
        "Authorization": f"Bearer {SECRET}",
        "Content-Type": "application/json",
        "User-Agent": "feed-passport/0.1",
        "X-Feed-Passport-Owner": "owner-one",
    }
    rendered = f"{bridge!r}\n{bridge!s}\n{lease!r}\n{lease!s}"
    assert SECRET not in rendered
    assert CONNECTION_REF not in rendered
    assert "r" * 43 not in rendered
    with pytest.raises(TypeError):
        json.dumps(lease)
    with pytest.raises(TypeError):
        pickle.dumps(lease)
    with pytest.raises(TypeError):
        copy.copy(lease)
    with pytest.raises(TypeError):
        pickle.dumps(bridge)


def test_oauth_start_and_callback_use_only_fixed_owner_bound_sidecar_routes() -> None:
    callback_ref = "g" * 43
    http = QueueHttpClient(
        [
            FakeResponse(
                200,
                {
                    "platform": "bluesky",
                    "authorization_url": "https://pds.example/oauth/authorize?request_uri=urn%3Arequest",
                    "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
                },
            ),
            FakeResponse(
                200,
                {
                    "platform": "bluesky",
                    "connection_ref": callback_ref,
                    "external_subject": OWNER_DID,
                    "status": "active",
                },
            ),
        ]
    )
    bridge = client(http)

    started = bridge.start_authorization(
        owner_id="owner-one",
        handle="@Alice.BSky.Social",
        now=NOW,
    )
    grant = bridge.complete_authorization(
        owner_id="owner-one",
        query="code=callback-code&state=" + "s" * 43,
        now=NOW,
    )

    assert started["platform"] == "bluesky"
    assert http.calls[0]["url"].endswith("/v1/oauth/atproto/start")
    assert http.calls[0]["json"] == {"handle": "alice.bsky.social"}
    assert http.calls[1]["url"].endswith("/v1/oauth/atproto/callback")
    assert http.calls[1]["json"] == {"query": "code=callback-code&state=" + "s" * 43}
    assert all(
        call["headers"]["X-Feed-Passport-Owner"] == "owner-one" for call in http.calls
    )
    assert grant.credential_ref_for_registry(owner_id="owner-one") == callback_ref
    rendered = f"{grant!r}\n{grant!s}"
    assert callback_ref not in rendered
    assert SECRET not in rendered
    with pytest.raises(TypeError):
        copy.copy(grant)
    with pytest.raises(TypeError):
        pickle.dumps(grant)
    with pytest.raises(LiveAuthenticationError):
        grant.credential_ref_for_registry(owner_id="owner-two")


def test_oauth_revoke_maps_exact_reference_and_treats_interruption_as_unknown() -> None:
    revoked = QueueHttpClient(
        [
            FakeResponse(
                200,
                    {
                        "platform": "bluesky",
                        "connection_ref": CONNECTION_REF,
                        "status": "revoked",
                        "revocation_proof": "provider_confirmed",
                    },
            )
        ]
    )
    bridge = client(revoked)

    bridge.revoke_connection(FakeConnection(), now=NOW)

    assert revoked.calls[0]["url"].endswith("/v1/oauth/atproto/sessions/revoke")
    assert revoked.calls[0]["json"] == {"connection_ref": CONNECTION_REF}
    interrupted = QueueHttpClient([TimeoutError("no trustworthy response")])
    with pytest.raises(RemoteOutcomeUnknown) as caught:
        client(interrupted).revoke_connection(FakeConnection(), now=NOW)
    assert caught.value.outcome_unknown is True


def test_restore_refuses_a_sidecar_subject_from_another_owner_binding() -> None:
    http = QueueHttpClient([restore_response(subject="did:plc:other999")])

    with pytest.raises(LiveAuthenticationError, match="owner-bound"):
        client(http).restore(FakeConnection(), now=NOW)


def test_live_promotion_requires_certification_and_an_active_token_free_connection() -> None:
    no_network = QueueHttpClient([])
    guided, bound = adapter(
        no_network,
        actions=frozenset({ActionType.FOLLOW_CREATOR}),
        certified=False,
    )
    assert guided.capabilities(bound.id).level is CapabilityLevel.GUIDED
    assert not guided.capabilities(bound.id).execute

    executable, _ = adapter(
        no_network,
        actions=frozenset({ActionType.FOLLOW_CREATOR}),
    )
    assert executable.capabilities(bound.id).level is CapabilityLevel.EXECUTABLE
    assert executable.capabilities(bound.id).execute == frozenset({ActionType.FOLLOW_CREATOR})

    inactive = FakeConnection(id="connection:bluesky:reauth", status="reauth_required")
    inactive_adapter, _ = adapter(
        no_network,
        actions=frozenset({ActionType.FOLLOW_CREATOR}),
        connection=inactive,
    )
    assert inactive_adapter.capabilities(inactive.id).level is CapabilityLevel.GUIDED

    class TokenBearingConnection:
        id = "connection:bluesky:unsafe"
        owner_id = "owner-one"
        platform = "bluesky"
        external_subject_id = OWNER_DID
        granted_scopes = frozenset()
        credential_ref = CONNECTION_REF
        status = "active"
        access_token = "must-not-cross"

    unsafe = TokenBearingConnection()
    with pytest.raises(ValueError, match="token-free"):
        AtprotoSidecarLiveAdapter(
            connections={unsafe.id: unsafe},
            sidecar_client=client(no_network),
            certification=certification(frozenset({ActionType.FOLLOW_CREATOR})),
        )


def test_observe_uses_one_opaque_lease_and_only_allowlisted_operations() -> None:
    preferences = [
        {
            "$type": "app.bsky.actor.defs#mutedWordsPref",
            "items": [{"id": "word-one", "value": "spoilers", "targets": ["content"]}],
        }
    ]
    http = QueueHttpClient(
        [
            restore_response("o"),
            operation_response(
                AtprotoSidecarOperation.GET_FOLLOWS,
                {"follows": [{"did": TARGET_DID}], "cursor": "page-two"},
            ),
            operation_response(
                AtprotoSidecarOperation.GET_FOLLOWS,
                {"follows": [{"did": "did:plc:second789"}]},
            ),
            operation_response(
                AtprotoSidecarOperation.GET_MUTES,
                {"mutes": [{"did": "did:plc:muted987"}]},
            ),
            operation_response(
                AtprotoSidecarOperation.GET_PREFERENCES,
                {"preferences": preferences, "observed_sha256": "1" * 64},
            ),
        ]
    )
    live, bound = adapter(http, actions=frozenset({ActionType.MUTE_CREATOR}))

    observed = live.observe(bound.id, now=NOW)

    assert observed.followed_creators == frozenset({TARGET_DID, "did:plc:second789"})
    assert observed.muted_creators == frozenset({"did:plc:muted987"})
    assert observed.muted_keywords == frozenset({"spoilers"})
    operations = [
        call["json"].get("operation")
        for call in http.calls
        if call["url"].endswith("/sessions/execute")
    ]
    assert operations == [
        "graph.get_follows",
        "graph.get_follows",
        "graph.get_mutes",
        "actor.get_preferences",
    ]
    assert sum(call["url"].endswith("/sessions/restore") for call in http.calls) == 1
    assert not http.responses


@pytest.mark.parametrize(
    ("relationship", "present", "operation", "input_data", "result", "remote_ref"),
    (
        (
            "follow",
            True,
            AtprotoSidecarOperation.FOLLOW,
            {"actor": TARGET_DID},
            {
                "uri": f"at://{OWNER_DID}/app.bsky.graph.follow/follow-one",
                "cid": "cid-one",
            },
            None,
        ),
        (
            "follow",
            False,
            AtprotoSidecarOperation.DELETE_FOLLOW,
            {"uri": f"at://{OWNER_DID}/app.bsky.graph.follow/follow-one"},
            {
                "deleted": True,
                "uri": f"at://{OWNER_DID}/app.bsky.graph.follow/follow-one",
            },
            f"at://{OWNER_DID}/app.bsky.graph.follow/follow-one",
        ),
        (
            "mute",
            True,
            AtprotoSidecarOperation.MUTE,
            {"actor": TARGET_DID},
            {"muted": True, "actor": TARGET_DID},
            None,
        ),
        (
            "mute",
            False,
            AtprotoSidecarOperation.UNMUTE,
            {"actor": TARGET_DID},
            {"muted": False, "actor": TARGET_DID},
            None,
        ),
    ),
)
def test_graph_mutations_map_to_exact_sidecar_operations(
    relationship: str,
    present: bool,
    operation: AtprotoSidecarOperation,
    input_data: dict[str, Any],
    result: dict[str, Any],
    remote_ref: str | None,
) -> None:
    http = QueueHttpClient([restore_response("m"), operation_response(operation, result)])
    live, bound = adapter(
        http,
        actions=frozenset(
            {
                ActionType.FOLLOW_CREATOR,
                ActionType.UNFOLLOW_CREATOR,
                ActionType.MUTE_CREATOR,
                ActionType.UNMUTE_CREATOR,
            }
        ),
    )

    live._mutate_to_state(
        bound,
        proposed(ActionType.FOLLOW_CREATOR),
        {"present": present, "target_id": TARGET_DID, "relationship": relationship},
        current_state={
            "present": not present,
            "target_id": TARGET_DID,
            "relationship": relationship,
            "remote_ref": remote_ref,
        },
        now=NOW,
    )

    execute_call = http.calls[1]
    assert execute_call["json"] == {
        "lease_ref": "m" * 43,
        "operation": operation.value,
        "input": input_data,
    }
    assert not http.responses


def test_sidecar_mutation_rechecks_certification_after_session_restore() -> None:
    validated = certification(frozenset({ActionType.FOLLOW_CREATOR}))
    clock = MutableClock(NOW)
    http = ExpiringRestoreHttpClient(
        [restore_response("e")],
        clock=clock,
        expires_at=validated.expires_at,
    )
    bound = FakeConnection()
    live = AtprotoSidecarLiveAdapter(
        connections={bound.id: bound},
        sidecar_client=client(http),
        certification=validated,
        clock=clock,
    )

    with pytest.raises(UnsupportedPlatformAction, match="current validated certification"):
        live._mutate_to_state(
            bound,
            proposed(ActionType.FOLLOW_CREATOR),
            {
                "present": True,
                "target_id": TARGET_DID,
                "relationship": "follow",
            },
            current_state={
                "present": False,
                "target_id": TARGET_DID,
                "relationship": "follow",
                "remote_ref": None,
            },
            now=NOW,
        )

    assert len(http.calls) == 1
    assert not http.responses


def test_muted_word_update_preserves_preferences_and_uses_fresh_digest_guard() -> None:
    original_preferences = [
        {"$type": "app.bsky.actor.defs#adultContentPref", "enabled": False}
    ]
    resulting_preferences = [
        *original_preferences,
        {
            "$type": "app.bsky.actor.defs#mutedWordsPref",
            "items": [{"id": "word-one", "value": "spoilers", "targets": ["content", "tag"]}],
        },
    ]
    http = QueueHttpClient(
        [
            restore_response("a"),
            operation_response(
                AtprotoSidecarOperation.GET_PREFERENCES,
                {"preferences": original_preferences, "observed_sha256": "1" * 64},
            ),
            restore_response("b"),
            operation_response(
                AtprotoSidecarOperation.PUT_PREFERENCES,
                {
                    "updated": True,
                    "external_subject": OWNER_DID,
                    "preferences_sha256": "2" * 64,
                },
            ),
            restore_response("d"),
            operation_response(
                AtprotoSidecarOperation.GET_PREFERENCES,
                {"preferences": resulting_preferences, "observed_sha256": "2" * 64},
            ),
        ]
    )
    live, bound = adapter(http, actions=frozenset({ActionType.MUTE_KEYWORD}))
    action = proposed(ActionType.MUTE_KEYWORD, target="spoilers")

    prepared = live.prepare_remote_action(bound.id, action, now=NOW)
    outcome = live.apply_prepared_action(prepared, now=NOW)

    assert outcome.status is ActionStatus.EXECUTED
    put_call = next(
        call
        for call in http.calls
        if call.get("json", {}).get("operation") == "actor.put_preferences"
    )
    sent = put_call["json"]["input"]
    assert sent["expected_sha256"] == "1" * 64
    assert sent["preferences"][0] == original_preferences[0]
    muted = next(
        item
        for item in sent["preferences"]
        if item["$type"] == "app.bsky.actor.defs#mutedWordsPref"
    )
    assert muted["items"][0]["value"] == "spoilers"
    serialized_outcome = json.dumps(
        {
            "before": dict(outcome.before_state),
            "after": dict(outcome.after_state),
            "reference": outcome.platform_reference,
        }
    )
    assert SECRET not in serialized_outcome
    assert "lease_ref" not in serialized_outcome
    assert not http.responses


def test_unmute_word_uses_put_preferences_without_clobbering_unrelated_items() -> None:
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
        [
            restore_response("u"),
            operation_response(
                AtprotoSidecarOperation.PUT_PREFERENCES,
                {
                    "updated": True,
                    "external_subject": OWNER_DID,
                    "preferences_sha256": "4" * 64,
                },
            ),
        ]
    )
    live, bound = adapter(http, actions=frozenset({ActionType.UNMUTE_KEYWORD}))

    live._mutate_to_state(
        bound,
        proposed(ActionType.UNMUTE_KEYWORD, target="spoilers"),
        {"present": False, "target_id": "spoilers", "relationship": "muted_word"},
        current_state={
            "present": True,
            "target_id": "spoilers",
            "relationship": "muted_word",
            "remote_ref": "remove-me",
            "preferences": preferences,
            "preferences_sha256": "3" * 64,
        },
        now=NOW,
    )

    payload = http.calls[1]["json"]
    assert payload["operation"] == "actor.put_preferences"
    assert payload["input"]["expected_sha256"] == "3" * 64
    sent = payload["input"]["preferences"]
    assert sent[0] == preferences[0]
    muted = next(item for item in sent if item["$type"].endswith("#mutedWordsPref"))
    assert [item["value"] for item in muted["items"]] == ["sports"]
    assert not http.responses


def test_reconcile_observes_without_replaying_a_remote_write() -> None:
    http = QueueHttpClient(
        [
            restore_response("p"),
            operation_response(AtprotoSidecarOperation.GET_FOLLOWS, {"follows": []}),
            restore_response("q"),
            operation_response(AtprotoSidecarOperation.GET_FOLLOWS, {"follows": []}),
        ]
    )
    live, bound = adapter(http, actions=frozenset({ActionType.FOLLOW_CREATOR}))
    prepared = live.prepare_remote_action(
        bound.id,
        proposed(ActionType.FOLLOW_CREATOR),
        now=NOW,
    )

    outcome = live.reconcile_remote_action(prepared, now=NOW)

    assert outcome.status is ActionStatus.FAILED
    assert outcome.error_code == "remote_action_not_applied"
    operations = [
        call["json"].get("operation")
        for call in http.calls
        if call["url"].endswith("/sessions/execute")
    ]
    assert operations == ["graph.get_follows", "graph.get_follows"]
    assert not http.responses


def test_follow_verification_tolerates_bounded_appview_projection_lag() -> None:
    follow_uri = f"at://{OWNER_DID}/app.bsky.graph.follow/follow-lagged"
    present = {"did": TARGET_DID, "viewer": {"following": follow_uri}}
    http = QueueHttpClient(
        [
            restore_response("a"),
            operation_response(AtprotoSidecarOperation.GET_FOLLOWS, {"follows": []}),
            restore_response("b"),
            operation_response(
                AtprotoSidecarOperation.FOLLOW,
                {"uri": follow_uri, "cid": "cid-lagged"},
            ),
            restore_response("c"),
            operation_response(AtprotoSidecarOperation.GET_FOLLOWS, {"follows": []}),
            restore_response("d"),
            operation_response(AtprotoSidecarOperation.GET_FOLLOWS, {"follows": []}),
            restore_response("e"),
            operation_response(AtprotoSidecarOperation.GET_FOLLOWS, {"follows": [present]}),
        ]
    )
    delays: list[float] = []
    bound = FakeConnection()
    live = AtprotoSidecarLiveAdapter(
        connections={bound.id: bound},
        sidecar_client=client(http),
        certification=certification(frozenset({ActionType.FOLLOW_CREATOR})),
        sleeper=delays.append,
    )

    prepared = live.prepare_remote_action(
        bound.id,
        proposed(ActionType.FOLLOW_CREATOR),
        now=NOW,
    )
    outcome = live.apply_prepared_action(prepared, now=NOW)

    assert outcome.status is ActionStatus.EXECUTED
    assert outcome.after_state["remote_ref"] == follow_uri
    assert delays == [0.5, 0.5]
    operations = [
        call["json"].get("operation")
        for call in http.calls
        if call["url"].endswith("/sessions/execute")
    ]
    assert operations.count("graph.follow") == 1
    assert operations == [
        "graph.get_follows",
        "graph.follow",
        "graph.get_follows",
        "graph.get_follows",
        "graph.get_follows",
    ]
    assert not http.responses


def test_post_write_projection_lag_still_fails_closed_when_exhausted() -> None:
    follow_uri = f"at://{OWNER_DID}/app.bsky.graph.follow/follow-unknown"
    http = QueueHttpClient(
        [
            restore_response("a"),
            operation_response(AtprotoSidecarOperation.GET_FOLLOWS, {"follows": []}),
            restore_response("b"),
            operation_response(
                AtprotoSidecarOperation.FOLLOW,
                {"uri": follow_uri, "cid": "cid-unknown"},
            ),
            restore_response("c"),
            operation_response(AtprotoSidecarOperation.GET_FOLLOWS, {"follows": []}),
            restore_response("d"),
            operation_response(AtprotoSidecarOperation.GET_FOLLOWS, {"follows": []}),
            restore_response("e"),
            operation_response(AtprotoSidecarOperation.GET_FOLLOWS, {"follows": []}),
        ]
    )
    delays: list[float] = []
    bound = FakeConnection()
    live = AtprotoSidecarLiveAdapter(
        connections={bound.id: bound},
        sidecar_client=client(http),
        certification=certification(frozenset({ActionType.FOLLOW_CREATOR})),
        sleeper=delays.append,
    )
    live.POST_WRITE_READ_ATTEMPTS = 3
    prepared = live.prepare_remote_action(
        bound.id,
        proposed(ActionType.FOLLOW_CREATOR),
        now=NOW,
    )

    with pytest.raises(RemoteOutcomeUnknown, match="could not be reconciled"):
        live.apply_prepared_action(prepared, now=NOW)

    assert delays == [0.5, 0.5]
    operations = [
        call["json"].get("operation")
        for call in http.calls
        if call["url"].endswith("/sessions/execute")
    ]
    assert operations.count("graph.follow") == 1
    assert not http.responses


def test_rollback_uses_the_inverse_operation_and_verifies_state() -> None:
    follow_uri = f"at://{OWNER_DID}/app.bsky.graph.follow/follow-one"
    present = {"did": TARGET_DID, "viewer": {"following": follow_uri}}
    http = QueueHttpClient(
        [
            restore_response("r"),
            operation_response(AtprotoSidecarOperation.GET_FOLLOWS, {"follows": [present]}),
            restore_response("s"),
            operation_response(
                AtprotoSidecarOperation.DELETE_FOLLOW,
                {"deleted": True, "uri": follow_uri},
            ),
            restore_response("t"),
            operation_response(AtprotoSidecarOperation.GET_FOLLOWS, {"follows": []}),
        ]
    )
    live, bound = adapter(
        http,
        actions=frozenset({ActionType.FOLLOW_CREATOR, ActionType.UNFOLLOW_CREATOR}),
    )
    action = proposed(ActionType.FOLLOW_CREATOR)
    executed = ActionOutcome(
        action=action,
        status=ActionStatus.EXECUTED,
        before_state={"present": False, "target_id": TARGET_DID, "relationship": "follow"},
        after_state={
            "present": True,
            "target_id": TARGET_DID,
            "relationship": "follow",
            "remote_ref": follow_uri,
        },
        executed_at=NOW,
        platform_reference=follow_uri,
    )
    receipt = ActionReceipt(
        id="receipt:bluesky:one",
        passport_id="passport-one",
        passport_version=1,
        destination_id=bound.id,
        outcomes=(executed,),
        issued_at=NOW,
        trace_id="trace-one",
        previous_checkpoint_id=None,
    )

    rolled_back = live.rollback(bound.id, receipt, now=NOW)

    assert rolled_back.restored_actions == (action.id,)
    assert not rolled_back.failed_actions
    operations = [
        call["json"].get("operation")
        for call in http.calls
        if call["url"].endswith("/sessions/execute")
    ]
    assert operations == ["graph.get_follows", "graph.delete_follow", "graph.get_follows"]
    assert not http.responses


def test_client_denies_non_enum_operations_without_a_sidecar_call() -> None:
    http = QueueHttpClient([restore_response("x")])
    bridge = client(http)
    lease = bridge.restore(FakeConnection(), now=NOW)

    with pytest.raises(LivePermissionError, match="allowlisted"):
        bridge.execute(
            lease,
            "com.atproto.repo.applyWrites",  # type: ignore[arg-type]
            {},
            now=NOW,
            mutation=True,
        )

    assert len(http.calls) == 1


@pytest.mark.parametrize(
    ("status", "error_code", "mutation", "expected_type", "unknown"),
    (
        (401, "sidecar_authentication_required", False, LiveAuthenticationError, False),
        (403, "bridge_operation_denied", False, LivePermissionError, False),
        (404, "missing_target", False, LiveTargetNotFound, False),
        (409, "oauth_state_expired", True, LiveProtocolError, False),
        (409, "preferences_changed", True, LiveTransientError, False),
        (429, "sidecar_rate_limited", False, LiveRateLimited, False),
        (503, "bridge_operation_failed", False, LiveTransientError, False),
        (503, "bridge_operation_failed", True, RemoteOutcomeUnknown, True),
    ),
)
def test_sidecar_error_taxonomy_is_sanitized(
    status: int,
    error_code: str,
    mutation: bool,
    expected_type: type[Exception],
    unknown: bool,
) -> None:
    echoed_secret = "must-not-appear-from-sidecar"
    http = QueueHttpClient(
        [
            restore_response("e"),
            FakeResponse(status, {"error": error_code, "detail": echoed_secret}),
        ]
    )
    bridge = client(http)
    lease = bridge.restore(FakeConnection(), now=NOW)

    with pytest.raises(expected_type) as caught:
        bridge.execute(
            lease,
            AtprotoSidecarOperation.FOLLOW,
            {"actor": TARGET_DID},
            now=NOW,
            mutation=mutation,
        )

    assert echoed_secret not in str(caught.value)
    assert SECRET not in str(caught.value)
    assert bool(getattr(caught.value, "outcome_unknown", False)) is unknown


def test_transport_interruptions_distinguish_read_retry_from_unknown_mutation() -> None:
    restore_http = QueueHttpClient([TimeoutError("socket included nothing trustworthy")])
    with pytest.raises(LiveTransientError) as read_error:
        client(restore_http).restore(FakeConnection(), now=NOW)
    assert read_error.value.retryable is True
    assert read_error.value.outcome_unknown is False

    mutation_http = QueueHttpClient([restore_response("z"), TimeoutError("after dispatch")])
    bridge = client(mutation_http)
    lease = bridge.restore(FakeConnection(), now=NOW)
    with pytest.raises(RemoteOutcomeUnknown) as mutation_error:
        bridge.execute(
            lease,
            AtprotoSidecarOperation.FOLLOW,
            {"actor": TARGET_DID},
            now=NOW,
            mutation=True,
        )
    assert mutation_error.value.outcome_unknown is True
    assert mutation_error.value.retryable is False


def test_invalid_success_after_mutation_is_an_unknown_outcome() -> None:
    http = QueueHttpClient(
        [
            restore_response("v"),
            FakeResponse(
                200,
                {
                    "platform": "bluesky",
                    "operation": AtprotoSidecarOperation.FOLLOW.value,
                    "result": "not-an-object",
                },
            ),
        ]
    )
    bridge = client(http)
    lease = bridge.restore(FakeConnection(), now=NOW)

    with pytest.raises(RemoteOutcomeUnknown) as caught:
        bridge.execute(
            lease,
            AtprotoSidecarOperation.FOLLOW,
            {"actor": TARGET_DID},
            now=NOW,
            mutation=True,
        )

    assert caught.value.outcome_unknown is True


@pytest.mark.parametrize("credential_field", ("access_token", "DPoP", "private_key"))
def test_sidecar_response_cannot_smuggle_credential_material(credential_field: str) -> None:
    http = QueueHttpClient(
        [
            restore_response("k"),
            operation_response(
                AtprotoSidecarOperation.GET_PREFERENCES,
                {
                    "preferences": [],
                    "observed_sha256": "1" * 64,
                    credential_field: "blocked",
                },
            ),
        ]
    )
    bridge = client(http)
    lease = bridge.restore(FakeConnection(), now=NOW)

    with pytest.raises(LiveProtocolError) as caught:
        bridge.execute(
            lease,
            AtprotoSidecarOperation.GET_PREFERENCES,
            {},
            now=NOW,
            mutation=False,
        )

    assert "blocked" not in str(caught.value)


def test_sidecar_response_cannot_echo_the_internal_service_secret_under_a_safe_key() -> None:
    http = QueueHttpClient(
        [
            restore_response("j"),
            operation_response(
                AtprotoSidecarOperation.GET_PREFERENCES,
                {
                    "preferences": [],
                    "observed_sha256": "1" * 64,
                    "message": f"unexpected echo: {SECRET}",
                },
            ),
        ]
    )
    bridge = client(http)
    lease = bridge.restore(FakeConnection(), now=NOW)

    with pytest.raises(LiveProtocolError) as caught:
        bridge.execute(
            lease,
            AtprotoSidecarOperation.GET_PREFERENCES,
            {},
            now=NOW,
            mutation=False,
        )

    assert SECRET not in str(caught.value)
