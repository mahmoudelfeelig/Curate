from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_package.py"
SPEC = importlib.util.spec_from_file_location("feed_passport_validate_package", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

REVISION = "a" * 40
REQUIRED_MEMBERS = {
    "agentcore_main.py": b"handler = object()\n",
    "bedrock_agentcore/__init__.py": b"",
    "boto3/__init__.py": b"",
    "feed_passport/runtime/agentcore_app.py": b"",
    "pydantic/__init__.py": b"",
    "strands/__init__.py": b"",
}


def manifest(*, revision: str = REVISION, dirty: bool = False) -> dict[str, object]:
    return {
        "format": "feed-passport-agentcore-package/v1",
        "source_commit": revision,
        "source_worktree_dirty": dirty,
        "python_runtime": "PYTHON_3_13",
        "architecture": "linux_arm64",
        "entrypoint": "agentcore_main.py",
    }


class PackageValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.archive = Path(self.temp.name) / "agentcore.zip"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_archive(self, package_manifest: dict[str, object] | None) -> None:
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, value in REQUIRED_MEMBERS.items():
                archive.writestr(name, value)
            if package_manifest is not None:
                archive.writestr(
                    "package-manifest.json",
                    json.dumps(package_manifest, sort_keys=True).encode("utf-8"),
                )

    def test_accepts_only_a_clean_manifest_bound_to_exact_expected_revision(self) -> None:
        self.write_archive(manifest())

        evidence = MODULE.validate_archive(
            self.archive,
            expected_source_commit=REVISION,
        )

        self.assertEqual(evidence["source_commit"], REVISION)
        self.assertIs(evidence["source_worktree_dirty"], False)
        self.assertEqual(evidence["architecture"], "linux_arm64")

    def test_rejects_missing_dirty_and_stale_manifests(self) -> None:
        for package_manifest, expected in (
            (None, "missing"),
            (manifest(dirty=True), "clean exact source commit"),
            (manifest(revision="b" * 40), "clean exact source commit"),
        ):
            with self.subTest(expected=expected, package_manifest=package_manifest):
                self.write_archive(package_manifest)
                with self.assertRaisesRegex(SystemExit, expected):
                    MODULE.validate_archive(
                        self.archive,
                        expected_source_commit=REVISION,
                    )


if __name__ == "__main__":
    unittest.main()
