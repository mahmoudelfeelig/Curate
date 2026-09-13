from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path
from typing import Any


REQUIRED_PREFIXES = (
    "agentcore_main.py",
    "bedrock_agentcore/",
    "boto3/",
    "feed_passport/runtime/agentcore_app.py",
    "pydantic/",
    "strands/",
    "package-manifest.json",
)
MANIFEST_FORMAT = "feed-passport-agentcore-package/v1"
GIT_REVISION = re.compile(r"[0-9a-f]{40,64}")
MAX_COMPRESSED_BYTES = 250 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 750 * 1024 * 1024


def validate_archive(archive_path: Path, *, expected_source_commit: str) -> dict[str, Any]:
    archive_path = archive_path.resolve(strict=True)
    if not GIT_REVISION.fullmatch(expected_source_commit):
        raise SystemExit("expected source commit must be a lowercase Git revision")
    if archive_path.stat().st_size > MAX_COMPRESSED_BYTES:
        raise SystemExit("AgentCore archive exceeds the 250 MiB compressed service limit")
    with zipfile.ZipFile(archive_path) as archive:
        bad = archive.testzip()
        if bad:
            raise SystemExit(f"corrupt member in AgentCore archive: {bad}")
        members = archive.infolist()
        names = [member.filename for member in members]
        if len(names) != len(set(names)):
            raise SystemExit("AgentCore archive contains duplicate member names")
        unsafe = [
            member
            for member in members
            if member.filename.startswith(("/", "\\"))
            or ".." in Path(member.filename).parts
            or stat.S_ISLNK(member.external_attr >> 16)
        ]
        if unsafe:
            raise SystemExit(f"AgentCore archive contains unsafe member: {unsafe[0].filename}")
        missing = [
            required
            for required in REQUIRED_PREFIXES
            if not any(name == required or name.startswith(required) for name in names)
        ]
        if missing:
            raise SystemExit(f"AgentCore archive is missing: {', '.join(missing)}")
        forbidden = [
            name
            for name in names
            if name.lower().endswith((".dll", ".exe", ".pyd"))
            or name.startswith(".venv/")
        ]
        if forbidden:
            raise SystemExit(f"AgentCore archive contains forbidden member: {forbidden[0]}")
        uncompressed_bytes = sum(member.file_size for member in members)
        if uncompressed_bytes > MAX_UNCOMPRESSED_BYTES:
            raise SystemExit("AgentCore archive exceeds the 750 MiB uncompressed service limit")
        try:
            manifest = json.loads(archive.read("package-manifest.json").decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SystemExit("AgentCore package manifest is missing or invalid") from exc
        expected_manifest = {
            "format": MANIFEST_FORMAT,
            "source_commit": expected_source_commit,
            "source_worktree_dirty": False,
            "python_runtime": "PYTHON_3_13",
            "architecture": "linux_arm64",
            "entrypoint": "agentcore_main.py",
        }
        packaging_backend = manifest.get("packaging_backend") if isinstance(manifest, dict) else None
        manifest_without_backend = (
            {key: value for key, value in manifest.items() if key != "packaging_backend"}
            if isinstance(manifest, dict)
            else None
        )
        if (
            packaging_backend not in {"PipCrossPlatform", "Docker"}
            or manifest_without_backend != expected_manifest
        ):
            raise SystemExit(
                "AgentCore package manifest must bind a clean exact source commit and runtime"
            )

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    return {
        "archive": str(archive_path),
        "bytes": archive_path.stat().st_size,
        "uncompressed_bytes": uncompressed_bytes,
        "sha256": digest,
        "entrypoint": "agentcore_main.py",
        "runtime": "PYTHON_3_13",
        "architecture": "linux_arm64",
        "source_commit": expected_source_commit,
        "source_worktree_dirty": False,
        "packaging_backend": packaging_backend,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--expected-source-commit", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            validate_archive(
                args.archive,
                expected_source_commit=args.expected_source_commit,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
