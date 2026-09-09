from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import islice
from threading import Lock, RLock
from typing import Final, Literal

from feed_passport.infrastructure.platform_imports import (
    InstagramExportSnapshot,
    InstagramImportLimits,
    parse_instagram_accounts_center_export,
)


DEFAULT_SESSION_TTL: Final = timedelta(minutes=15)
MAX_SELECTED_HANDLES: Final = 500
MAX_ACTIVE_SESSIONS: Final = 32
_HANDLE = re.compile(r"^[a-z0-9_](?:[a-z0-9._]{0,28}[a-z0-9_])?$")


class InstagramImportSessionError(RuntimeError):
    """A stable, redacted import-session failure suitable for an API boundary."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class InstagramImportPreview:
    session_id: str
    status: Literal["ready"]
    passport_id: str
    passport_version: int
    created_at: datetime
    expires_at: datetime
    source_sha256: str
    parser_id: str
    source_relationship_count: int
    accepted_relationship_count: int
    duplicate_relationship_count: int
    rejected_relationship_count: int
    ignored_archive_entry_count: int
    warnings: tuple[str, ...]
    observed_fields: tuple[str, ...]
    unobserved_fields: tuple[str, ...]
    followed_handles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InstagramImportSummary:
    status: Literal["consumed", "discarded"]
    source_sha256: str
    source_relationship_count: int
    accepted_relationship_count: int
    selected_relationship_count: int
    duplicate_relationship_count: int
    rejected_relationship_count: int


@dataclass(slots=True, repr=False)
class _ActiveSession:
    owner_id: str
    passport_id: str
    passport_version: int
    created_at: datetime
    expires_at: datetime
    source_sha256: str
    parser_id: str
    source_relationship_count: int
    accepted_relationship_count: int
    duplicate_relationship_count: int
    rejected_relationship_count: int
    ignored_archive_entry_count: int
    warnings: tuple[str, ...]
    observed_fields: tuple[str, ...]
    unobserved_fields: tuple[str, ...]
    followed_handles: tuple[str, ...]
    applying: bool = field(default=False, repr=False)
    lock: RLock = field(default_factory=RLock, repr=False)

    @classmethod
    def from_snapshot(
        cls,
        *,
        owner_id: str,
        passport_id: str,
        passport_version: int,
        created_at: datetime,
        expires_at: datetime,
        snapshot: InstagramExportSnapshot,
    ) -> _ActiveSession:
        return cls(
            owner_id=owner_id,
            passport_id=passport_id,
            passport_version=passport_version,
            created_at=created_at,
            expires_at=expires_at,
            # Only the recognized following JSON may cross the parser boundary.
            # Hashing the containing ZIP would fingerprint unrelated private
            # Accounts Center files that this workflow intentionally ignores.
            source_sha256=snapshot.accepted_file_sha256,
            parser_id=snapshot.parser_id,
            source_relationship_count=snapshot.source_relationship_count,
            accepted_relationship_count=snapshot.accepted_relationship_count,
            duplicate_relationship_count=snapshot.duplicate_relationship_count,
            rejected_relationship_count=snapshot.rejected_relationship_count,
            ignored_archive_entry_count=snapshot.ignored_archive_entry_count,
            warnings=snapshot.warnings,
            observed_fields=snapshot.observed_fields,
            unobserved_fields=snapshot.unobserved_fields,
            followed_handles=snapshot.followed_handles,
        )

    def preview(self, session_id: str) -> InstagramImportPreview:
        return InstagramImportPreview(
            session_id=session_id,
            status="ready",
            passport_id=self.passport_id,
            passport_version=self.passport_version,
            created_at=self.created_at,
            expires_at=self.expires_at,
            source_sha256=self.source_sha256,
            parser_id=self.parser_id,
            source_relationship_count=self.source_relationship_count,
            accepted_relationship_count=self.accepted_relationship_count,
            duplicate_relationship_count=self.duplicate_relationship_count,
            rejected_relationship_count=self.rejected_relationship_count,
            ignored_archive_entry_count=self.ignored_archive_entry_count,
            warnings=self.warnings,
            observed_fields=self.observed_fields,
            unobserved_fields=self.unobserved_fields,
            followed_handles=self.followed_handles,
        )

    def summary(
        self,
        *,
        status: Literal["consumed", "discarded"],
        selected_count: int,
    ) -> InstagramImportSummary:
        return InstagramImportSummary(
            status=status,
            source_sha256=self.source_sha256,
            source_relationship_count=self.source_relationship_count,
            accepted_relationship_count=self.accepted_relationship_count,
            selected_relationship_count=selected_count,
            duplicate_relationship_count=self.duplicate_relationship_count,
            rejected_relationship_count=self.rejected_relationship_count,
        )


class InstagramImportSessionService:
    """Owner-bound, in-memory staging for one short-lived Accounts Center import."""

    __slots__ = (
        "_clock",
        "_create_lock",
        "_index_lock",
        "_limits",
        "_sessions",
        "_ttl",
    )

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        limits: InstagramImportLimits | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._limits = limits
        self._ttl = DEFAULT_SESSION_TTL
        self._create_lock = Lock()
        self._index_lock = Lock()
        self._sessions: dict[str, _ActiveSession] = {}

    def create_preview(
        self,
        *,
        owner_id: str,
        passport_id: str,
        passport_version: int,
        source: bytes | bytearray | memoryview,
    ) -> InstagramImportPreview:
        self.purge_expired()
        normalized_owner = self._validated_owner(owner_id)
        normalized_passport = self._validated_passport_id(passport_id)
        normalized_version = self._validated_passport_version(passport_version)
        parse_arguments = {"limits": self._limits} if self._limits is not None else {}
        snapshot = parse_instagram_accounts_center_export(source, **parse_arguments)
        created_at = self._now()
        session = _ActiveSession.from_snapshot(
            owner_id=normalized_owner,
            passport_id=normalized_passport,
            passport_version=normalized_version,
            created_at=created_at,
            expires_at=created_at + self._ttl,
            snapshot=snapshot,
        )
        # One owner keeps one active preview per Passport. Replacement and the
        # small global ceiling bound private memory even under repeated local
        # uploads while preserving an in-flight apply operation.
        with self._create_lock:
            with self._index_lock:
                replaceable = tuple(
                    (session_id, active)
                    for session_id, active in self._sessions.items()
                    if active.owner_id == normalized_owner
                    and active.passport_id == normalized_passport
                )
            for existing_id, existing in replaceable:
                with existing.lock:
                    if existing.applying:
                        raise InstagramImportSessionError(
                            "session_busy",
                            "An Instagram import for this Passport is already being applied",
                        )
                    with self._index_lock:
                        if self._sessions.get(existing_id) is existing:
                            del self._sessions[existing_id]
            with self._index_lock:
                if len(self._sessions) >= MAX_ACTIVE_SESSIONS:
                    raise InstagramImportSessionError(
                        "session_capacity",
                        "Too many Instagram import previews are active; discard or wait for one to expire",
                    )
                session_id = self._new_session_id()
                while session_id in self._sessions:
                    session_id = self._new_session_id()
                self._sessions[session_id] = session
        return session.preview(session_id)

    def get_preview(self, *, session_id: str, owner_id: str) -> InstagramImportPreview:
        session = self._owned_session(session_id=session_id, owner_id=owner_id)
        with session.lock:
            self._assert_current_and_live(session_id=session_id, session=session)
            return session.preview(session_id)

    def apply(
        self,
        *,
        session_id: str,
        owner_id: str,
        passport_id: str,
        expected_passport_version: int,
        selected_handles: Iterable[str],
        remaining_capacity: int,
        apply_callback: Callable[[tuple[str, ...]], object],
    ) -> InstagramImportSummary:
        if not callable(apply_callback):
            raise TypeError("apply_callback must be callable")
        session = self._owned_session(session_id=session_id, owner_id=owner_id)
        with session.lock:
            self._assert_current_and_live(session_id=session_id, session=session)
            self._assert_passport_binding(
                session,
                passport_id=passport_id,
                passport_version=expected_passport_version,
            )
            selected = self._validated_selection(
                selected_handles,
                available=frozenset(session.followed_handles),
                remaining_capacity=remaining_capacity,
            )
            if session.applying:
                raise InstagramImportSessionError(
                    "session_busy",
                    "Instagram import session is already being applied",
                )
            session.applying = True
            try:
                apply_callback(selected)
            except Exception:
                session.applying = False
                raise InstagramImportSessionError(
                    "apply_callback_failed",
                    "Instagram import could not be applied; the session remains available for retry",
                ) from None
            summary = session.summary(status="consumed", selected_count=len(selected))
            self._remove_current(session_id=session_id, session=session)
            return summary

    def discard(self, *, session_id: str, owner_id: str) -> InstagramImportSummary:
        session = self._owned_session(session_id=session_id, owner_id=owner_id)
        with session.lock:
            self._assert_current_and_live(session_id=session_id, session=session)
            if session.applying:
                raise InstagramImportSessionError(
                    "session_busy",
                    "Instagram import session is already being applied",
                )
            summary = session.summary(status="discarded", selected_count=0)
            self._remove_current(session_id=session_id, session=session)
            return summary

    def purge_expired(self) -> int:
        now = self._now()
        with self._index_lock:
            candidates = tuple(self._sessions.items())
        purged = 0
        for session_id, session in candidates:
            with session.lock:
                with self._index_lock:
                    if self._sessions.get(session_id) is session and now >= session.expires_at:
                        del self._sessions[session_id]
                        purged += 1
        return purged

    def clear(self) -> int:
        """Drop every private preview, including during orderly service shutdown."""

        with self._index_lock:
            count = len(self._sessions)
            self._sessions.clear()
        return count

    def _owned_session(self, *, session_id: str, owner_id: str) -> _ActiveSession:
        normalized_owner = self._validated_owner(owner_id)
        if not isinstance(session_id, str) or not session_id:
            raise self._unavailable()
        with self._index_lock:
            session = self._sessions.get(session_id)
        owner_matches = session is not None and secrets.compare_digest(
            session.owner_id.encode("utf-8"),
            normalized_owner.encode("utf-8"),
        )
        if session is None or not owner_matches:
            raise self._unavailable()
        return session

    def _assert_current_and_live(self, *, session_id: str, session: _ActiveSession) -> None:
        now = self._now()
        with self._index_lock:
            if self._sessions.get(session_id) is not session:
                raise self._unavailable()
            if now >= session.expires_at:
                del self._sessions[session_id]
                raise InstagramImportSessionError(
                    "session_expired",
                    "Instagram import session expired and its private preview was purged",
                )

    def _remove_current(self, *, session_id: str, session: _ActiveSession) -> None:
        with self._index_lock:
            if self._sessions.get(session_id) is not session:
                raise self._unavailable()
            del self._sessions[session_id]

    def _assert_passport_binding(
        self,
        session: _ActiveSession,
        *,
        passport_id: str,
        passport_version: int,
    ) -> None:
        if (
            self._validated_passport_id(passport_id) != session.passport_id
            or self._validated_passport_version(passport_version)
            != session.passport_version
        ):
            raise InstagramImportSessionError(
                "passport_binding_changed",
                "Instagram import preview belongs to a different Passport revision",
            )

    @staticmethod
    def _validated_selection(
        selected_handles: Iterable[str],
        *,
        available: frozenset[str],
        remaining_capacity: int,
    ) -> tuple[str, ...]:
        if isinstance(selected_handles, (str, bytes, bytearray)):
            raise InstagramImportSessionError(
                "invalid_selection",
                "Instagram import selection must be a collection of normalized handles",
            )
        if isinstance(remaining_capacity, bool) or not isinstance(remaining_capacity, int):
            raise InstagramImportSessionError(
                "invalid_capacity",
                "Remaining creator capacity must be a non-negative integer",
            )
        if remaining_capacity < 0:
            raise InstagramImportSessionError(
                "invalid_capacity",
                "Remaining creator capacity must be a non-negative integer",
            )
        selection_limit = min(MAX_SELECTED_HANDLES, remaining_capacity)
        try:
            selected = tuple(islice(iter(selected_handles), selection_limit + 1))
        except TypeError:
            raise InstagramImportSessionError(
                "invalid_selection",
                "Instagram import selection must be a collection of normalized handles",
            ) from None
        if not selected:
            raise InstagramImportSessionError(
                "empty_selection",
                "Select at least one followed handle to apply",
            )
        if len(selected) > selection_limit:
            raise InstagramImportSessionError(
                "selection_capacity_exceeded",
                "Instagram import selection exceeds the available creator capacity",
            )
        if any(not isinstance(handle, str) or not _is_normalized_handle(handle) for handle in selected):
            raise InstagramImportSessionError(
                "invalid_selection",
                "Instagram import selection contains a non-normalized handle",
            )
        if len(set(selected)) != len(selected):
            raise InstagramImportSessionError(
                "duplicate_selection",
                "Instagram import selection contains duplicate handles",
            )
        if not set(selected) <= available:
            raise InstagramImportSessionError(
                "selection_not_in_snapshot",
                "Instagram import selection contains a handle absent from this preview",
            )
        return selected

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Instagram import session clock must be timezone-aware")
        return value

    @staticmethod
    def _validated_owner(owner_id: str) -> str:
        if (
            not isinstance(owner_id, str)
            or not owner_id
            or len(owner_id) > 160
            or owner_id != owner_id.strip()
        ):
            raise InstagramImportSessionError(
                "invalid_owner",
                "Instagram import session requires a normalized owner identifier",
            )
        return owner_id

    @staticmethod
    def _validated_passport_id(passport_id: str) -> str:
        if (
            not isinstance(passport_id, str)
            or not passport_id
            or len(passport_id) > 160
            or passport_id != passport_id.strip()
        ):
            raise InstagramImportSessionError(
                "invalid_passport",
                "Instagram import session requires a normalized Passport identifier",
            )
        return passport_id

    @staticmethod
    def _validated_passport_version(passport_version: int) -> int:
        if (
            isinstance(passport_version, bool)
            or not isinstance(passport_version, int)
            or passport_version < 1
        ):
            raise InstagramImportSessionError(
                "invalid_passport",
                "Instagram import session requires a positive Passport version",
            )
        return passport_version

    @staticmethod
    def _new_session_id() -> str:
        return f"igimp_{secrets.token_urlsafe(24)}"

    @staticmethod
    def _unavailable() -> InstagramImportSessionError:
        return InstagramImportSessionError(
            "session_unavailable",
            "Instagram import session is unavailable",
        )


def _is_normalized_handle(value: str) -> bool:
    return value == value.lower() and _HANDLE.fullmatch(value) is not None and ".." not in value
