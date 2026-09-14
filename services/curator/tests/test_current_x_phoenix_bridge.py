from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from evaluation.current_x_phoenix_bridge import (
    MODE,
    OFFICIAL_REPOSITORY,
    REQUIRED_MANIFEST_FILES,
    REQUIRED_REFERENCE_SENTINELS,
    CurrentPhoenixCheckoutError,
    CurrentPhoenixContractError,
    CurrentPhoenixExecutionError,
    GeneratorCommand,
    build_generator_command,
    inspect_checkout,
    run_generator_suite,
    write_receipt,
)


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class InjectedRunner:
    def __init__(
        self,
        tracked_files: list[str],
        *,
        remote: str = OFFICIAL_REPOSITORY + ".git",
        tracked_sources_dirty: bool = False,
        process_results: list[SimpleNamespace] | None = None,
    ) -> None:
        self.tracked_files = tracked_files
        self.remote = remote
        self.tracked_sources_dirty = tracked_sources_dirty
        self.process_results = list(process_results or [])
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command: list[str], **kwargs: object) -> SimpleNamespace:
        self.calls.append((command, kwargs))
        if command[0] != "git":
            if not self.process_results:
                raise AssertionError(f"unexpected generator command: {command!r}")
            return self.process_results.pop(0)

        arguments = command[3:]
        if arguments == ["rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout=("a" * 40 + "\n").encode(), stderr=b"")
        if arguments == ["rev-parse", "HEAD^{tree}"]:
            return SimpleNamespace(returncode=0, stdout=("b" * 40 + "\n").encode(), stderr=b"")
        if arguments == ["config", "--get", "remote.origin.url"]:
            return SimpleNamespace(returncode=0, stdout=(self.remote + "\n").encode(), stderr=b"")
        if arguments[:2] == ["ls-files", "-z"]:
            payload = b"\0".join(path.encode() for path in self.tracked_files) + b"\0"
            return SimpleNamespace(returncode=0, stdout=payload, stderr=b"")
        if arguments[:3] == ["diff", "--quiet", "HEAD"]:
            return SimpleNamespace(
                returncode=1 if self.tracked_sources_dirty else 0,
                stdout=b"",
                stderr=b"",
            )
        raise AssertionError(f"unexpected git command: {command!r}")


class CurrentPhoenixBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "x-algorithm"
        (self.root / ".git").mkdir(parents=True)

        self.fixture_bytes: dict[str, bytes] = {}
        required = set(REQUIRED_MANIFEST_FILES) | set(REQUIRED_REFERENCE_SENTINELS)
        required.add("phoenix/reference/helper_fixture.py")
        for relative_path in sorted(required):
            content = f"fixture for {relative_path}\n".encode()
            destination = self.root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            self.fixture_bytes[relative_path] = content
        self.tracked_files = sorted(required)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_inspection_hashes_manifests_and_every_tracked_reference_file(self) -> None:
        runner = InjectedRunner(self.tracked_files)

        checkout = inspect_checkout(self.root, runner=runner, expected_commit="a" * 40)

        self.assertEqual(checkout.commit, "a" * 40)
        self.assertEqual(checkout.tree, "b" * 40)
        self.assertEqual(checkout.remote, OFFICIAL_REPOSITORY + ".git")
        self.assertEqual(checkout.git_inspection_backend, "injected_runner")
        evidence = {item.path: item for item in checkout.source_files}
        self.assertEqual(set(evidence), set(self.tracked_files))
        helper = evidence["phoenix/reference/helper_fixture.py"]
        self.assertEqual(helper.bytes, len(self.fixture_bytes[helper.path]))
        self.assertEqual(helper.sha256, sha256(self.fixture_bytes[helper.path]))

    def test_injected_runner_receipt_never_claims_upstream_process_execution(self) -> None:
        outputs = [
            SimpleNamespace(returncode=0, stdout=b"world self-check ok\n", stderr=b""),
            SimpleNamespace(returncode=0, stdout=b"dump self-check ok\n", stderr=b"warning\n"),
        ]
        runner = InjectedRunner(self.tracked_files, process_results=outputs)
        suite = (
            GeneratorCommand(name="world-self-check", script="reference/world.py"),
            GeneratorCommand(
                name="dump-self-check",
                script="reference/dump_gen.py",
                arguments=("--out", "fixture-output", "--self-check"),
            ),
        )

        report = run_generator_suite(
            self.root,
            suite,
            runner=runner,
            python_executable="python",
            expected_commit="a" * 40,
        )

        self.assertEqual(report["mode"], MODE)
        self.assertFalse(report["suite_passed"])
        self.assertTrue(report["runner_reported_success"])
        self.assertTrue(report["suite_completed"])
        self.assertFalse(report["ranking_executed"])
        self.assertEqual(report["execution_backend"], "injected_runner")
        self.assertFalse(report["truth"]["upstream_processes_executed"])
        self.assertFalse(report["truth"]["current_upstream_sources_hashed"])
        self.assertTrue(report["truth"]["commands_are_allowlisted_reference_generators"])
        self.assertFalse(report["truth"]["production_ranking_fidelity_established"])
        self.assertFalse(report["truth"]["live_feed_changed"])
        first = report["commands"][0]
        self.assertEqual(first["command"][0], "python")
        self.assertEqual(Path(first["command"][1]).parts[-2:], ("reference", "world.py"))
        self.assertEqual(first["exit_code"], 0)
        self.assertEqual(first["stdout_sha256"], sha256(b"world self-check ok\n"))
        self.assertEqual(first["stderr_sha256"], sha256(b""))
        self.assertNotIn("stdout", first)
        self.assertNotIn("world self-check ok", json.dumps(report))

    def test_unofficial_missing_modified_and_unpinned_sources_fail_closed(self) -> None:
        with self.assertRaisesRegex(CurrentPhoenixCheckoutError, "official xai-org"):
            inspect_checkout(
                self.root,
                runner=InjectedRunner(self.tracked_files, remote="https://github.com/example/fork"),
            )

        missing = self.root / REQUIRED_REFERENCE_SENTINELS[-1]
        missing.unlink()
        with self.assertRaisesRegex(CurrentPhoenixCheckoutError, "missing required"):
            inspect_checkout(self.root, runner=InjectedRunner(self.tracked_files))
        missing.write_bytes(self.fixture_bytes[REQUIRED_REFERENCE_SENTINELS[-1]])

        with self.assertRaisesRegex(CurrentPhoenixCheckoutError, "differ from commit"):
            inspect_checkout(
                self.root,
                runner=InjectedRunner(self.tracked_files, tracked_sources_dirty=True),
            )

        with self.assertRaisesRegex(CurrentPhoenixCheckoutError, "expected commit"):
            inspect_checkout(
                self.root,
                runner=InjectedRunner(self.tracked_files),
                expected_commit="c" * 40,
            )

    def test_only_allowlisted_generator_scripts_and_python_launchers_are_accepted(self) -> None:
        with self.assertRaisesRegex(CurrentPhoenixContractError, "not an allowlisted"):
            build_generator_command(
                self.root,
                GeneratorCommand(name="train", script="reference/train_synth.py"),
                python_executable="python",
            )
        with self.assertRaisesRegex(CurrentPhoenixContractError, "Python executable"):
            build_generator_command(
                self.root,
                GeneratorCommand(name="world", script="reference/world.py"),
                python_executable="bash",
            )
        with self.assertRaisesRegex(CurrentPhoenixContractError, "not an allowlisted"):
            build_generator_command(
                self.root,
                GeneratorCommand(name="escape", script="../reference/world.py"),
                python_executable="python",
            )

    def test_nonzero_generator_exit_raises_with_a_hash_only_failure_receipt(self) -> None:
        outputs = [SimpleNamespace(returncode=9, stdout=b"partial output\n", stderr=b"failed\n")]
        runner = InjectedRunner(self.tracked_files, process_results=outputs)
        suite = (
            GeneratorCommand(name="world", script="reference/world.py"),
            GeneratorCommand(name="never-run", script="reference/oss_recsys_synth.py"),
        )

        with self.assertRaises(CurrentPhoenixExecutionError) as raised:
            run_generator_suite(
                self.root,
                suite,
                runner=runner,
                python_executable="python",
            )

        receipt = raised.exception.receipt
        self.assertFalse(receipt["suite_passed"])
        self.assertFalse(receipt["suite_completed"])
        self.assertFalse(receipt["ranking_executed"])
        self.assertEqual(receipt["commands_executed"], 1)
        self.assertEqual(receipt["commands"][0]["exit_code"], 9)
        self.assertEqual(receipt["commands"][0]["stderr_sha256"], sha256(b"failed\n"))
        self.assertNotIn("failed\n", json.dumps(receipt))
        generator_calls = [command for command, _ in runner.calls if command[0] != "git"]
        self.assertEqual(len(generator_calls), 1)

    def test_write_receipt_rejects_any_ranking_claim(self) -> None:
        output = self.root / "receipt.json"
        with self.assertRaisesRegex(CurrentPhoenixContractError, "ranking_executed"):
            write_receipt(
                {
                    "schema": "feed-passport/x-phoenix-current-reference-generators/v1",
                    "mode": MODE,
                    "ranking_executed": True,
                    "truth": {"ranking_executed": True},
                },
                output,
            )
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
