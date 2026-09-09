from __future__ import annotations

import hashlib
import hmac
import os
import tempfile
import uuid
from pathlib import Path
from typing import Mapping


RUN_TOKEN_ENV = "FEED_PASSPORT_QA_DISPOSABLE_RUN_TOKEN"
DATABASE_CLAIM_ENV = "FEED_PASSPORT_QA_DISPOSABLE_DATABASE_PATH"
DATABASE_ENV = "FEED_PASSPORT_DB_PATH"
TARGET_SCHEMA = "feed-passport/disposable-api-target/v1"
DIRECTORY_PREFIX = "feed-passport-browser-platforms-"


def _canonical_database_path(value: Path) -> str:
    normalized = value.as_posix()
    return normalized.casefold() if os.name == "nt" else normalized


def load_disposable_qa_target(
    *,
    auth_mode: str,
    loopback_only: bool,
    owned_bundle: bool,
    environment: Mapping[str, str] | None = None,
) -> dict[str, str] | None:
    """Return a public one-run ownership proof for the self-owned browser QA API.

    These internal variables are deliberately absent from ``.env.example``. A
    normal API has no proof. Partial or manually redirected configuration fails
    startup before any browser can mutate state.
    """

    env = os.environ if environment is None else environment
    token = env.get(RUN_TOKEN_ENV, "").strip()
    claimed_database = env.get(DATABASE_CLAIM_ENV, "").strip()
    if bool(token) != bool(claimed_database):
        raise ValueError("disposable QA run token and database claim must be configured together")
    if not token:
        return None
    if auth_mode != "demo" or not loopback_only or not owned_bundle:
        raise ValueError("disposable QA ownership proof requires an owned loopback demo service")
    try:
        parsed_token = uuid.UUID(token)
    except ValueError as exc:
        raise ValueError("disposable QA run token must be a canonical UUIDv4") from exc
    if parsed_token.version != 4 or str(parsed_token) != token:
        raise ValueError("disposable QA run token must be a canonical UUIDv4")

    configured_database = env.get(DATABASE_ENV, "").strip()
    if not configured_database:
        raise ValueError("disposable QA ownership proof requires an explicit database path")
    actual_path = Path(configured_database).expanduser().resolve(strict=True)
    claim_path = Path(claimed_database).expanduser().resolve(strict=True)
    if actual_path != claim_path or not actual_path.is_file():
        raise ValueError("disposable QA database claim does not match the active database file")
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    try:
        actual_path.relative_to(temporary_root)
    except ValueError as exc:
        raise ValueError("disposable QA database must be inside the operating-system temporary directory") from exc
    if not actual_path.parent.name.startswith(DIRECTORY_PREFIX) or actual_path.name != "curator.db":
        raise ValueError("disposable QA database does not use the owned harness path shape")

    binding = hmac.new(
        token.encode("ascii"),
        _canonical_database_path(actual_path).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "schema": TARGET_SCHEMA,
        "run_token": token,
        "database_binding": binding,
    }
