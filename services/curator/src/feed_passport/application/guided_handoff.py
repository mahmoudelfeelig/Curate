from __future__ import annotations

from datetime import datetime
from threading import RLock
from typing import Protocol

from feed_passport.domain.guided_handoff import (
    GuidedHandoffConflict,
    GuidedHandoffSession,
    GuidedStepResolution,
)
from feed_passport.domain.models import TranslationPlan


class GuidedHandoffNotFound(LookupError):
    pass


class GuidedHandoffRepository(Protocol):
    """Optimistic storage seam for one immutable guided-handoff aggregate."""

    def create(self, session: GuidedHandoffSession) -> None: ...

    def get(self, session_id: str) -> GuidedHandoffSession | None: ...

    def save(self, session: GuidedHandoffSession, *, expected_revision: int) -> None: ...


class InMemoryGuidedHandoffRepository:
    """Thread-safe local implementation used by tests and single-process composition."""

    def __init__(self) -> None:
        self._sessions: dict[str, GuidedHandoffSession] = {}
        self._lock = RLock()

    def create(self, session: GuidedHandoffSession) -> None:
        with self._lock:
            if session.id in self._sessions:
                raise GuidedHandoffConflict(f"guided handoff {session.id} already exists")
            self._sessions[session.id] = session

    def get(self, session_id: str) -> GuidedHandoffSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def save(self, session: GuidedHandoffSession, *, expected_revision: int) -> None:
        with self._lock:
            current = self._sessions.get(session.id)
            if current is None:
                raise GuidedHandoffNotFound(f"guided handoff {session.id} was not found")
            if current.revision != expected_revision:
                raise GuidedHandoffConflict(
                    f"guided handoff {session.id} changed concurrently"
                )
            if session.revision != expected_revision + 1:
                raise GuidedHandoffConflict(
                    "guided handoff persistence requires exactly one aggregate revision"
                )
            self._sessions[session.id] = session


class GuidedHandoffService:
    """Owner-bound application service for manual platform-control handoffs.

    The service has no HTTP client, platform transport, credential provider, model,
    or raw observation input. It persists only the safe immutable projection created
    by :class:`GuidedHandoffSession` from actions explicitly marked guided_handoff.
    """

    def __init__(self, repository: GuidedHandoffRepository) -> None:
        self._repository = repository

    def preview(
        self,
        *,
        session_id: str,
        owner_id: str,
        platform: str,
        plan: TranslationPlan,
        now: datetime,
    ) -> GuidedHandoffSession:
        session = GuidedHandoffSession.preview(
            session_id=session_id,
            owner_id=owner_id,
            platform=platform,
            plan=plan,
            now=now,
        )
        self._repository.create(session)
        return session

    def get(self, session_id: str, *, actor_id: str) -> GuidedHandoffSession:
        session = self._load(session_id)
        session.assert_owner(actor_id)
        return session

    def consent(
        self,
        session_id: str,
        *,
        actor_id: str,
        consent_reference: str,
        expected_steps_sha256: str,
        now: datetime,
    ) -> GuidedHandoffSession:
        current = self.get(session_id, actor_id=actor_id)
        updated = current.consent(
            actor_id=actor_id,
            consent_reference=consent_reference,
            expected_steps_sha256=expected_steps_sha256,
            now=now,
        )
        return self._save_transition(current, updated)

    def begin_handoff(
        self,
        session_id: str,
        *,
        actor_id: str,
        now: datetime,
    ) -> GuidedHandoffSession:
        current = self.get(session_id, actor_id=actor_id)
        updated = current.begin_handoff(actor_id=actor_id, now=now)
        return self._save_transition(current, updated)

    def resolve_step(
        self,
        session_id: str,
        *,
        actor_id: str,
        step_id: str,
        resolution: GuidedStepResolution | str,
        now: datetime,
    ) -> GuidedHandoffSession:
        current = self.get(session_id, actor_id=actor_id)
        updated = current.resolve_step(
            actor_id=actor_id,
            step_id=step_id,
            resolution=resolution,
            now=now,
        )
        return self._save_transition(current, updated)

    def finalize(
        self,
        session_id: str,
        *,
        actor_id: str,
        now: datetime,
    ) -> GuidedHandoffSession:
        current = self.get(session_id, actor_id=actor_id)
        updated = current.finalize(actor_id=actor_id, now=now)
        return self._save_transition(current, updated)

    def _load(self, session_id: str) -> GuidedHandoffSession:
        session = self._repository.get(session_id)
        if session is None:
            raise GuidedHandoffNotFound(f"guided handoff {session_id} was not found")
        return session

    def _save_transition(
        self,
        current: GuidedHandoffSession,
        updated: GuidedHandoffSession,
    ) -> GuidedHandoffSession:
        if updated is current:
            return current
        self._repository.save(updated, expected_revision=current.revision)
        return updated


__all__ = [
    "GuidedHandoffConflict",
    "GuidedHandoffNotFound",
    "GuidedHandoffRepository",
    "GuidedHandoffService",
    "InMemoryGuidedHandoffRepository",
]
