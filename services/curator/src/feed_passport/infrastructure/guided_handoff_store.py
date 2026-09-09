from __future__ import annotations

from feed_passport.application.guided_handoff import (
    GuidedHandoffConflict,
    GuidedHandoffNotFound,
    GuidedHandoffRepository,
)
from feed_passport.domain.guided_handoff import (
    GuidedHandoffSession,
    guided_handoff_from_record,
    guided_handoff_to_record,
)

from .sqlite_store import ConcurrencyConflict, SQLiteStore


class SQLiteGuidedHandoffRepository(GuidedHandoffRepository):
    """Durable safe-projection repository for user-completed native-control handoffs."""

    projection_kind = "guided_handoffs"

    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    def create(self, session: GuidedHandoffSession) -> None:
        if session.revision != 1:
            raise GuidedHandoffConflict("a new guided handoff must begin at revision one")
        if self._store.get_projection(self.projection_kind, session.id) is not None:
            raise GuidedHandoffConflict(f"guided handoff {session.id} already exists")
        try:
            self._append(session, expected_version=0, event_type="guided_handoff.previewed")
        except ConcurrencyConflict as error:
            raise GuidedHandoffConflict(str(error)) from error

    def get(self, session_id: str) -> GuidedHandoffSession | None:
        stored = self._store.get_projection(self.projection_kind, session_id)
        if stored is None:
            return None
        projection_version, value = stored
        session = guided_handoff_from_record(value)
        if projection_version != session.revision:
            raise GuidedHandoffConflict("guided handoff projection revision is inconsistent")
        return session

    def save(self, session: GuidedHandoffSession, *, expected_revision: int) -> None:
        stored = self._store.get_projection(self.projection_kind, session.id)
        if stored is None:
            raise GuidedHandoffNotFound(f"guided handoff {session.id} was not found")
        projection_version, value = stored
        current = guided_handoff_from_record(value)
        if projection_version != expected_revision or current.revision != expected_revision:
            raise GuidedHandoffConflict(f"guided handoff {session.id} changed concurrently")
        if session.revision != expected_revision + 1:
            raise GuidedHandoffConflict(
                "guided handoff persistence requires exactly one aggregate revision"
            )
        try:
            self._append(
                session,
                expected_version=expected_revision,
                event_type=f"guided_handoff.{session.state.value}",
            )
        except ConcurrencyConflict as error:
            raise GuidedHandoffConflict(str(error)) from error

    def _append(
        self,
        session: GuidedHandoffSession,
        *,
        expected_version: int,
        event_type: str,
    ) -> None:
        record = guided_handoff_to_record(session)
        resolved_count = sum(step.resolution is not None for step in session.steps)
        self._store.append_event(
            aggregate_id=session.id,
            aggregate_type="guided_handoff",
            expected_version=expected_version,
            event_type=event_type,
            payload={
                "state": session.state.value,
                "step_count": len(session.steps),
                "resolved_count": resolved_count,
                "steps_sha256": session.steps_sha256,
            },
            actor_id=session.owner_id,
            trace_id=f"guided-handoff:{session.id}",
            occurred_at=session.updated_at,
            projection_kind=self.projection_kind,
            projection=record,
        )


__all__ = ["SQLiteGuidedHandoffRepository"]
