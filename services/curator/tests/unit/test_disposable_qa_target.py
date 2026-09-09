from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from pathlib import Path

import pytest

from feed_passport.api.disposable_qa import (
    DATABASE_CLAIM_ENV,
    DATABASE_ENV,
    RUN_TOKEN_ENV,
    TARGET_SCHEMA,
    load_disposable_qa_target,
)


def _owned_database(tmp_path: Path) -> Path:
    directory = tmp_path / "feed-passport-browser-platforms-test"
    directory.mkdir(parents=True)
    database = directory / "curator.db"
    database.write_bytes(b"sqlite-placeholder")
    return database.resolve()


def _environment(database: Path) -> dict[str, str]:
    token = str(uuid.uuid4())
    return {
        RUN_TOKEN_ENV: token,
        DATABASE_CLAIM_ENV: str(database),
        DATABASE_ENV: str(database),
    }


def test_disposable_target_is_absent_without_internal_configuration() -> None:
    assert load_disposable_qa_target(
        auth_mode="demo",
        loopback_only=True,
        owned_bundle=True,
        environment={},
    ) is None


def test_disposable_target_binds_one_owned_temporary_database(tmp_path: Path) -> None:
    database = _owned_database(tmp_path)
    environment = _environment(database)
    canonical_path = database.as_posix().casefold() if os.name == "nt" else database.as_posix()
    expected_binding = hmac.new(
        environment[RUN_TOKEN_ENV].encode("ascii"),
        canonical_path.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    assert load_disposable_qa_target(
        auth_mode="demo",
        loopback_only=True,
        owned_bundle=True,
        environment=environment,
    ) == {
        "schema": TARGET_SCHEMA,
        "run_token": environment[RUN_TOKEN_ENV],
        "database_binding": expected_binding,
    }


@pytest.mark.parametrize(
    ("auth_mode", "loopback_only", "owned_bundle"),
    [
        ("oidc", True, True),
        ("demo", False, True),
        ("demo", True, False),
    ],
)
def test_disposable_target_rejects_non_owned_runtime_shapes(
    tmp_path: Path,
    auth_mode: str,
    loopback_only: bool,
    owned_bundle: bool,
) -> None:
    environment = _environment(_owned_database(tmp_path))
    with pytest.raises(ValueError, match="owned loopback demo"):
        load_disposable_qa_target(
            auth_mode=auth_mode,
            loopback_only=loopback_only,
            owned_bundle=owned_bundle,
            environment=environment,
        )


def test_disposable_target_rejects_partial_or_redirected_configuration(tmp_path: Path) -> None:
    database = _owned_database(tmp_path)
    environment = _environment(database)
    with pytest.raises(ValueError, match="configured together"):
        load_disposable_qa_target(
            auth_mode="demo",
            loopback_only=True,
            owned_bundle=True,
            environment={RUN_TOKEN_ENV: environment[RUN_TOKEN_ENV]},
        )

    other = database.parent / "other.db"
    other.write_bytes(b"other")
    environment[DATABASE_CLAIM_ENV] = str(other)
    with pytest.raises(ValueError, match="does not match"):
        load_disposable_qa_target(
            auth_mode="demo",
            loopback_only=True,
            owned_bundle=True,
            environment=environment,
        )


def test_disposable_target_rejects_valued_or_noncanonical_paths(tmp_path: Path) -> None:
    database = tmp_path / "valued.db"
    database.write_bytes(b"valued")
    environment = _environment(database.resolve())
    with pytest.raises(ValueError, match="path shape"):
        load_disposable_qa_target(
            auth_mode="demo",
            loopback_only=True,
            owned_bundle=True,
            environment=environment,
        )

    owned_database = _owned_database(tmp_path / "second")
    bad_token_environment = _environment(owned_database)
    bad_token_environment[RUN_TOKEN_ENV] = "not-a-uuid"
    with pytest.raises(ValueError, match="canonical UUIDv4"):
        load_disposable_qa_target(
            auth_mode="demo",
            loopback_only=True,
            owned_bundle=True,
            environment=bad_token_environment,
        )
