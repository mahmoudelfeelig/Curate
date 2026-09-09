from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from feed_passport.application.guided_handoff import GuidedHandoffConflict, GuidedHandoffService
from feed_passport.domain import ActionType, CapabilityLevel, ProposedAction, TranslationPlan
from feed_passport.infrastructure.guided_handoff_store import SQLiteGuidedHandoffRepository
from feed_passport.infrastructure.sqlite_store import SQLiteStore


NOW = datetime(2026, 9, 8, 20, 0, tzinfo=timezone.utc)


def _plan() -> TranslationPlan:
    action = ProposedAction(
        id="action-follow",
        destination_id="private-account",
        action_type=ActionType.FOLLOW_CREATOR,
        target="public.creator",
        reason="Preserve explicit creator intent.",
        idempotency_key="guided-follow-key",
        reversible=False,
        parameters={
            "delivery": "guided_handoff",
            "instruction": "Open the creator profile and select Follow.",
            "credential": "must-not-persist",
        },
    )
    return TranslationPlan(
        id="plan-guided",
        passport_id="passport-owner",
        passport_version=2,
        destination_id="private-account",
        capability_level=CapabilityLevel.GUIDED,
        actions=(action,),
        losses=(),
        created_at=NOW,
        estimated_topic_distance=1.0,
    )


def test_sqlite_guided_handoff_survives_restart_without_private_action_metadata(tmp_path) -> None:
    database = tmp_path / "guided.db"
    first_store = SQLiteStore(database)
    first = GuidedHandoffService(SQLiteGuidedHandoffRepository(first_store))
    preview = first.preview(
        session_id="guided-1",
        owner_id="owner-a",
        platform="instagram",
        plan=_plan(),
        now=NOW,
    )
    consented = first.consent(
        preview.id,
        actor_id="owner-a",
        consent_reference="consent-guided-1",
        expected_steps_sha256=preview.steps_sha256,
        now=NOW + timedelta(seconds=1),
    )
    first.begin_handoff(
        consented.id,
        actor_id="owner-a",
        now=NOW + timedelta(seconds=2),
    )
    first_store.close()

    second_store = SQLiteStore(database)
    second = GuidedHandoffService(SQLiteGuidedHandoffRepository(second_store))
    restored = second.get("guided-1", actor_id="owner-a")
    assert restored.state.value == "awaiting_handoff"
    assert restored.steps[0].target == "public.creator"
    raw = second_store.get_projection("guided_handoffs", "guided-1")
    assert raw is not None
    serialized = str(raw[1]).casefold()
    assert "private-account" not in serialized
    assert "must-not-persist" not in serialized
    assert "idempotency" not in serialized
    second_store.close()


def test_sqlite_guided_handoff_rejects_stale_revision(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "guided.db")
    repository = SQLiteGuidedHandoffRepository(store)
    service = GuidedHandoffService(repository)
    preview = service.preview(
        session_id="guided-1",
        owner_id="owner-a",
        platform="instagram",
        plan=_plan(),
        now=NOW,
    )
    current = service.consent(
        preview.id,
        actor_id="owner-a",
        consent_reference="consent-guided-1",
        expected_steps_sha256=preview.steps_sha256,
        now=NOW + timedelta(seconds=1),
    )
    with pytest.raises(GuidedHandoffConflict):
        repository.save(current, expected_revision=1)
    store.close()
