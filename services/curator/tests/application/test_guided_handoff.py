from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from feed_passport.application.guided_handoff import (
    GuidedHandoffConflict,
    GuidedHandoffService,
    InMemoryGuidedHandoffRepository,
)
from feed_passport.domain import ActionStatus, ActionType, CapabilityLevel, ProposedAction, TranslationPlan
from feed_passport.domain.guided_handoff import (
    GuidedHandoffState,
    GuidedHandoffTransitionError,
    GuidedStepResolution,
    guided_handoff_from_record,
    guided_handoff_to_record,
)


NOW = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)


def action(
    ordinal: int,
    *,
    action_type: ActionType = ActionType.MUTE_KEYWORD,
    target: str | None = None,
    instruction: str = "Open Hidden Words and add this exact phrase.",
    delivery: str = "guided_handoff",
    extra_parameters: dict[str, object] | None = None,
) -> ProposedAction:
    parameters: dict[str, object] = {
        "delivery": delivery,
        "instruction": instruction,
        "evidence_urls": ("https://example.test/official-control",),
    }
    parameters.update(extra_parameters or {})
    return ProposedAction(
        id=f"action-{ordinal}",
        destination_id="declared-instagram-account",
        action_type=action_type,
        target=target or f"topic-{ordinal}",
        reason=f"Private compiler rationale {ordinal} must not be retained by the handoff.",
        idempotency_key=f"idempotency-{ordinal}",
        reversible=False,
        parameters=parameters,
    )


def plan(*actions: ProposedAction) -> TranslationPlan:
    return TranslationPlan(
        id="plan-guided-instagram-v7",
        passport_id="passport-owner-a",
        passport_version=7,
        destination_id="declared-instagram-account",
        capability_level=CapabilityLevel.GUIDED,
        actions=tuple(actions),
        losses=(),
        created_at=NOW,
        estimated_topic_distance=0.3,
    )


def service() -> GuidedHandoffService:
    return GuidedHandoffService(InMemoryGuidedHandoffRepository())


def preview(service: GuidedHandoffService, *actions: ProposedAction):
    return service.preview(
        session_id="guided-session-a",
        owner_id="person-a",
        platform="instagram",
        plan=plan(*actions),
        now=NOW,
    )


def ready_for_resolution(service: GuidedHandoffService, *actions: ProposedAction):
    session = preview(service, *actions)
    session = service.consent(
        session.id,
        actor_id="person-a",
        consent_reference="consent-guided-a",
        expected_steps_sha256=session.steps_sha256,
        now=NOW + timedelta(minutes=1),
    )
    return service.begin_handoff(
        session.id,
        actor_id="person-a",
        now=NOW + timedelta(minutes=2),
    )


def test_preview_projects_only_exact_immutable_guided_steps_and_discards_private_parameters() -> None:
    guided = action(
        1,
        target="ragebait",
        extra_parameters={
            "access_token": "must-never-survive",
            "raw_private_feed": {"watched": ["private-video"]},
        },
    )
    not_guided = action(2, delivery="official_api")

    session = preview(service(), guided, not_guided)

    assert session.state is GuidedHandoffState.PREVIEWED
    assert session.receipt is None
    assert len(session.steps) == 1
    assert session.steps[0].id == guided.id
    assert session.steps[0].action_type is guided.action_type
    assert session.steps[0].target == guided.target
    assert session.steps[0].instruction == guided.parameters["instruction"]
    assert session.steps[0].resolution is None
    with pytest.raises(FrozenInstanceError):
        session.steps[0].target = "changed"  # type: ignore[misc]

    encoded = json.dumps(guided_handoff_to_record(session), sort_keys=True)
    assert "must-never-survive" not in encoded
    assert "private-video" not in encoded
    assert "Private compiler rationale" not in encoded
    assert "idempotency-1" not in encoded
    assert "declared-instagram-account" not in encoded


def test_complete_state_machine_requires_every_resolution_and_explicit_finalize() -> None:
    app = service()
    session = preview(
        app,
        action(1, action_type=ActionType.FOLLOW_CREATOR, target="creator.one"),
        action(2, target="topic-two"),
        action(3, action_type=ActionType.HIDE_TOPIC, target="topic-three"),
    )
    assert session.state is GuidedHandoffState.PREVIEWED

    session = app.consent(
        session.id,
        actor_id="person-a",
        consent_reference="consent-guided-a",
        expected_steps_sha256=session.steps_sha256,
        now=NOW + timedelta(minutes=1),
    )
    assert session.state is GuidedHandoffState.CONSENTED
    assert session.receipt is None

    session = app.begin_handoff(
        session.id,
        actor_id="person-a",
        now=NOW + timedelta(minutes=2),
    )
    assert session.state is GuidedHandoffState.AWAITING_HANDOFF

    session = app.resolve_step(
        session.id,
        actor_id="person-a",
        step_id="action-1",
        resolution=GuidedStepResolution.COMPLETED_BY_USER,
        now=NOW + timedelta(minutes=3),
    )
    session = app.resolve_step(
        session.id,
        actor_id="person-a",
        step_id="action-2",
        resolution=GuidedStepResolution.SKIPPED_BY_USER,
        now=NOW + timedelta(minutes=4),
    )
    assert session.state is GuidedHandoffState.AWAITING_HANDOFF
    assert session.receipt is None
    with pytest.raises(GuidedHandoffTransitionError, match="every handoff step"):
        app.finalize(session.id, actor_id="person-a", now=NOW + timedelta(minutes=5))

    session = app.resolve_step(
        session.id,
        actor_id="person-a",
        step_id="action-3",
        resolution=GuidedStepResolution.CONTROL_NOT_FOUND,
        now=NOW + timedelta(minutes=5),
    )
    assert session.state is GuidedHandoffState.USER_RESOLVED
    assert session.receipt is None

    finalized = app.finalize(
        session.id,
        actor_id="person-a",
        now=NOW + timedelta(minutes=6),
    )
    assert finalized.state is GuidedHandoffState.FINALIZED
    assert finalized.receipt is not None
    assert [outcome.status for outcome in finalized.receipt.outcomes] == [
        ActionStatus.GUIDED,
        ActionStatus.SKIPPED,
        ActionStatus.SKIPPED,
    ]
    assert ActionStatus.EXECUTED not in {outcome.status for outcome in finalized.receipt.outcomes}
    assert finalized.receipt.summary.total_steps == 3
    assert finalized.receipt.summary.completed_by_user == 1
    assert finalized.receipt.summary.skipped_by_user == 1
    assert finalized.receipt.summary.control_not_found == 1
    assert finalized.receipt.summary.api_writes == 0
    assert finalized.receipt.summary.recommendation_outcomes_verified == 0
    assert finalized.receipt.summary.platform_verified is False


def test_resolution_and_finalize_retries_are_idempotent_but_conflicts_fail() -> None:
    app = service()
    session = ready_for_resolution(app, action(1))
    resolved = app.resolve_step(
        session.id,
        actor_id="person-a",
        step_id="action-1",
        resolution="completed_by_user",
        now=NOW + timedelta(minutes=3),
    )
    retried = app.resolve_step(
        session.id,
        actor_id="person-a",
        step_id="action-1",
        resolution="completed_by_user",
        now=NOW + timedelta(minutes=30),
    )
    assert retried == resolved
    assert retried.revision == resolved.revision
    assert retried.updated_at == resolved.updated_at

    with pytest.raises(GuidedHandoffConflict, match="already resolved"):
        app.resolve_step(
            session.id,
            actor_id="person-a",
            step_id="action-1",
            resolution="skipped_by_user",
            now=NOW + timedelta(minutes=31),
        )

    finalized = app.finalize(
        session.id,
        actor_id="person-a",
        now=NOW + timedelta(minutes=32),
    )
    assert app.finalize(
        session.id,
        actor_id="person-a",
        now=NOW + timedelta(minutes=40),
    ) == finalized
    assert app.resolve_step(
        session.id,
        actor_id="person-a",
        step_id="action-1",
        resolution="completed_by_user",
        now=NOW + timedelta(minutes=41),
    ) == finalized


def test_owner_and_consent_digest_are_bound_to_the_previewed_session() -> None:
    app = service()
    session = preview(app, action(1))

    with pytest.raises(PermissionError, match="owner"):
        app.get(session.id, actor_id="person-b")
    with pytest.raises(PermissionError, match="owner"):
        app.consent(
            session.id,
            actor_id="person-b",
            consent_reference="consent-stolen",
            expected_steps_sha256=session.steps_sha256,
            now=NOW + timedelta(minutes=1),
        )
    with pytest.raises(GuidedHandoffConflict, match="steps digest"):
        app.consent(
            session.id,
            actor_id="person-a",
            consent_reference="consent-guided-a",
            expected_steps_sha256="0" * 64,
            now=NOW + timedelta(minutes=1),
        )


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        ("begin", "consented"),
        ("resolve", "awaiting_handoff"),
        ("finalize", "every handoff step"),
    ],
)
def test_transitions_cannot_skip_required_states(operation: str, message: str) -> None:
    app = service()
    session = preview(app, action(1))

    with pytest.raises(GuidedHandoffTransitionError, match=message):
        if operation == "begin":
            app.begin_handoff(session.id, actor_id="person-a", now=NOW + timedelta(minutes=1))
        elif operation == "resolve":
            app.resolve_step(
                session.id,
                actor_id="person-a",
                step_id="action-1",
                resolution="completed_by_user",
                now=NOW + timedelta(minutes=1),
            )
        else:
            app.finalize(session.id, actor_id="person-a", now=NOW + timedelta(minutes=1))


def test_serialization_round_trip_is_strict_and_preserves_consent_bound_steps() -> None:
    app = service()
    session = ready_for_resolution(app, action(1))
    session = app.resolve_step(
        session.id,
        actor_id="person-a",
        step_id="action-1",
        resolution="control_not_found",
        now=NOW + timedelta(minutes=3),
    )
    session = app.finalize(
        session.id,
        actor_id="person-a",
        now=NOW + timedelta(minutes=4),
    )

    record = guided_handoff_to_record(session)
    assert guided_handoff_from_record(record) == session

    record_with_credential = dict(record)
    record_with_credential["access_token"] = "forbidden"
    with pytest.raises(ValueError, match="unexpected fields"):
        guided_handoff_from_record(record_with_credential)

    tampered = json.loads(json.dumps(record))
    tampered["steps"][0]["instruction"] = "Different instruction"
    with pytest.raises(ValueError, match="steps_sha256"):
        guided_handoff_from_record(tampered)


def test_preview_rejects_missing_or_malformed_guided_actions() -> None:
    app = service()
    with pytest.raises(ValueError, match="at least one guided"):
        preview(app, action(1, delivery="official_api"))
    with pytest.raises(ValueError, match="instruction"):
        preview(app, action(1, instruction="  "))
    with pytest.raises(ValueError, match="duplicate guided action id"):
        duplicate = action(1)
        preview(app, duplicate, duplicate)

