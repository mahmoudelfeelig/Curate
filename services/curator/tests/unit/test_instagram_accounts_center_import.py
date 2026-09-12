from __future__ import annotations

import hashlib
import io
import json
import stat
import struct
import zipfile
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from feed_passport.adapters.platforms.instagram import INSTAGRAM_PROFILE
from feed_passport.domain import ActionType
from feed_passport.infrastructure.platform_imports import (
    InstagramExportError,
    InstagramImportLimits,
    parse_instagram_accounts_center_export,
)


FIXTURE = Path(__file__).parents[1] / "fixtures" / "instagram" / "accounts_center_following.json"
FOLLOWING_PATH = "export-root/connections/followers_and_following/following.json"


def relationship(
    handle: str = "paper.lab",
    *,
    href: str | None = None,
    title: str | None = None,
    timestamp: object = 1_724_889_600,
) -> dict[str, object]:
    item: dict[str, object] = {
        "href": href or f"https://www.instagram.com/{handle}/",
        "value": handle,
    }
    if timestamp is not ...:
        item["timestamp"] = timestamp
    return {
        "title": handle if title is None else title,
        "media_list_data": [],
        "string_list_data": [item],
    }


def compact_relationship(
    handle: str = "paper.lab",
    *,
    href: str | None = None,
) -> dict[str, object]:
    return {
        "title": handle,
        "string_list_data": [
            {
                "href": href or f"https://www.instagram.com/{handle}/",
                "timestamp": 1_724_889_600,
            }
        ],
    }


def following_json(*items: object, extra: dict[str, object] | None = None) -> bytes:
    document: dict[str, object] = {"relationships_following": list(items)}
    document.update(extra or {})
    return json.dumps(document, separators=(",", ":")).encode()


def archive_bytes(
    entries: list[tuple[str | zipfile.ZipInfo, bytes]],
    *,
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=compression) as archive:
        for name, value in entries:
            archive.writestr(name, value)
    return target.getvalue()


def encrypted_flag(source: bytes) -> bytes:
    value = bytearray(source)
    local_offset = value.index(b"PK\x03\x04")
    central_offset = value.index(b"PK\x01\x02")
    local_flags = struct.unpack_from("<H", value, local_offset + 6)[0] | 0x1
    central_flags = struct.unpack_from("<H", value, central_offset + 8)[0] | 0x1
    struct.pack_into("<H", value, local_offset + 6, local_flags)
    struct.pack_into("<H", value, central_offset + 8, central_flags)
    return bytes(value)


def replace_member_name(source: bytes, original: bytes, replacement: bytes) -> bytes:
    assert len(original) == len(replacement)
    assert source.count(original) == 2
    return source.replace(original, replacement)


def assert_error(code: str, source: bytes, **kwargs: object) -> None:
    with pytest.raises(InstagramExportError) as raised:
        parse_instagram_accounts_center_export(source, **kwargs)
    assert raised.value.code == code


def test_raw_following_json_normalizes_only_creator_relationship_evidence() -> None:
    source = FIXTURE.read_bytes()

    snapshot = parse_instagram_accounts_center_export(source)

    assert snapshot.source_format == "json"
    assert snapshot.parser_id == "meta.instagram.relationships_following.v1"
    assert snapshot.recognized_path == "following.json"
    assert snapshot.source_sha256 == hashlib.sha256(source).hexdigest()
    assert snapshot.accepted_file_sha256 == snapshot.source_sha256
    assert snapshot.followed_handles == ("city_zine", "paper.lab")
    assert [item.relationship_timestamp for item in snapshot.following] == [
        1_724_976_000,
        1_724_889_600,
    ]
    assert snapshot.source_relationship_count == 2
    assert snapshot.accepted_relationship_count == 2
    assert snapshot.duplicate_relationship_count == 0
    assert snapshot.rejected_relationship_count == 0
    assert snapshot.warnings == ()
    assert snapshot.observed_fields == ("followed_creators",)
    assert "topic_distribution" in snapshot.unobserved_fields
    assert "recommendation_state" in snapshot.unobserved_fields
    assert not hasattr(snapshot, "topic_distribution")
    assert snapshot.raw_archive_retained is False
    assert snapshot.credentials_included is False
    with pytest.raises(FrozenInstanceError):
        snapshot.source_format = "zip"  # type: ignore[misc]


def test_current_compact_export_uses_title_only_when_profile_url_agrees() -> None:
    snapshot = parse_instagram_accounts_center_export(
        following_json(compact_relationship("paper.lab"))
    )

    assert snapshot.followed_handles == ("paper.lab",)
    assert snapshot.accepted_relationship_count == 1

    redirect_snapshot = parse_instagram_accounts_center_export(
        following_json(
            compact_relationship(
                "paper.lab",
                href="https://www.instagram.com/_u/paper.lab/",
            )
        )
    )
    assert redirect_snapshot.followed_handles == ("paper.lab",)

    assert_error(
        "no_valid_following_records",
        following_json(
            compact_relationship(
                "paper.lab",
                href="https://www.instagram.com/different.account/",
            )
        ),
    )


def test_zip_reads_one_recognized_member_without_extracting_or_retaining_ignored_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = FIXTURE.read_bytes()
    secret = b"private direct-message body that must never be parsed"
    source = archive_bytes(
        [
            (FOLLOWING_PATH, payload),
            ("export-root/your_instagram_activity/messages/inbox.json", secret),
        ]
    )

    def extraction_forbidden(*_: object, **__: object) -> None:
        raise AssertionError("archive extraction is forbidden")

    monkeypatch.setattr(zipfile.ZipFile, "extract", extraction_forbidden)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", extraction_forbidden)
    snapshot = parse_instagram_accounts_center_export(source)

    assert snapshot.source_format == "zip"
    assert snapshot.recognized_path == "connections/followers_and_following/following.json"
    assert snapshot.source_sha256 == hashlib.sha256(source).hexdigest()
    assert snapshot.accepted_file_sha256 == hashlib.sha256(payload).hexdigest()
    assert snapshot.ignored_archive_entry_count == 1
    assert snapshot.warnings == ("ignored_archive_entries:1",)
    assert secret.decode() not in repr(snapshot)


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "../escape.json",
        "/absolute.json",
        "C:/drive.json",
        "folder/./file.json",
        "folder//file.json",
    ],
)
def test_zip_rejects_unsafe_member_paths(unsafe_name: str) -> None:
    source = archive_bytes([(FOLLOWING_PATH, FIXTURE.read_bytes()), (unsafe_name, b"ignored")])
    assert_error("unsafe_archive_path", source)


def test_zip_rejects_backslash_and_nul_paths_from_raw_archive_headers() -> None:
    backslash = replace_member_name(
        archive_bytes([(FOLLOWING_PATH, FIXTURE.read_bytes()), ("folder/windows.json", b"ignored")]),
        b"folder/windows.json",
        b"folder\\windows.json",
    )
    assert_error("unsafe_archive_path", backslash)

    nul = replace_member_name(
        archive_bytes([(FOLLOWING_PATH, FIXTURE.read_bytes()), ("nulxsuffix.json", b"ignored")]),
        b"nulxsuffix.json",
        b"nul\x00suffix.json",
    )
    assert_error("unsafe_archive_path", nul)


def test_zip_rejects_case_insensitive_duplicate_paths() -> None:
    source = archive_bytes(
        [
            (FOLLOWING_PATH, FIXTURE.read_bytes()),
            (FOLLOWING_PATH.upper(), FIXTURE.read_bytes()),
        ]
    )
    assert_error("duplicate_archive_path", source)


def test_zip_rejects_encrypted_and_non_regular_members() -> None:
    encrypted = encrypted_flag(archive_bytes([(FOLLOWING_PATH, FIXTURE.read_bytes())]))
    assert_error("encrypted_archive_entry", encrypted)

    link = zipfile.ZipInfo("export-root/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    non_regular = archive_bytes([(FOLLOWING_PATH, FIXTURE.read_bytes()), (link, b"target")])
    assert_error("non_regular_archive_entry", non_regular)


@pytest.mark.parametrize(
    ("source", "code"),
    [
        (b'{"relationships_following":[],"relationships_following":[]}', "duplicate_json_key"),
        (b'{"relationships_following":[],"unknown":NaN}', "non_finite_json_number"),
        (b'{"relationships_following":[}\xff', "invalid_json_encoding"),
        (b"<html>not a JSON export</html>", "unsupported_export_format"),
        (b'{"not_following":[]}', "unsupported_following_schema"),
        (b"[]", "unsupported_following_schema"),
    ],
)
def test_rejects_malformed_or_unsupported_json(source: bytes, code: str) -> None:
    assert_error(code, source)


@pytest.mark.parametrize(
    "item",
    [
        relationship("name..withdots"),
        relationship("endswith."),
        relationship("@prefixed"),
        relationship("a" * 31),
        relationship("paper.lab", href="https://www.instagram.com/other/"),
        relationship("paper.lab", href="https://instagram.com.evil.test/paper.lab/"),
        relationship("paper.lab", href="https://user@instagram.com/paper.lab/"),
        relationship("paper.lab", href="https://www.instagram.com/paper.lab/?next=x"),
        relationship("paper.lab", href="https://www.instagram.com//paper.lab/"),
        relationship("paper.lab", title="other"),
        {
            "title": {},
            "string_list_data": [
                {"href": "https://instagram.com/paper.lab/", "value": "paper.lab"}
            ],
        },
        relationship("paper.lab", timestamp=True),
    ],
)
def test_rejects_exports_with_only_invalid_relationships(item: object) -> None:
    assert_error("no_valid_following_records", following_json(item))


def test_mixed_records_are_deduplicated_and_report_aggregate_warnings() -> None:
    source = following_json(
        relationship("Paper.Lab", timestamp=1_700_000_000),
        relationship("paper.lab", timestamp=1_800_000_000),
        relationship("invalid..name"),
        relationship("city_zine", timestamp=...),
        extra={"export_metadata": {"ignored": True}},
    )

    snapshot = parse_instagram_accounts_center_export(source)

    assert snapshot.followed_handles == ("city_zine", "paper.lab")
    assert snapshot.following[1].relationship_timestamp == 1_800_000_000
    assert snapshot.source_relationship_count == 4
    assert snapshot.accepted_relationship_count == 2
    assert snapshot.duplicate_relationship_count == 1
    assert snapshot.rejected_relationship_count == 1
    assert snapshot.warnings == (
        "ignored_top_level_fields:1",
        "rejected_relationships:1",
        "duplicate_relationships:1",
        "missing_relationship_timestamps:1",
    )


def test_accepts_a_recognized_empty_following_export_without_inventing_state() -> None:
    snapshot = parse_instagram_accounts_center_export(following_json())
    assert snapshot.following == ()
    assert snapshot.accepted_relationship_count == 0
    assert snapshot.observed_fields == ("followed_creators",)
    assert "topic_distribution" in snapshot.unobserved_fields


def test_enforces_configurable_input_archive_json_relationship_and_depth_caps() -> None:
    assert_error(
        "input_too_large",
        following_json(relationship()),
        limits=InstagramImportLimits(max_input_bytes=4),
    )
    assert_error(
        "too_many_archive_entries",
        archive_bytes([(FOLLOWING_PATH, following_json()), ("safe/ignored.json", b"{}")]),
        limits=InstagramImportLimits(max_archive_entries=1),
    )
    assert_error(
        "json_too_large",
        following_json(relationship()),
        limits=InstagramImportLimits(max_json_bytes=16),
    )
    assert_error(
        "too_many_relationships",
        following_json(relationship("one"), relationship("two")),
        limits=InstagramImportLimits(max_relationships=1),
    )
    assert_error(
        "json_too_deep",
        following_json(extra={"nested": {"one": {"two": {"three": True}}}}),
        limits=InstagramImportLimits(max_json_depth=3),
    )
    too_long_path = "x" * 20
    assert_error(
        "unsafe_archive_path",
        archive_bytes([(FOLLOWING_PATH, following_json()), (too_long_path, b"ignored")]),
        limits=InstagramImportLimits(max_archive_path_chars=10),
    )
    assert_error(
        "no_valid_following_records",
        following_json(relationship("paper.lab", href=f"https://instagram.com/{'x' * 80}")),
        limits=InstagramImportLimits(max_scalar_chars=40),
    )


def test_rejects_high_ratio_archive_and_ambiguous_or_missing_following_files() -> None:
    ratio_source = archive_bytes(
        [(FOLLOWING_PATH, following_json(relationship("compressible")) + b" " * 20_000)]
    )
    assert_error(
        "suspicious_compression_ratio",
        ratio_source,
        limits=InstagramImportLimits(max_compression_ratio=2),
    )

    assert_error("following_json_missing", archive_bytes([("safe/other.json", b"{}")]))
    duplicate = archive_bytes(
        [
            (FOLLOWING_PATH, following_json()),
            (f"second/{FOLLOWING_PATH}", following_json()),
        ]
    )
    assert_error("ambiguous_following_json", duplicate)


def test_instagram_profile_declares_only_real_native_creator_and_hidden_word_handoffs() -> None:
    assert INSTAGRAM_PROFILE.native_handoff_actions == frozenset(
        {
            ActionType.FOLLOW_CREATOR,
            ActionType.UNFOLLOW_CREATOR,
            ActionType.MUTE_CREATOR,
            ActionType.UNMUTE_CREATOR,
            ActionType.MUTE_KEYWORD,
            ActionType.UNMUTE_KEYWORD,
        }
    )
    assert not {
        ActionType.HIDE_TOPIC,
        ActionType.SHOW_TOPIC,
        ActionType.SET_TOPIC_PREFERENCE,
    } & INSTAGRAM_PROFILE.native_handoff_actions
    assert INSTAGRAM_PROFILE.topic_strategy == "unavailable"
