from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Literal, Mapping
from urllib.parse import urlsplit


PARSER_ID: Final = "meta.instagram.relationships_following.v1"
_FOLLOWING_PATH: Final = "connections/followers_and_following/following.json"
_HANDLE = re.compile(r"^[a-z0-9_](?:[a-z0-9._]{0,28}[a-z0-9_])?$")
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_SUPPORTED_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
_UNOBSERVED_FIELDS: Final = (
    "topic_distribution",
    "feed_items",
    "muted_creators",
    "hidden_words",
    "format_preferences",
    "languages",
    "serendipity",
    "outrage",
    "source_concentration",
    "recommendation_state",
)


class InstagramExportError(ValueError):
    """A stable, non-sensitive failure raised for an unsafe or unsupported export."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class InstagramImportLimits:
    max_input_bytes: int = 64 * 1024 * 1024
    max_json_bytes: int = 16 * 1024 * 1024
    max_archive_entries: int = 2_000
    max_total_uncompressed_bytes: int = 256 * 1024 * 1024
    max_compression_ratio: float = 100.0
    max_relationships: int = 10_000
    max_json_depth: int = 32
    max_archive_path_chars: int = 512
    max_scalar_chars: int = 512

    def __post_init__(self) -> None:
        integer_limits = (
            self.max_input_bytes,
            self.max_json_bytes,
            self.max_archive_entries,
            self.max_total_uncompressed_bytes,
            self.max_relationships,
            self.max_json_depth,
            self.max_archive_path_chars,
            self.max_scalar_chars,
        )
        if any(value < 1 for value in integer_limits) or self.max_compression_ratio <= 0:
            raise ValueError("Instagram import limits must be positive")


DEFAULT_INSTAGRAM_IMPORT_LIMITS = InstagramImportLimits()


@dataclass(frozen=True, slots=True)
class InstagramFollowing:
    handle: str
    relationship_timestamp: int | None


@dataclass(frozen=True, slots=True)
class InstagramExportSnapshot:
    source_sha256: str
    accepted_file_sha256: str
    source_format: Literal["json", "zip"]
    parser_id: str
    recognized_path: str
    following: tuple[InstagramFollowing, ...]
    source_relationship_count: int
    accepted_relationship_count: int
    duplicate_relationship_count: int
    rejected_relationship_count: int
    ignored_archive_entry_count: int
    warnings: tuple[str, ...]
    observed_fields: tuple[str, ...] = ("followed_creators",)
    unobserved_fields: tuple[str, ...] = _UNOBSERVED_FIELDS
    raw_archive_retained: bool = False
    credentials_included: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-f0-9]{64}", self.source_sha256):
            raise ValueError("source_sha256 must be lowercase SHA-256")
        if not re.fullmatch(r"[a-f0-9]{64}", self.accepted_file_sha256):
            raise ValueError("accepted_file_sha256 must be lowercase SHA-256")
        if self.parser_id != PARSER_ID:
            raise ValueError("unexpected Instagram parser identifier")
        if self.accepted_relationship_count != len(self.following):
            raise ValueError("accepted relationship count must match the normalized records")
        if self.source_relationship_count != (
            self.accepted_relationship_count
            + self.duplicate_relationship_count
            + self.rejected_relationship_count
        ):
            raise ValueError("relationship counts must account for every source record")
        if self.raw_archive_retained or self.credentials_included:
            raise ValueError("normalized Instagram snapshots cannot retain archives or credentials")

    @property
    def followed_handles(self) -> tuple[str, ...]:
        return tuple(item.handle for item in self.following)


@dataclass(frozen=True, slots=True)
class _NormalizedArchiveEntry:
    info: zipfile.ZipInfo
    path: str


def parse_instagram_accounts_center_export(
    source: bytes | bytearray | memoryview,
    *,
    limits: InstagramImportLimits = DEFAULT_INSTAGRAM_IMPORT_LIMITS,
) -> InstagramExportSnapshot:
    """Parse only Instagram's recognized following relationship export.

    The function is pure: it opens no path, extracts no archive member, retains no
    source bytes, and performs no network or account operation.
    """

    if not isinstance(source, (bytes, bytearray, memoryview)):
        raise TypeError("Instagram export source must be bytes")
    raw = bytes(source)
    if not raw:
        raise InstagramExportError("empty_export", "Instagram export is empty")
    if len(raw) > limits.max_input_bytes:
        raise InstagramExportError(
            "input_too_large",
            "Instagram export exceeds the configured compressed-input limit",
        )

    source_sha256 = hashlib.sha256(raw).hexdigest()
    if raw.lstrip().startswith((b"{", b"[")):
        if len(raw) > limits.max_json_bytes:
            raise InstagramExportError(
                "json_too_large",
                "Instagram following JSON exceeds the configured file limit",
            )
        return _parse_snapshot(
            raw,
            source_sha256=source_sha256,
            source_format="json",
            recognized_path="following.json",
            ignored_archive_entry_count=0,
            limits=limits,
        )
    if raw.startswith(b"PK"):
        return _parse_zip(raw, source_sha256=source_sha256, limits=limits)
    raise InstagramExportError(
        "unsupported_export_format",
        "Expected an Instagram following.json file or Accounts Center ZIP export",
    )


def _parse_zip(
    raw: bytes,
    *,
    source_sha256: str,
    limits: InstagramImportLimits,
) -> InstagramExportSnapshot:
    try:
        with zipfile.ZipFile(io.BytesIO(raw), mode="r") as archive:
            infos = archive.infolist()
            if len(infos) > limits.max_archive_entries:
                raise InstagramExportError(
                    "too_many_archive_entries",
                    "Instagram export contains too many archive entries",
                )
            normalized = _validate_archive_entries(infos, limits=limits)
            following_entries = [
                item
                for item in normalized
                if not item.info.is_dir() and _is_following_path(item.path)
            ]
            if not following_entries:
                raise InstagramExportError(
                    "following_json_missing",
                    "Accounts Center ZIP does not contain the recognized Instagram following JSON",
                )
            if len(following_entries) != 1:
                raise InstagramExportError(
                    "ambiguous_following_json",
                    "Accounts Center ZIP contains more than one recognized following JSON",
                )

            entry = following_entries[0]
            if entry.info.file_size > limits.max_json_bytes:
                raise InstagramExportError(
                    "json_too_large",
                    "Instagram following JSON exceeds the configured file limit",
                )
            try:
                with archive.open(entry.info, mode="r") as stream:
                    payload = stream.read(limits.max_json_bytes + 1)
            except (NotImplementedError, RuntimeError, OSError, zipfile.BadZipFile) as exc:
                raise InstagramExportError(
                    "unreadable_archive_entry",
                    "Instagram following JSON could not be read safely",
                ) from exc
            if len(payload) > limits.max_json_bytes:
                raise InstagramExportError(
                    "json_too_large",
                    "Instagram following JSON exceeds the configured file limit",
                )
            ignored_count = sum(not item.info.is_dir() for item in normalized) - 1
            return _parse_snapshot(
                payload,
                source_sha256=source_sha256,
                source_format="zip",
                recognized_path=_FOLLOWING_PATH,
                ignored_archive_entry_count=ignored_count,
                limits=limits,
            )
    except InstagramExportError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise InstagramExportError(
            "invalid_zip",
            "Instagram Accounts Center ZIP is malformed or unsupported",
        ) from exc


def _validate_archive_entries(
    infos: list[zipfile.ZipInfo],
    *,
    limits: InstagramImportLimits,
) -> tuple[_NormalizedArchiveEntry, ...]:
    total_uncompressed = 0
    seen_paths: set[str] = set()
    normalized: list[_NormalizedArchiveEntry] = []
    for info in infos:
        path = _normalize_archive_path(info, limits=limits)
        comparison_path = path.rstrip("/").casefold()
        if comparison_path in seen_paths:
            raise InstagramExportError(
                "duplicate_archive_path",
                "Instagram export contains duplicate case-insensitive archive paths",
            )
        seen_paths.add(comparison_path)
        if info.flag_bits & 0x1:
            raise InstagramExportError(
                "encrypted_archive_entry",
                "Encrypted Accounts Center archive entries are not supported",
            )
        if info.compress_type not in _SUPPORTED_COMPRESSION:
            raise InstagramExportError(
                "unsupported_compression",
                "Accounts Center ZIP uses an unsupported compression method",
            )
        _validate_archive_entry_type(info)
        if info.file_size < 0 or info.compress_size < 0:
            raise InstagramExportError(
                "invalid_archive_size",
                "Accounts Center ZIP contains an invalid member size",
            )
        total_uncompressed += info.file_size
        if total_uncompressed > limits.max_total_uncompressed_bytes:
            raise InstagramExportError(
                "archive_uncompressed_too_large",
                "Accounts Center ZIP exceeds the configured uncompressed-size limit",
            )
        if info.file_size and not info.is_dir():
            if info.compress_size == 0:
                raise InstagramExportError(
                    "suspicious_compression_ratio",
                    "Accounts Center ZIP contains a suspicious compressed member",
                )
            if info.file_size / info.compress_size > limits.max_compression_ratio:
                raise InstagramExportError(
                    "suspicious_compression_ratio",
                    "Accounts Center ZIP exceeds the configured compression-ratio limit",
                )
        normalized.append(_NormalizedArchiveEntry(info=info, path=path))
    return tuple(normalized)


def _normalize_archive_path(
    info: zipfile.ZipInfo,
    *,
    limits: InstagramImportLimits,
) -> str:
    raw_name = info.orig_filename
    if (
        not raw_name
        or len(raw_name) > limits.max_archive_path_chars
        or "\x00" in raw_name
        or any(ord(character) < 32 for character in raw_name)
    ):
        raise InstagramExportError(
            "unsafe_archive_path",
            "Accounts Center ZIP contains an unsafe archive path",
        )
    if "\\" in raw_name or raw_name.startswith("/") or _DRIVE_PREFIX.match(raw_name):
        raise InstagramExportError(
            "unsafe_archive_path",
            "Accounts Center ZIP contains an unsafe archive path",
        )
    is_directory = raw_name.endswith("/")
    parts = raw_name[:-1].split("/") if is_directory else raw_name.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise InstagramExportError(
            "unsafe_archive_path",
            "Accounts Center ZIP contains an unsafe archive path",
        )
    normalized = "/".join(parts)
    return f"{normalized}/" if is_directory else normalized


def _validate_archive_entry_type(info: zipfile.ZipInfo) -> None:
    mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    if info.is_dir():
        if info.file_size != 0 or file_type not in {0, stat.S_IFDIR}:
            raise InstagramExportError(
                "non_regular_archive_entry",
                "Accounts Center ZIP contains a non-regular archive entry",
            )
        return
    if file_type not in {0, stat.S_IFREG}:
        raise InstagramExportError(
            "non_regular_archive_entry",
            "Accounts Center ZIP contains a non-regular archive entry",
        )


def _is_following_path(path: str) -> bool:
    folded = path.casefold()
    return folded == _FOLLOWING_PATH or folded.endswith(f"/{_FOLLOWING_PATH}")


def _parse_snapshot(
    payload: bytes,
    *,
    source_sha256: str,
    source_format: Literal["json", "zip"],
    recognized_path: str,
    ignored_archive_entry_count: int,
    limits: InstagramImportLimits,
) -> InstagramExportSnapshot:
    document = _load_json(payload, limits=limits)
    if not isinstance(document, Mapping):
        raise InstagramExportError(
            "unsupported_following_schema",
            "Instagram following JSON must contain a relationships_following object",
        )
    relationships = document.get("relationships_following")
    if not isinstance(relationships, list):
        raise InstagramExportError(
            "unsupported_following_schema",
            "Instagram following JSON must contain a relationships_following array",
        )
    if len(relationships) > limits.max_relationships:
        raise InstagramExportError(
            "too_many_relationships",
            "Instagram following JSON exceeds the configured relationship limit",
        )

    records: dict[str, InstagramFollowing] = {}
    duplicate_count = 0
    rejected_count = 0
    missing_timestamp_count = 0
    for relationship in relationships:
        parsed = _parse_relationship(relationship, limits=limits)
        if parsed is None:
            rejected_count += 1
            continue
        if parsed.relationship_timestamp is None:
            missing_timestamp_count += 1
        previous = records.get(parsed.handle)
        if previous is not None:
            duplicate_count += 1
            records[parsed.handle] = InstagramFollowing(
                handle=parsed.handle,
                relationship_timestamp=_latest_timestamp(
                    previous.relationship_timestamp,
                    parsed.relationship_timestamp,
                ),
            )
            continue
        records[parsed.handle] = parsed

    if relationships and not records:
        raise InstagramExportError(
            "no_valid_following_records",
            "Instagram following JSON contains no valid relationship records",
        )

    warnings: list[str] = []
    extra_top_level = len(set(document) - {"relationships_following"})
    for code, count in (
        ("ignored_archive_entries", ignored_archive_entry_count),
        ("ignored_top_level_fields", extra_top_level),
        ("rejected_relationships", rejected_count),
        ("duplicate_relationships", duplicate_count),
        ("missing_relationship_timestamps", missing_timestamp_count),
    ):
        if count:
            warnings.append(f"{code}:{count}")

    following = tuple(records[key] for key in sorted(records))
    return InstagramExportSnapshot(
        source_sha256=source_sha256,
        accepted_file_sha256=hashlib.sha256(payload).hexdigest(),
        source_format=source_format,
        parser_id=PARSER_ID,
        recognized_path=recognized_path,
        following=following,
        source_relationship_count=len(relationships),
        accepted_relationship_count=len(following),
        duplicate_relationship_count=duplicate_count,
        rejected_relationship_count=rejected_count,
        ignored_archive_entry_count=ignored_archive_entry_count,
        warnings=tuple(warnings),
    )


def _load_json(payload: bytes, *, limits: InstagramImportLimits) -> Mapping[str, Any] | list[Any]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InstagramExportError(
            "invalid_json_encoding",
            "Instagram following JSON must be UTF-8",
        ) from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_number,
        )
    except InstagramExportError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise InstagramExportError(
            "invalid_json",
            "Instagram following JSON is malformed",
        ) from exc
    if _json_depth(value) > limits.max_json_depth:
        raise InstagramExportError(
            "json_too_deep",
            "Instagram following JSON exceeds the configured nesting-depth limit",
        )
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> Mapping[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InstagramExportError(
                "duplicate_json_key",
                "Instagram following JSON contains a duplicate object key",
            )
        result[key] = value
    return MappingProxyType(result)


def _reject_non_finite_number(_: str) -> None:
    raise InstagramExportError(
        "non_finite_json_number",
        "Instagram following JSON contains a non-finite number",
    )


def _json_depth(value: Any) -> int:
    stack: list[tuple[Any, int]] = [(value, 1)]
    maximum = 0
    while stack:
        current, depth = stack.pop()
        maximum = max(maximum, depth)
        if isinstance(current, Mapping):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
    return maximum


def _parse_relationship(
    value: Any,
    *,
    limits: InstagramImportLimits,
) -> InstagramFollowing | None:
    if not isinstance(value, Mapping):
        return None
    string_data = value.get("string_list_data")
    if not isinstance(string_data, list) or len(string_data) != 1:
        return None
    item = string_data[0]
    if not isinstance(item, Mapping):
        return None
    nested_handle = item.get("value")
    title = value.get("title")
    # Accounts Center currently emits both the older nested `value` shape and
    # a compact shape where the relationship title is the only explicit
    # handle. In either form the normalized handle must agree with the HTTPS
    # Instagram profile URL before it is accepted.
    raw_handle = nested_handle if nested_handle is not None else title
    href = item.get("href")
    if not isinstance(raw_handle, str) or not isinstance(href, str):
        return None
    if len(raw_handle) > limits.max_scalar_chars or len(href) > limits.max_scalar_chars:
        return None
    handle = _canonical_handle(raw_handle)
    href_handle = _handle_from_instagram_url(href)
    if handle is None or href_handle is None or handle != href_handle:
        return None
    if title is not None and title != "":
        if (
            not isinstance(title, str)
            or len(title) > limits.max_scalar_chars
            or _canonical_handle(title) != handle
        ):
            return None
    timestamp = item.get("timestamp")
    if timestamp is not None:
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            return None
        if not 0 <= timestamp <= 4_102_444_800:
            return None
    return InstagramFollowing(handle=handle, relationship_timestamp=timestamp)


def _canonical_handle(value: str) -> str | None:
    if value != value.strip() or value.startswith("@"):
        return None
    normalized = value.lower()
    if not _HANDLE.fullmatch(normalized) or ".." in normalized:
        return None
    return normalized


def _handle_from_instagram_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"instagram.com", "www.instagram.com"}
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or "%" in parsed.path
    ):
        return None
    # Meta exports may encode the same profile as either `/handle/` or the
    # Accounts Center redirect form `/_u/handle/`. Both remain constrained to
    # one canonical handle on the exact Instagram host.
    path_match = re.fullmatch(r"/(?:_u/)?([^/]+)/?", parsed.path)
    if path_match is None:
        return None
    return _canonical_handle(path_match.group(1))


def _latest_timestamp(left: int | None, right: int | None) -> int | None:
    present = [value for value in (left, right) if value is not None]
    return max(present) if present else None
