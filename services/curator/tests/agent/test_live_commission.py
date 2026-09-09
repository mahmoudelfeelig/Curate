from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable, AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from strands.models import Model

from feed_passport.adapters.lab.adapter import LabAccountState, LabAdapter
from feed_passport.agent import (
    ConsentBroker,
    ConsentError,
    LiveCommissionPlanner,
    LiveCommissionPlannerError,
)
from feed_passport.application import (
    CuratorApplication,
    InvalidStateError,
    LiveCommissionService,
    LivePriorityMode,
)
from feed_passport.domain import ActionType, CapabilityLevel, OverlayMode, ProposedAction
from feed_passport.domain.models import PlatformCapabilityManifest
from feed_passport.infrastructure import AesGcmKeyring, EncryptedConnectionRegistry, SQLiteStore
from feed_passport.agent.model_provider import ModelExecutionProfile
from feed_passport.ports.live_platform import (
    PreparedRemoteAction,
    RemoteOutcomeUnknown,
    ValidatedLiveCertification,
)


NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
REVISION = "a" * 40
OWNER_ID = "private-owner-do-not-send"
CONNECTION_ID = "private-connection-do-not-send"
EXTERNAL_SUBJECT = "private-channel-do-not-send"
CREDENTIAL_REF = "private-credential-reference-do-not-send"
POSITIVE_CREATOR = "private-positive-creator-do-not-send"
NEGATIVE_CREATOR = "private-negative-creator-do-not-send"
PRIVATE_INTENT = "private-intent-canary-do-not-send"
PRIVATE_TOPIC = "private-topic-canary-do-not-send"
PRIVATE_EXCLUSION = "private-exclusion-canary-do-not-send"


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


class SimulatedProcessDeath(BaseException):
    """Bypass in-process exception cleanup to model an abrupt worker exit."""


def certification(*, expires_at: datetime | None = None) -> ValidatedLiveCertification:
    actions = frozenset({ActionType.FOLLOW_CREATOR, ActionType.MUTE_CREATOR})
    return ValidatedLiveCertification(
        platform="youtube",
        certified_at=NOW,
        expires_at=expires_at or NOW + timedelta(days=14),
        code_revision=REVISION,
        execute=actions,
        observe=frozenset({"authorized_controls"}),
        verify=frozenset({"authorized_controls"}),
        rollback=actions,
        receipt_ref="conformance:youtube:dummy-001",
        evidence_sha256="b" * 64,
    )


class CertifiedLocalLiveAdapter(LabAdapter):
    platform = "youtube"

    def __init__(self, validated: ValidatedLiveCertification) -> None:
        super().__init__(
            accounts=(
                LabAccountState(
                    id=CONNECTION_ID,
                    topic_affinity={"research": 1.0},
                    serendipity=0.2,
                    source_cap=0.4,
                ),
            )
        )
        self.certification = validated
        self.apply_calls = 0
        self.applied_targets: list[str] = []
        self.lose_next_response = False

    @property
    def validated_live_certification(self) -> ValidatedLiveCertification:
        return self.certification

    def capabilities(self, account_id: str) -> PlatformCapabilityManifest:
        self._require_account(account_id)
        actions = self.certification.execute
        return PlatformCapabilityManifest(
            platform=self.platform,
            level=CapabilityLevel.EXECUTABLE,
            observe=self.certification.observe,
            execute=actions,
            verify=self.certification.verify,
            rollback=self.certification.rollback,
            evidence_url="https://example.test/certified-dummy-evidence",
            certified_at=self.certification.certified_at,
        )

    def prepare_remote_action(self, account_id, action, *, now):
        preview = self.clone()
        expected = preview.execute(account_id, action, now=now)
        return PreparedRemoteAction(
            platform=self.platform,
            connection_id=account_id,
            action=action,
            before_state=expected.before_state,
            desired_state=expected.after_state,
            prepared_at=now,
        )

    def apply_prepared_action(self, prepared, *, now):
        self.apply_calls += 1
        self.applied_targets.append(prepared.action.target)
        outcome = super().execute(prepared.connection_id, prepared.action, now=now)
        if self.lose_next_response:
            self.lose_next_response = False
            raise RemoteOutcomeUnknown(
                platform=self.platform,
                code="simulated_response_lost",
                detail="The local test transport lost its response.",
                outcome_unknown=True,
            )
        return outcome

    def reconcile_remote_action(self, prepared, *, now):
        return super().execute(prepared.connection_id, prepared.action, now=now)


class ScriptedLivePlannerModel(Model):
    def __init__(self, proposal: dict[str, Any]) -> None:
        self.proposal = proposal
        self.seen_messages: list[str] = []
        self.seen_tool_names: list[set[str]] = []
        self.seen_tool_results: list[list[dict[str, Any]]] = []
        self._config: dict[str, Any] = {
            "model_id": "scripted-live-planner",
            "context_window_limit": 4096,
        }

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return dict(self._config)

    async def structured_output(
        self,
        output_model: type[Any],
        prompt: list[dict[str, Any]],
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        if False:
            yield {"output": output_model()}

    def stream(
        self,
        messages: list[dict[str, Any]],
        tool_specs: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[dict[str, Any]]:
        self.seen_messages.append(json.dumps(messages, sort_keys=True, default=str))
        self.seen_tool_names.append({str(item["name"]) for item in tool_specs or []})
        results = [
            block["toolResult"]
            for message in messages
            for block in message.get("content", ())
            if "toolResult" in block
        ]
        self.seen_tool_results.append(results)
        if len(results) == 0:
            return self._tool_call("inspect_redacted_demand", {}, "shape-1")
        if len(results) == 1:
            return self._tool_call(
                "inspect_certified_action_families", {}, "families-1"
            )
        if len(results) == 2:
            return self._tool_call(
                "submit_live_family_priority", self.proposal, "proposal-1"
            )
        return self._text("The exact proposal is ready for separate human review.")

    async def _tool_call(
        self, name: str, values: dict[str, Any], tool_use_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        yield {"messageStart": {"role": "assistant"}}
        yield {
            "contentBlockStart": {
                "start": {"toolUse": {"toolUseId": tool_use_id, "name": name}}
            }
        }
        yield {
            "contentBlockDelta": {
                "delta": {"toolUse": {"input": json.dumps(values)}}
            }
        }
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 20, "outputTokens": 8, "totalTokens": 28},
                "metrics": {"latencyMs": 1},
            }
        }

    async def _text(self, value: str) -> AsyncIterator[dict[str, Any]]:
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {}}}
        yield {"contentBlockDelta": {"delta": {"text": value}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
                "metrics": {"latencyMs": 1},
            }
        }


class CloseFailingLivePlannerModel(ScriptedLivePlannerModel):
    async def aclose(self) -> None:
        raise RuntimeError("local model close failed")


def valid_proposal(*, actions: list[str] | None = None) -> dict[str, Any]:
    return {
        "prioritized_action_types": actions
        or ["follow_creator", "mute_creator"],
    }


@dataclass
class Harness:
    store: SQLiteStore
    registry: EncryptedConnectionRegistry
    adapter: CertifiedLocalLiveAdapter
    application: CuratorApplication
    service: LiveCommissionService
    broker: ConsentBroker
    passport_id: str
    clock: MutableClock


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    clock = MutableClock(NOW + timedelta(minutes=1))
    store = SQLiteStore(tmp_path / "live-commission.db")
    registry = EncryptedConnectionRegistry(
        store,
        AesGcmKeyring(
            active_key_id="connection-v1",
            keys={"connection-v1": b"c" * 32},
            index_key=b"i" * 32,
        ),
        id_factory=lambda: CONNECTION_ID,
    )
    registry.register_connection(
        owner_id=OWNER_ID,
        platform="youtube",
        external_subject=EXTERNAL_SUBJECT,
        credential_ref=CREDENTIAL_REF,
        metadata={"granted_scopes": ["dummy-control"]},
        now=clock(),
    )
    adapter = CertifiedLocalLiveAdapter(certification())
    identifiers = (f"id-{index}" for index in range(1000))
    application = CuratorApplication(
        store=store,
        adapters={"youtube": adapter},
        connections=registry,
        action_journal=store,
        clock=clock,
        id_factory=identifiers.__next__,
    )
    passport = application.create_passport(
        owner_id=OWNER_ID,
        name="Private dummy Passport",
        intent=PRIVATE_INTENT,
        topic_targets={PRIVATE_TOPIC: 1.0},
        creator_preferences={POSITIVE_CREATOR: 1.0, NEGATIVE_CREATOR: -1.0},
        hard_exclusions=frozenset({PRIVATE_EXCLUSION}),
        serendipity=0.2,
        max_source_share=0.4,
    )
    broker = ConsentBroker(application, secret="live-commission-test-secret", clock=clock)
    service = LiveCommissionService(application, consent_broker=broker)
    result = Harness(
        store=store,
        registry=registry,
        adapter=adapter,
        application=application,
        service=service,
        broker=broker,
        passport_id=passport.id,
        clock=clock,
    )
    try:
        yield result
    finally:
        store.close()


def finalize_commission(harness: Harness) -> dict[str, Any]:
    candidate = harness.service.prepare_candidate(
        actor_id=OWNER_ID,
        priority_mode=LivePriorityMode.BALANCED,
        passport_id=harness.passport_id,
        platform="youtube",
        destination_connection_id=CONNECTION_ID,
        max_total_actions=2,
    )
    return harness.service.finalize_candidate(
        candidate.commission_id,
        actor_id=OWNER_ID,
        prioritized_action_types=(
            ActionType.FOLLOW_CREATOR,
            ActionType.MUTE_CREATOR,
        ),
        planner_evidence={"authority": "priority_only", "priority": {}},
    )


def issue_live_grant(harness: Harness, commission_id: str) -> str:
    grant = harness.broker.issue_for_live_commission(
        commission_id, actor_id=OWNER_ID
    )
    return grant.token


def test_model_sees_only_redacted_precompiled_families_then_exact_plan_executes(
    harness: Harness,
) -> None:
    model = ScriptedLivePlannerModel(valid_proposal())
    planner = LiveCommissionPlanner(
        harness.service,
        model_factory=lambda: model,
        provider="scripted_local",
        model_id="scripted-live-planner",
        timeout_seconds=5,
        endpoint_scope="scripted_no_network",
    )

    commission = asyncio.run(
        planner.preview(
            actor_id=OWNER_ID,
            priority_mode=LivePriorityMode.CREATOR_CONTINUITY_FIRST,
            passport_id=harness.passport_id,
            platform="youtube",
            destination_connection_id=CONNECTION_ID,
            max_total_actions=1,
        )
    )

    assert commission["status"] == "awaiting_approval"
    assert commission["selected_action_types"] == ["follow_creator"]
    assert commission["priority_mode"] == "creator_continuity_first"
    assert "refined_goal" not in json.dumps(commission)
    exact_actions = commission["approval_scope"]["executable_plan"]["actions"]
    assert len(exact_actions) == 1
    assert exact_actions[0]["target"] == POSITIVE_CREATOR
    assert model.seen_tool_names[0] == {
        "inspect_redacted_demand",
        "inspect_certified_action_families",
        "submit_live_family_priority",
    }
    model_context = "\n".join(model.seen_messages)
    for private_value in (
        OWNER_ID,
        CONNECTION_ID,
        EXTERNAL_SUBJECT,
        CREDENTIAL_REF,
        harness.passport_id,
        PRIVATE_INTENT,
        PRIVATE_TOPIC,
        PRIVATE_EXCLUSION,
        POSITIVE_CREATOR,
        NEGATIVE_CREATOR,
        harness.adapter.certification.receipt_ref,
        harness.adapter.certification.evidence_sha256,
        harness.adapter.certification.code_revision,
    ):
        assert private_value not in model_context

    def find_schema(value: Any, schema: str) -> dict[str, Any] | None:
        if isinstance(value, dict):
            if value.get("schema") == schema:
                return value
            for item in value.values():
                found = find_schema(item, schema)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = find_schema(item, schema)
                if found is not None:
                    return found
        elif isinstance(value, str) and schema in value:
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return None
            return find_schema(decoded, schema)
        return None

    messages = [json.loads(item) for item in model.seen_messages]
    demand = next(
        found
        for message in messages
        if (found := find_schema(message, "feed-passport-live-demand/v1"))
    )
    families = next(
        found
        for message in messages
        if (found := find_schema(message, "feed-passport-live-families/v1"))
    )
    assert set(demand) == {"schema", "priority_mode", "demand_shape"}
    assert set(families) == {
        "schema",
        "platform_family",
        "families",
        "locked_max_total_actions",
        "exact_targets_withheld",
        "authority",
    }

    migration = harness.application.projection_get(
        "migrations", commission["migration_id"]
    )
    assert migration["execution_authority"] == "agent_live_commission_only"
    with pytest.raises(ConsentError, match="exact live commission"):
        harness.broker.issue_for_migration(
            commission["migration_id"], actor_id=OWNER_ID
        )

    grant = issue_live_grant(harness, commission["id"])
    result = harness.service.execute(
        commission["id"],
        actor_id=OWNER_ID,
        approval_token=grant,
    )

    assert result["receipt_id"]
    assert result["migration"]["receipt_id"] == result["receipt_id"]
    assert harness.adapter.apply_calls == 1
    assert POSITIVE_CREATOR in harness.adapter._accounts[CONNECTION_ID].following
    assert NEGATIVE_CREATOR not in harness.adapter._accounts[CONNECTION_ID].muted_creators

    rollback_grant = harness.broker.issue_for_rollback(
        result["receipt_id"], actor_id=OWNER_ID, platform="youtube"
    )
    harness.broker.consume(
        rollback_grant.token,
        operation="rollback_receipt",
        resource_id=result["receipt_id"],
        actor_id=OWNER_ID,
    )
    rollback = harness.application.rollback_receipt(
        result["receipt_id"], actor_id=OWNER_ID, platform="youtube"
    )
    assert rollback["status"] == "rolled_back"
    assert POSITIVE_CREATOR not in harness.adapter._accounts[CONNECTION_ID].following


def test_model_cannot_select_public_engagement_or_expand_the_candidate(
    harness: Harness,
) -> None:
    model = ScriptedLivePlannerModel(valid_proposal(actions=["like"]))
    planner = LiveCommissionPlanner(
        harness.service,
        model_factory=lambda: model,
        provider="scripted_local",
        model_id="scripted-live-planner",
        timeout_seconds=5,
        endpoint_scope="scripted_no_network",
    )

    with pytest.raises(LiveCommissionPlannerError):
        asyncio.run(
            planner.preview(
                actor_id=OWNER_ID,
                priority_mode=LivePriorityMode.BALANCED,
                passport_id=harness.passport_id,
                platform="youtube",
                destination_connection_id=CONNECTION_ID,
                max_total_actions=20,
            )
        )

    commissions = harness.service.list(actor_id=OWNER_ID)
    assert len(commissions) == 1
    assert commissions[0]["status"] == "planning_failed"
    assert harness.adapter.apply_calls == 0


def test_model_submission_schema_rejects_authored_prose(harness: Harness) -> None:
    model = ScriptedLivePlannerModel(
        {**valid_proposal(), "rationale": "Trust this model-authored claim."}
    )
    planner = LiveCommissionPlanner(
        harness.service,
        model_factory=lambda: model,
        provider="scripted_local",
        model_id="scripted-live-planner",
        timeout_seconds=5,
        endpoint_scope="scripted_no_network",
    )

    with pytest.raises(LiveCommissionPlannerError):
        asyncio.run(
            planner.preview(
                actor_id=OWNER_ID,
                priority_mode=LivePriorityMode.BALANCED,
                passport_id=harness.passport_id,
                platform="youtube",
                destination_connection_id=CONNECTION_ID,
                max_total_actions=2,
            )
        )
    assert harness.service.list(actor_id=OWNER_ID)[0]["status"] == "planning_failed"
    assert harness.adapter.apply_calls == 0


def test_model_factory_failure_closes_candidate_lifecycle(harness: Harness) -> None:
    def failing_factory():
        raise RuntimeError("local runtime unavailable")

    planner = LiveCommissionPlanner(
        harness.service,
        model_factory=failing_factory,
        provider="scripted_local",
        model_id="missing-local-model",
        timeout_seconds=5,
        endpoint_scope="scripted_no_network",
    )

    with pytest.raises(LiveCommissionPlannerError, match="RuntimeError"):
        asyncio.run(
            planner.preview(
                actor_id=OWNER_ID,
                priority_mode=LivePriorityMode.BALANCED,
                passport_id=harness.passport_id,
                platform="youtube",
                destination_connection_id=CONNECTION_ID,
                max_total_actions=2,
            )
        )

    commissions = harness.service.list(actor_id=OWNER_ID)
    assert len(commissions) == 1
    assert commissions[0]["status"] == "planning_failed"
    assert harness.adapter.apply_calls == 0


def test_model_close_failure_closes_candidate_lifecycle(harness: Harness) -> None:
    model = CloseFailingLivePlannerModel(valid_proposal())
    planner = LiveCommissionPlanner(
        harness.service,
        model_factory=lambda: model,
        provider="scripted_local",
        model_id="scripted-live-planner",
        timeout_seconds=5,
        endpoint_scope="scripted_no_network",
    )

    with pytest.raises(LiveCommissionPlannerError, match="could not close safely"):
        asyncio.run(
            planner.preview(
                actor_id=OWNER_ID,
                priority_mode=LivePriorityMode.BALANCED,
                passport_id=harness.passport_id,
                platform="youtube",
                destination_connection_id=CONNECTION_ID,
                max_total_actions=2,
            )
        )

    assert harness.service.list(actor_id=OWNER_ID)[0]["status"] == "planning_failed"
    assert harness.adapter.apply_calls == 0


def test_sealing_failure_closes_candidate_lifecycle(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = ScriptedLivePlannerModel(valid_proposal())
    planner = LiveCommissionPlanner(
        harness.service,
        model_factory=lambda: model,
        provider="scripted_local",
        model_id="scripted-live-planner",
        timeout_seconds=5,
        endpoint_scope="scripted_no_network",
    )

    def fail_to_finalize(*_args, **_kwargs):
        raise RuntimeError("simulated sealing failure")

    monkeypatch.setattr(harness.service, "finalize_candidate", fail_to_finalize)
    with pytest.raises(LiveCommissionPlannerError, match="could not be sealed safely"):
        asyncio.run(
            planner.preview(
                actor_id=OWNER_ID,
                priority_mode=LivePriorityMode.BALANCED,
                passport_id=harness.passport_id,
                platform="youtube",
                destination_connection_id=CONNECTION_ID,
                max_total_actions=2,
            )
        )

    assert harness.service.list(actor_id=OWNER_ID)[0]["status"] == "planning_failed"
    assert harness.adapter.apply_calls == 0


def test_family_priority_changes_the_exact_plan_when_budget_is_binding(
    harness: Harness,
) -> None:
    def exact_target(order: tuple[ActionType, ...]) -> str:
        candidate = harness.service.prepare_candidate(
            actor_id=OWNER_ID,
            priority_mode=LivePriorityMode.BALANCED,
            passport_id=harness.passport_id,
            platform="youtube",
            destination_connection_id=CONNECTION_ID,
            max_total_actions=1,
        )
        commission = harness.service.finalize_candidate(
            candidate.commission_id,
            actor_id=OWNER_ID,
            prioritized_action_types=order,
            planner_evidence={"authority": "priority_only", "priority": {}},
        )
        return str(commission["approval_scope"]["executable_plan"]["actions"][0]["target"])

    assert exact_target((ActionType.MUTE_CREATOR, ActionType.FOLLOW_CREATOR)) == (
        NEGATIVE_CREATOR
    )
    assert exact_target((ActionType.FOLLOW_CREATOR, ActionType.MUTE_CREATOR)) == (
        POSITIVE_CREATOR
    )


def test_model_demand_buckets_use_the_same_effective_passport_as_compilation(
    harness: Harness,
) -> None:
    harness.application.create_overlay(
        base_passport_id=harness.passport_id,
        name="Temporary protective controls",
        topic_adjustments={},
        add_exclusions=frozenset(f"overlay-exclusion-{index}" for index in range(8)),
        remove_exclusions=frozenset(),
        starts_at=harness.clock(),
        expires_at=harness.clock() + timedelta(hours=2),
        mode=OverlayMode.ISOLATED,
        serendipity=None,
        max_outrage=None,
        actor_id=OWNER_ID,
    )

    candidate = harness.service.prepare_candidate(
        actor_id=OWNER_ID,
        priority_mode=LivePriorityMode.PROTECTIVE_CONTROLS_FIRST,
        passport_id=harness.passport_id,
        platform="youtube",
        destination_connection_id=CONNECTION_ID,
        max_total_actions=2,
    )

    assert candidate.demand_shape["hard_exclusion_count_bucket"] == "high"
    commission = harness.service.get(candidate.commission_id, actor_id=OWNER_ID)
    migration = harness.application.projection_get(
        "migrations",
        commission["migration_id"],
    )
    effective = harness.application.effective_passport(harness.passport_id)
    assert migration["effective_passport_fingerprint"] == (
        harness.application._passport_fingerprint(effective)
    )


@pytest.mark.parametrize(
    "priority",
    (
        (ActionType.FOLLOW_CREATOR,),
        (ActionType.FOLLOW_CREATOR, ActionType.FOLLOW_CREATOR),
    ),
)
def test_family_priority_requires_an_exact_unique_permutation(
    harness: Harness,
    priority: tuple[ActionType, ...],
) -> None:
    candidate = harness.service.prepare_candidate(
        actor_id=OWNER_ID,
        priority_mode=LivePriorityMode.PROTECTIVE_CONTROLS_FIRST,
        passport_id=harness.passport_id,
        platform="youtube",
        destination_connection_id=CONNECTION_ID,
        max_total_actions=2,
    )

    with pytest.raises(InvalidStateError, match="exact unique permutation"):
        harness.service.finalize_candidate(
            candidate.commission_id,
            actor_id=OWNER_ID,
            prioritized_action_types=priority,
            planner_evidence={"authority": "priority_only", "priority": {}},
        )
    assert harness.adapter.apply_calls == 0


def test_concurrent_finalizers_cannot_substitute_a_different_exact_scope(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = harness.service.prepare_candidate(
        actor_id=OWNER_ID,
        priority_mode=LivePriorityMode.BALANCED,
        passport_id=harness.passport_id,
        platform="youtube",
        destination_connection_id=CONNECTION_ID,
        max_total_actions=1,
    )
    barrier = Barrier(2)
    original_claim = harness.service._claim_candidate_finalization

    def synchronized_claim(*args, **kwargs):
        barrier.wait(timeout=5)
        return original_claim(*args, **kwargs)

    monkeypatch.setattr(
        harness.service,
        "_claim_candidate_finalization",
        synchronized_claim,
    )

    def finalize(order: tuple[ActionType, ...]):
        try:
            return harness.service.finalize_candidate(
                candidate.commission_id,
                actor_id=OWNER_ID,
                prioritized_action_types=order,
                planner_evidence={"authority": "priority_only", "priority": {}},
            )
        except Exception as exc:  # noqa: BLE001 - assert exact concurrent outcome below
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            finalize,
            (ActionType.FOLLOW_CREATOR, ActionType.MUTE_CREATOR),
        )
        second = executor.submit(
            finalize,
            (ActionType.MUTE_CREATOR, ActionType.FOLLOW_CREATOR),
        )
        outcomes = (first.result(timeout=10), second.result(timeout=10))

    completed = [value for value in outcomes if isinstance(value, dict)]
    rejected = [value for value in outcomes if isinstance(value, InvalidStateError)]
    assert len(completed) == 1
    assert len(rejected) == 1
    assert "already claimed" in str(rejected[0])
    stored = harness.service.get(candidate.commission_id, actor_id=OWNER_ID)
    assert stored["approval_scope"] == completed[0]["approval_scope"]
    migration = harness.application.projection_get(
        "migrations",
        stored["migration_id"],
    )
    assert migration["plan"] == stored["approval_scope"]["executable_plan"]
    assert harness.adapter.apply_calls == 0


@pytest.mark.parametrize("crash_point", ("before_migration_seal", "after_migration_seal"))
def test_finalization_recovers_the_durable_exact_scope_across_process_death(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    crash_point: str,
) -> None:
    candidate = harness.service.prepare_candidate(
        actor_id=OWNER_ID,
        priority_mode=LivePriorityMode.BALANCED,
        passport_id=harness.passport_id,
        platform="youtube",
        destination_connection_id=CONNECTION_ID,
        max_total_actions=1,
    )
    original_seal = harness.service._record_sealed_migration_plan
    original_record = harness.service._record

    if crash_point == "before_migration_seal":
        def crash_before_seal(*_args, **_kwargs):
            raise SimulatedProcessDeath("worker exited before the migration seal")

        monkeypatch.setattr(
            harness.service,
            "_record_sealed_migration_plan",
            crash_before_seal,
        )
    else:
        def crash_after_seal(*args, **kwargs):
            if kwargs.get("event_type") == "agent_live_commission.previewed":
                raise SimulatedProcessDeath("worker exited after the migration seal")
            return original_record(*args, **kwargs)

        monkeypatch.setattr(harness.service, "_record", crash_after_seal)

    with pytest.raises(SimulatedProcessDeath):
        harness.service.finalize_candidate(
            candidate.commission_id,
            actor_id=OWNER_ID,
            prioritized_action_types=(
                ActionType.FOLLOW_CREATOR,
                ActionType.MUTE_CREATOR,
            ),
            planner_evidence={"authority": "priority_only", "priority": {"first": "follow"}},
        )

    claimed = harness.service.get(candidate.commission_id, actor_id=OWNER_ID)
    assert claimed["status"] == "sealing"
    assert claimed["sealing_target_status"] == "awaiting_approval"
    assert claimed["approval_scope"]["prioritized_action_types"] == [
        "follow_creator",
        "mute_creator",
    ]
    migration_before_recovery = harness.application.projection_get(
        "migrations", claimed["migration_id"]
    )
    if crash_point == "before_migration_seal":
        assert migration_before_recovery.get(
            "agent_live_commission_plan_fingerprint"
        ) is None
    else:
        assert migration_before_recovery["agent_live_commission_plan_fingerprint"] == (
            claimed["sealed_plan_fingerprint"]
        )

    monkeypatch.setattr(
        harness.service,
        "_record_sealed_migration_plan",
        original_seal,
    )
    monkeypatch.setattr(harness.service, "_record", original_record)
    restarted = LiveCommissionService(
        harness.application,
        consent_broker=harness.broker,
    )
    recovered = restarted.finalize_candidate(
        candidate.commission_id,
        actor_id=OWNER_ID,
        # A later caller cannot substitute a different model ordering. The
        # durable sealing claim, not these retry arguments, is authoritative.
        prioritized_action_types=(
            ActionType.MUTE_CREATOR,
            ActionType.FOLLOW_CREATOR,
        ),
        planner_evidence={"authority": "different_retry_payload"},
    )

    assert recovered["status"] == "awaiting_approval"
    assert "sealing_target_status" not in recovered
    assert recovered["approval_scope"] == claimed["approval_scope"]
    assert recovered["planner_evidence"] == claimed["planner_evidence"]
    assert recovered["approval_scope"]["executable_plan"]["actions"][0]["target"] == (
        POSITIVE_CREATOR
    )
    sealed = harness.application.projection_get("migrations", recovered["migration_id"])
    assert sealed["plan"] == recovered["approval_scope"]["executable_plan"]
    assert sealed["agent_live_commission_plan_fingerprint"] == (
        recovered["sealed_plan_fingerprint"]
    )
    assert harness.adapter.apply_calls == 0


def test_live_priority_planner_rejects_external_model_profile_before_state_change(
    harness: Harness,
) -> None:
    model_factory_called = False
    before_migrations = tuple(harness.application.projection_list("migrations"))

    def model_factory():
        nonlocal model_factory_called
        model_factory_called = True
        return ScriptedLivePlannerModel(valid_proposal())

    with pytest.raises(ValueError, match="local-only"):
        LiveCommissionPlanner(
            harness.service,
            model_factory=model_factory,
            provider="bedrock",
            model_id="external-model",
            timeout_seconds=5,
            endpoint_scope="aws_bedrock",
            execution_profile=ModelExecutionProfile(
                provider="bedrock",
                model_id="external-model",
                endpoint_scope="aws_bedrock",
                external_model_calls=True,
                paid_model_calls=True,
            ),
        )

    assert model_factory_called is False
    assert tuple(harness.application.projection_list("migrations")) == before_migrations


def test_pre_marker_generic_consent_cannot_cross_live_commission_binding(
    harness: Harness,
) -> None:
    migration = harness.application.prepare_migration(
        passport_id=harness.passport_id,
        platform="youtube",
        destination_account_id=CONNECTION_ID,
        actor_id=OWNER_ID,
    )
    grant = harness.broker.issue_for_migration(migration["id"], actor_id=OWNER_ID)
    marked = dict(migration)
    marked.update(
        {
            "agent_live_commission_id": "commission-race",
            "execution_authority": "agent_live_commission_only",
        }
    )
    harness.service._record_migration_marker(marked, actor_id=OWNER_ID)

    with pytest.raises(ConsentError, match="approved resource changed"):
        harness.broker.consume(
            grant.token,
            operation="execute_migration",
            resource_id=migration["id"],
            actor_id=OWNER_ID,
        )
    with pytest.raises(InvalidStateError, match="exact live commission"):
        harness.application.execute_migration(
            migration["id"],
            approved_by=OWNER_ID,
        )
    assert harness.adapter.apply_calls == 0


def test_generic_migration_cannot_execute_a_live_adapter(
    harness: Harness,
) -> None:
    migration = harness.application.prepare_migration(
        passport_id=harness.passport_id,
        platform="youtube",
        destination_account_id=CONNECTION_ID,
        actor_id=OWNER_ID,
    )

    with pytest.raises(InvalidStateError, match="exact live commission approval"):
        harness.application.execute_migration(
            migration["id"],
            approved_by=OWNER_ID,
        )
    assert harness.adapter.apply_calls == 0


def test_specialized_application_call_requires_consumed_exact_consent(
    harness: Harness,
) -> None:
    commission = finalize_commission(harness)
    scope = commission["approval_scope"]

    with pytest.raises(InvalidStateError, match="consumed one-time approval"):
        harness.application.execute_migration(
            commission["migration_id"],
            approved_by=OWNER_ID,
            max_total_actions=int(scope["max_total_actions"]),
            allowed_action_types=frozenset(
                ActionType(value) for value in scope["allowed_action_types"]
            ),
            execution_authority=f"agent_live_commission:{commission['id']}",
        )

    assert harness.adapter.apply_calls == 0


@pytest.mark.parametrize(
    "drift", ("connection", "passport", "certification", "code_revision")
)
def test_execution_fails_closed_when_bound_live_context_drifts(
    harness: Harness, drift: str
) -> None:
    commission = finalize_commission(harness)
    grant = issue_live_grant(harness, commission["id"])
    if drift == "connection":
        harness.registry.update_connection(
            CONNECTION_ID,
            owner_id=OWNER_ID,
            expected_version=1,
            now=harness.clock(),
            metadata={"granted_scopes": ["dummy-control"], "rotation": 2},
        )
    elif drift == "passport":
        harness.application.revise_passport(
            harness.passport_id,
            actor_id=OWNER_ID,
            changes={"intent": "A changed intent requires a fresh exact preview."},
        )
    elif drift == "certification":
        harness.adapter.certification = replace(
            harness.adapter.certification,
            evidence_sha256="c" * 64,
            receipt_ref="conformance:youtube:dummy-002",
        )
    else:
        harness.adapter.certification = replace(
            harness.adapter.certification,
            code_revision="d" * 40,
        )

    with pytest.raises(InvalidStateError):
        harness.service.execute(
            commission["id"],
            actor_id=OWNER_ID,
            approval_token=grant,
        )

    assert harness.adapter.apply_calls == 0
    assert harness.service.get(commission["id"], actor_id=OWNER_ID)["status"] == "stale"


def test_execution_rechecks_certification_expiry_after_approval(harness: Harness) -> None:
    commission = finalize_commission(harness)
    grant = issue_live_grant(harness, commission["id"])
    harness.clock.value = harness.adapter.certification.expires_at

    with pytest.raises(InvalidStateError, match="not currently active"):
        harness.service.execute(
            commission["id"],
            actor_id=OWNER_ID,
            approval_token=grant,
        )

    assert harness.adapter.apply_calls == 0
    assert harness.service.get(commission["id"], actor_id=OWNER_ID)["status"] == "stale"


def test_execution_rejects_exact_migration_plan_drift_after_consent(harness: Harness) -> None:
    commission = finalize_commission(harness)
    grant = issue_live_grant(harness, commission["id"])
    migration_id = commission["migration_id"]
    current = harness.store.get_projection("migrations", migration_id)
    assert current is not None
    migration = dict(current[1])
    migration["plan"]["actions"][0]["target"] = "tampered-target"
    harness.store.append_event(
        aggregate_id=migration_id,
        aggregate_type="migration",
        expected_version=current[0],
        event_type="migration.test_plan_tampered",
        payload={"test": True},
        actor_id=OWNER_ID,
        trace_id="test-plan-tamper",
        occurred_at=harness.clock(),
        projection_kind="migrations",
        projection=migration,
    )

    with pytest.raises(InvalidStateError, match="exact migration plan"):
        harness.service.execute(
            commission["id"],
            actor_id=OWNER_ID,
            approval_token=grant,
        )

    assert harness.adapter.apply_calls == 0
    assert harness.service.get(commission["id"], actor_id=OWNER_ID)["status"] == "stale"


def test_one_time_consent_is_bound_to_the_exact_commission_scope(harness: Harness) -> None:
    commission = finalize_commission(harness)
    grant = harness.broker.issue_for_live_commission(
        commission["id"], actor_id=OWNER_ID
    )
    current = harness.store.get_projection(
        "agent_live_commissions", commission["id"]
    )
    assert current is not None
    changed = dict(current[1])
    changed["approval_scope"]["connection_version"] = 99
    harness.store.append_event(
        aggregate_id=commission["id"],
        aggregate_type="agent_live_commission",
        expected_version=current[0],
        event_type="agent_live_commission.test_scope_tampered",
        payload={"test": True},
        actor_id=OWNER_ID,
        trace_id="test-scope-tamper",
        occurred_at=harness.clock(),
        projection_kind="agent_live_commissions",
        projection=changed,
    )

    with pytest.raises(ConsentError, match="approved resource changed"):
        harness.broker.consume(
            grant.token,
            operation="execute_agent_live_commission",
            resource_id=commission["id"],
            actor_id=OWNER_ID,
        )


def test_uncertain_remote_write_returns_reconciliation_state_without_fake_receipt(
    harness: Harness,
) -> None:
    commission = finalize_commission(harness)
    harness.adapter.lose_next_response = True
    grant = issue_live_grant(harness, commission["id"])

    uncertain = harness.service.execute(
        commission["id"],
        actor_id=OWNER_ID,
        approval_token=grant,
    )

    assert uncertain["status"] == "reconciliation_required"
    assert uncertain["receipt_id"] is None
    assert uncertain["migration"]["status"] == "reconciliation_required"
    reconciled = harness.service.reconcile(commission["id"], actor_id=OWNER_ID)
    assert reconciled["status"] == "failed_recoverable"
    assert reconciled["receipt_id"] is None

    retry_grant = issue_live_grant(harness, commission["id"])
    completed = harness.service.execute(
        commission["id"],
        actor_id=OWNER_ID,
        approval_token=retry_grant,
    )
    assert completed["receipt_id"]
    assert harness.adapter.apply_calls == 2
    assert harness.adapter.applied_targets.count(POSITIVE_CREATOR) == 1
    assert harness.adapter.applied_targets.count(NEGATIVE_CREATOR) == 1


def test_pristine_awaiting_commission_cannot_enter_recovery_without_durable_evidence(
    harness: Harness,
) -> None:
    commission = finalize_commission(harness)

    with pytest.raises(InvalidStateError, match="no durable post-consent"):
        harness.service.reconcile(commission["id"], actor_id=OWNER_ID)

    assert harness.service.get(commission["id"], actor_id=OWNER_ID)["status"] == (
        "awaiting_approval"
    )
    assert harness.adapter.apply_calls == 0


def test_recovery_rejects_a_durable_attempt_outside_the_exact_sealed_plan(
    harness: Harness,
) -> None:
    commission = finalize_commission(harness)
    harness.adapter.lose_next_response = True
    uncertain = harness.service.execute(
        commission["id"],
        actor_id=OWNER_ID,
        approval_token=issue_live_grant(harness, commission["id"]),
    )
    assert uncertain["status"] == "reconciliation_required"
    forged_action = ProposedAction(
        id="forged-action-outside-sealed-plan",
        destination_id=CONNECTION_ID,
        action_type=ActionType.FOLLOW_CREATOR,
        target="forged-target-outside-sealed-plan",
        reason="Exercise the immutable recovery binding.",
        idempotency_key="forged-idempotency-outside-sealed-plan",
        reversible=True,
    )
    forged_prepared = PreparedRemoteAction(
        platform="youtube",
        connection_id=CONNECTION_ID,
        action=forged_action,
        before_state={"present": False},
        desired_state={"present": True},
        prepared_at=harness.clock(),
    )
    harness.store.reserve_remote_action(
        attempt_id="forged-attempt-outside-sealed-plan",
        owner_id=OWNER_ID,
        connection_id=CONNECTION_ID,
        platform="youtube",
        migration_id=commission["migration_id"],
        action_id=forged_action.id,
        operation=forged_action.action_type.value,
        idempotency_key=forged_action.idempotency_key,
        request_fingerprint=forged_prepared.idempotency_fingerprint,
        action_payload=harness.application._prepared_payload(forged_prepared),
        created_at=harness.clock(),
    )
    writes_before_recovery = harness.adapter.apply_calls

    with pytest.raises(InvalidStateError, match="outside the exact migration plan"):
        harness.service.reconcile(commission["id"], actor_id=OWNER_ID)

    assert harness.adapter.apply_calls == writes_before_recovery


def test_durable_receipt_recovers_commission_without_new_consent_or_current_certification(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commission = finalize_commission(harness)
    grant = issue_live_grant(harness, commission["id"])
    original_finalize = harness.application._finalize_migration_receipt

    def crash_after_receipt(*_args, **_kwargs):
        raise SimulatedProcessDeath("worker exited after the durable receipt")

    monkeypatch.setattr(
        harness.application,
        "_finalize_migration_receipt",
        crash_after_receipt,
    )
    with pytest.raises(SimulatedProcessDeath):
        harness.service.execute(
            commission["id"],
            actor_id=OWNER_ID,
            approval_token=grant,
        )

    assert harness.service.get(commission["id"], actor_id=OWNER_ID)["status"] == (
        "awaiting_approval"
    )
    receipts = [
        item
        for item in harness.application.projection_list("receipts")
        if item.get("migration_id") == commission["migration_id"]
    ]
    assert len(receipts) == 1

    monkeypatch.setattr(
        harness.application,
        "_finalize_migration_receipt",
        original_finalize,
    )
    harness.clock.value = harness.adapter.certification.expires_at
    restarted = LiveCommissionService(
        harness.application,
        consent_broker=harness.broker,
    )
    recovered = restarted.reconcile(commission["id"], actor_id=OWNER_ID)

    assert recovered["receipt_id"] == receipts[0]["id"]
    assert recovered["migration"]["receipt_id"] == receipts[0]["id"]
    assert recovered["status"].startswith("issued")
    assert harness.adapter.apply_calls == 2


@pytest.mark.parametrize("tamper", ("finalization_plan", "journal_outcome"))
def test_receipt_recovery_rejects_corruption_outside_the_exact_plan_and_journal(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    commission = finalize_commission(harness)
    original_finalize = harness.application._finalize_migration_receipt

    def crash_after_receipt(*_args, **_kwargs):
        raise SimulatedProcessDeath("worker exited after the durable receipt")

    monkeypatch.setattr(
        harness.application,
        "_finalize_migration_receipt",
        crash_after_receipt,
    )
    with pytest.raises(SimulatedProcessDeath):
        harness.service.execute(
            commission["id"],
            actor_id=OWNER_ID,
            approval_token=issue_live_grant(harness, commission["id"]),
        )
    receipt = next(
        item
        for item in harness.application.projection_list("receipts")
        if item.get("migration_id") == commission["migration_id"]
    )
    current = harness.store.get_projection("receipts", receipt["id"])
    assert current is not None
    corrupted = dict(current[1])
    if tamper == "finalization_plan":
        corrupted["migration_finalization"]["plan"]["actions"][0]["target"] = (
            "corrupted-finalization-target"
        )
    else:
        corrupted["outcomes"][0]["after_state"] = {"corrupted": True}
    harness.store.append_event(
        aggregate_id=receipt["id"],
        aggregate_type="action_receipt",
        expected_version=current[0],
        event_type="receipt.test_corrupted",
        payload={"tamper": tamper},
        actor_id=OWNER_ID,
        trace_id="test-corrupted-live-receipt",
        occurred_at=harness.clock(),
        projection_kind="receipts",
        projection=corrupted,
    )
    monkeypatch.setattr(
        harness.application,
        "_finalize_migration_receipt",
        original_finalize,
    )

    with pytest.raises(InvalidStateError, match="exact executed plan|durable journal"):
        harness.service.reconcile(commission["id"], actor_id=OWNER_ID)

    assert harness.service.get(commission["id"], actor_id=OWNER_ID)["status"] == (
        "awaiting_approval"
    )
    assert harness.adapter.apply_calls == 2


def test_durable_journal_recovers_by_observation_without_new_consent_or_current_certification(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commission = finalize_commission(harness)
    harness.adapter.lose_next_response = True
    grant = issue_live_grant(harness, commission["id"])
    original_record = harness.service._record

    def crash_before_commission_checkpoint(*args, **kwargs):
        if str(kwargs.get("event_type", "")).startswith("agent_live_commission."):
            raise SimulatedProcessDeath("worker exited before the commission checkpoint")
        return original_record(*args, **kwargs)

    monkeypatch.setattr(
        harness.service,
        "_record",
        crash_before_commission_checkpoint,
    )
    with pytest.raises(SimulatedProcessDeath):
        harness.service.execute(
            commission["id"],
            actor_id=OWNER_ID,
            approval_token=grant,
        )

    stored = harness.service.get(commission["id"], actor_id=OWNER_ID)
    assert stored["status"] == "awaiting_approval"
    migration = harness.application.projection_get("migrations", commission["migration_id"])
    assert migration["status"] == "reconciliation_required"
    attempts = harness.store.list_remote_actions(
        owner_id=OWNER_ID,
        migration_id=commission["migration_id"],
        connection_id=CONNECTION_ID,
    )
    assert len(attempts) == 1
    writes_before_recovery = harness.adapter.apply_calls

    monkeypatch.setattr(harness.service, "_record", original_record)
    harness.clock.value = harness.adapter.certification.expires_at
    restarted = LiveCommissionService(
        harness.application,
        consent_broker=harness.broker,
    )
    recovered = restarted.reconcile(commission["id"], actor_id=OWNER_ID)

    assert recovered["status"] == "failed_recoverable"
    assert recovered["receipt_id"] is None
    assert harness.adapter.apply_calls == writes_before_recovery
    assert harness.adapter.applied_targets == [POSITIVE_CREATOR]


def test_certification_value_rejects_bad_expiry_and_code_revision() -> None:
    with pytest.raises(ValueError, match="expiry"):
        certification(expires_at=NOW)
    with pytest.raises(ValueError, match="Git revision"):
        replace(certification(), code_revision="main")
