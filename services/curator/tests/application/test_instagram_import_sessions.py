from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from threading import Event, Lock

import pytest

from feed_passport.application.instagram_import_sessions import (
    MAX_ACTIVE_SESSIONS,
    MAX_SELECTED_HANDLES,
    InstagramImportSessionError,
    InstagramImportSessionService,
)


NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


class MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def relationship(handle: str, *, marker: str | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "title": handle,
        "string_list_data": [
            {
                "href": f"https://www.instagram.com/{handle}/",
                "value": handle,
                "timestamp": 1_725_796_800,
            }
        ],
    }
    if marker is not None:
        value["ignored_marker"] = marker
    return value


def export_bytes(*handles: str, marker: str | None = None) -> bytes:
    return json.dumps(
        {
            "relationships_following": [
                relationship(handle, marker=marker if index == 0 else None)
                for index, handle in enumerate(handles)
            ]
        },
        separators=(",", ":"),
    ).encode()


def create_preview(
    service: InstagramImportSessionService,
    *,
    owner_id: str,
    source: bytes,
    passport_id: str = "passport-a",
    passport_version: int = 1,
):
    return service.create_preview(
        owner_id=owner_id,
        passport_id=passport_id,
        passport_version=passport_version,
        source=source,
    )


def assert_session_error(code: str, call: Callable[[], object]) -> None:
    with pytest.raises(InstagramImportSessionError) as raised:
        call()
    assert raised.value.code == code


def test_create_returns_owner_preview_with_random_id_and_exact_fifteen_minute_expiry() -> None:
    clock = MutableClock()
    service = InstagramImportSessionService(clock=clock)
    source = export_bytes("paper.lab", "city_zine")

    first = create_preview(service, owner_id="owner-a", source=source)
    second = create_preview(service, owner_id="owner-a", source=source)

    assert first.status == "ready"
    assert first.followed_handles == ("city_zine", "paper.lab")
    assert first.created_at == NOW
    assert first.expires_at == NOW + timedelta(minutes=15)
    assert first.session_id != second.session_id
    assert re.fullmatch(r"igimp_[A-Za-z0-9_-]{32}", first.session_id)
    assert first.source_relationship_count == 2
    assert first.accepted_relationship_count == 2
    assert "topic_distribution" in first.unobserved_fields
    with pytest.raises(FrozenInstanceError):
        first.status = "consumed"  # type: ignore[misc]


def test_new_preview_replaces_same_passport_and_global_active_count_is_bounded() -> None:
    service = InstagramImportSessionService(clock=MutableClock())
    replaced = create_preview(
        service,
        owner_id="owner-a",
        passport_id="passport-replaced",
        source=export_bytes("first.handle"),
    )
    current = create_preview(
        service,
        owner_id="owner-a",
        passport_id="passport-replaced",
        source=export_bytes("second.handle"),
    )

    assert_session_error(
        "session_unavailable",
        lambda: service.get_preview(
            session_id=replaced.session_id,
            owner_id="owner-a",
        ),
    )
    assert service.get_preview(
        session_id=current.session_id,
        owner_id="owner-a",
    ).followed_handles == ("second.handle",)

    for index in range(1, MAX_ACTIVE_SESSIONS):
        create_preview(
            service,
            owner_id=f"owner-{index}",
            passport_id=f"passport-{index}",
            source=export_bytes(f"creator_{index}"),
        )
    assert len(service._sessions) == MAX_ACTIVE_SESSIONS  # noqa: SLF001
    assert_session_error(
        "session_capacity",
        lambda: create_preview(
            service,
            owner_id="overflow-owner",
            passport_id="overflow-passport",
            source=export_bytes("overflow_creator"),
        ),
    )


def test_zip_preview_fingerprints_only_the_accepted_following_file() -> None:
    payload = export_bytes("paper.lab", "city_zine")
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "connections/followers_and_following/following.json",
            payload,
        )
        archive.writestr(
            "messages/inbox/private-thread/message_1.json",
            b'{"private":"must not affect accepted source fingerprint"}',
        )
    archive_bytes = archive_buffer.getvalue()

    preview = create_preview(
        InstagramImportSessionService(clock=MutableClock()),
        owner_id="owner-a",
        source=archive_bytes,
    )

    assert preview.source_sha256 == hashlib.sha256(payload).hexdigest()
    assert preview.source_sha256 != hashlib.sha256(archive_bytes).hexdigest()
    assert preview.ignored_archive_entry_count == 1


def test_preview_is_owner_bound_and_wrong_owner_cannot_learn_or_discard_it() -> None:
    service = InstagramImportSessionService(clock=MutableClock())
    preview = create_preview(service, owner_id="owner-a", source=export_bytes("private.handle"))

    assert_session_error(
        "session_unavailable",
        lambda: service.get_preview(session_id=preview.session_id, owner_id="owner-b"),
    )
    assert_session_error(
        "session_unavailable",
        lambda: service.discard(session_id=preview.session_id, owner_id="owner-b"),
    )
    assert service.get_preview(
        session_id=preview.session_id,
        owner_id="owner-a",
    ).followed_handles == ("private.handle",)


@pytest.mark.parametrize(
    ("selection", "capacity", "code"),
    [
        ([], 500, "empty_selection"),
        (["paper.lab", "paper.lab"], 500, "duplicate_selection"),
        (["Paper.Lab"], 500, "invalid_selection"),
        ([" paper.lab"], 500, "invalid_selection"),
        (["missing"], 500, "selection_not_in_snapshot"),
        (["paper.lab"], 0, "selection_capacity_exceeded"),
        (["paper.lab"], -1, "invalid_capacity"),
        (["paper.lab"], True, "invalid_capacity"),
    ],
)
def test_apply_rejects_invalid_or_out_of_scope_selections_without_consuming(
    selection: list[str],
    capacity: int,
    code: str,
) -> None:
    service = InstagramImportSessionService(clock=MutableClock())
    preview = create_preview(service, owner_id="owner-a", source=export_bytes("paper.lab"))
    callback_calls = 0

    def callback(_: tuple[str, ...]) -> None:
        nonlocal callback_calls
        callback_calls += 1

    assert_session_error(
        code,
        lambda: service.apply(
            session_id=preview.session_id,
            owner_id="owner-a",
            passport_id="passport-a",
            expected_passport_version=1,
            selected_handles=selection,
            remaining_capacity=capacity,
            apply_callback=callback,
        ),
    )
    assert callback_calls == 0
    assert service.get_preview(
        session_id=preview.session_id,
        owner_id="owner-a",
    ).followed_handles == ("paper.lab",)


def test_hard_selection_limit_is_five_hundred_even_with_more_remaining_capacity() -> None:
    handles = tuple(f"creator_{index}" for index in range(MAX_SELECTED_HANDLES + 1))
    service = InstagramImportSessionService(clock=MutableClock())
    preview = create_preview(service, owner_id="owner-a", source=export_bytes(*handles))

    assert_session_error(
        "selection_capacity_exceeded",
        lambda: service.apply(
            session_id=preview.session_id,
            owner_id="owner-a",
            passport_id="passport-a",
            expected_passport_version=1,
            selected_handles=handles,
            remaining_capacity=10_000,
            apply_callback=lambda _: None,
        ),
    )


def test_apply_consumes_once_only_after_callback_success_and_returns_redacted_summary() -> None:
    service = InstagramImportSessionService(clock=MutableClock())
    source = export_bytes("paper.lab", "city_zine", marker="raw-private-marker")
    preview = create_preview(service, owner_id="owner-a", source=source)
    applied: list[tuple[str, ...]] = []

    summary = service.apply(
        session_id=preview.session_id,
        owner_id="owner-a",
        passport_id="passport-a",
        expected_passport_version=1,
        selected_handles=("paper.lab",),
        remaining_capacity=1,
        apply_callback=applied.append,
    )

    assert applied == [("paper.lab",)]
    assert summary.status == "consumed"
    assert summary.selected_relationship_count == 1
    assert summary.source_relationship_count == 2
    assert summary.accepted_relationship_count == 2
    assert {item.name for item in fields(summary)} == {
        "status",
        "source_sha256",
        "source_relationship_count",
        "accepted_relationship_count",
        "selected_relationship_count",
        "duplicate_relationship_count",
        "rejected_relationship_count",
    }
    assert "paper.lab" not in repr(summary)
    assert "raw-private-marker" not in repr(summary)
    assert_session_error(
        "session_unavailable",
        lambda: service.apply(
            session_id=preview.session_id,
            owner_id="owner-a",
            passport_id="passport-a",
            expected_passport_version=1,
            selected_handles=("paper.lab",),
            remaining_capacity=1,
            apply_callback=applied.append,
        ),
    )
    assert applied == [("paper.lab",)]


def test_callback_failure_is_redacted_and_leaves_the_session_retryable() -> None:
    service = InstagramImportSessionService(clock=MutableClock())
    preview = create_preview(service, owner_id="owner-a", source=export_bytes("private.handle"))
    calls = 0

    def failing(_: tuple[str, ...]) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("private.handle should not escape through the service error")

    with pytest.raises(InstagramImportSessionError) as raised:
        service.apply(
            session_id=preview.session_id,
            owner_id="owner-a",
            passport_id="passport-a",
            expected_passport_version=1,
            selected_handles=("private.handle",),
            remaining_capacity=1,
            apply_callback=failing,
        )
    assert raised.value.code == "apply_callback_failed"
    assert "private.handle" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert calls == 1
    assert service.get_preview(
        session_id=preview.session_id,
        owner_id="owner-a",
    ).followed_handles == ("private.handle",)

    summary = service.apply(
        session_id=preview.session_id,
        owner_id="owner-a",
        passport_id="passport-a",
        expected_passport_version=1,
        selected_handles=("private.handle",),
        remaining_capacity=1,
        apply_callback=lambda _: None,
    )
    assert summary.status == "consumed"


def test_apply_is_bound_to_the_previewed_passport_and_exact_version() -> None:
    service = InstagramImportSessionService(clock=MutableClock())
    preview = create_preview(
        service,
        owner_id="owner-a",
        passport_id="passport-a",
        passport_version=3,
        source=export_bytes("paper.lab"),
    )
    callback_calls = 0

    def callback(_: tuple[str, ...]) -> None:
        nonlocal callback_calls
        callback_calls += 1

    for passport_id, passport_version in (("passport-b", 3), ("passport-a", 4)):
        assert_session_error(
            "passport_binding_changed",
            lambda passport_id=passport_id, passport_version=passport_version: service.apply(
                session_id=preview.session_id,
                owner_id="owner-a",
                passport_id=passport_id,
                expected_passport_version=passport_version,
                selected_handles=("paper.lab",),
                remaining_capacity=1,
                apply_callback=callback,
            ),
        )

    assert callback_calls == 0
    assert service.get_preview(
        session_id=preview.session_id,
        owner_id="owner-a",
    ).passport_version == 3


def test_concurrent_apply_runs_the_callback_exactly_once() -> None:
    service = InstagramImportSessionService(clock=MutableClock())
    preview = create_preview(service, owner_id="owner-a", source=export_bytes("paper.lab"))
    callback_entered = Event()
    callback_release = Event()
    callback_lock = Lock()
    callback_calls = 0

    def callback(_: tuple[str, ...]) -> None:
        nonlocal callback_calls
        with callback_lock:
            callback_calls += 1
        callback_entered.set()
        assert callback_release.wait(timeout=5)

    def apply() -> str:
        try:
            return service.apply(
                session_id=preview.session_id,
                owner_id="owner-a",
                passport_id="passport-a",
                expected_passport_version=1,
                selected_handles=("paper.lab",),
                remaining_capacity=1,
                apply_callback=callback,
            ).status
        except InstagramImportSessionError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(apply)
        assert callback_entered.wait(timeout=5)
        second = executor.submit(apply)
        callback_release.set()
        outcomes = {first.result(timeout=5), second.result(timeout=5)}

    assert outcomes == {"consumed", "session_unavailable"}
    assert callback_calls == 1


def test_discard_and_expiry_purge_all_private_session_state() -> None:
    clock = MutableClock()
    service = InstagramImportSessionService(clock=clock)
    discarded = create_preview(service, owner_id="owner-a", source=export_bytes("discard.me"))

    summary = service.discard(session_id=discarded.session_id, owner_id="owner-a")
    assert summary.status == "discarded"
    assert summary.selected_relationship_count == 0
    assert "discard.me" not in repr(summary)
    assert_session_error(
        "session_unavailable",
        lambda: service.get_preview(session_id=discarded.session_id, owner_id="owner-a"),
    )

    expired = create_preview(service, owner_id="owner-a", source=export_bytes("expire.me"))
    clock.value = NOW + timedelta(minutes=15)
    assert_session_error(
        "session_expired",
        lambda: service.get_preview(session_id=expired.session_id, owner_id="owner-a"),
    )
    assert_session_error(
        "session_unavailable",
        lambda: service.get_preview(session_id=expired.session_id, owner_id="owner-a"),
    )

    first = create_preview(service, owner_id="owner-a", source=export_bytes("purge.one"))
    second = create_preview(service, owner_id="owner-b", source=export_bytes("purge.two"))
    clock.value += timedelta(minutes=15)
    assert service.purge_expired() == 2
    for owner_id, session_id in (("owner-a", first.session_id), ("owner-b", second.session_id)):
        assert_session_error(
            "session_unavailable",
            lambda owner_id=owner_id, session_id=session_id: service.get_preview(
                session_id=session_id,
                owner_id=owner_id,
            ),
        )


def test_service_never_retains_source_bytes_and_error_text_redacts_identifiers() -> None:
    service = InstagramImportSessionService(clock=MutableClock())
    source = bytearray(export_bytes("private.handle", marker="raw-private-marker"))
    preview = create_preview(service, owner_id="sensitive-owner", source=source)
    source[:] = b"x" * len(source)

    session = service._sessions[preview.session_id]  # noqa: SLF001 - security invariant inspection
    retained_values = [getattr(session, item.name) for item in fields(session)]
    assert not any(isinstance(value, (bytes, bytearray, memoryview)) for value in retained_values)
    assert "raw-private-marker" not in repr(service)
    assert "raw-private-marker" not in repr(session)
    assert "private.handle" not in repr(session)
    assert "sensitive-owner" not in repr(session)

    with pytest.raises(InstagramImportSessionError) as raised:
        service.get_preview(session_id=preview.session_id, owner_id="attacker-owner")
    assert raised.value.code == "session_unavailable"
    assert "sensitive-owner" not in str(raised.value)
    assert "private.handle" not in str(raised.value)
    assert preview.session_id not in str(raised.value)


def test_clear_purges_every_private_preview_for_service_shutdown() -> None:
    service = InstagramImportSessionService(clock=MutableClock())
    first = create_preview(service, owner_id="owner-a", source=export_bytes("first.handle"))
    second = create_preview(service, owner_id="owner-b", source=export_bytes("second.handle"))

    assert service.clear() == 2
    assert service.clear() == 0
    for owner_id, session_id in (
        ("owner-a", first.session_id),
        ("owner-b", second.session_id),
    ):
        assert_session_error(
            "session_unavailable",
            lambda owner_id=owner_id, session_id=session_id: service.get_preview(
                session_id=session_id,
                owner_id=owner_id,
            ),
        )
