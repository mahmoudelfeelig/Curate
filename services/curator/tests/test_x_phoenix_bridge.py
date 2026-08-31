from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from evaluation.x_phoenix_bridge import (
    OFFICIAL_REPOSITORY,
    PhoenixCheckoutError,
    build_command,
    inspect_checkout,
    inspect_inputs,
    run_replay,
)


class StubRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command: list[str], **kwargs: object) -> SimpleNamespace:
        self.calls.append((command, kwargs))
        if command[:3] == ["git", "-C", command[2]] and command[-2:] == ["rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="a" * 40 + "\n", stderr="")
        if command[0] == "git" and command[-3:] == ["config", "--get", "remote.origin.url"]:
            return SimpleNamespace(returncode=0, stdout=OFFICIAL_REPOSITORY + ".git\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="PIPELINE RESULTS\n", stderr="")


class PhoenixBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "x-algorithm"
        (self.root / ".git").mkdir(parents=True)
        (self.root / "phoenix").mkdir()
        (self.root / "phoenix" / "run_pipeline.py").write_text("print('fixture')\n", encoding="utf-8")
        (self.root / "LICENSE").write_text("Apache License 2.0 fixture\n", encoding="utf-8")
        self.artifacts = self.root / "phoenix" / "artifacts" / "oss"
        (self.artifacts / "retrieval").mkdir(parents=True)
        (self.artifacts / "ranker").mkdir()
        (self.artifacts / "retrieval" / "config.json").write_text("{}\n", encoding="utf-8")
        (self.artifacts / "ranker" / "config.json").write_text("{}\n", encoding="utf-8")
        (self.artifacts / "example_sequence.json").write_text('{"user_id": 1}\n', encoding="utf-8")
        (self.artifacts / "sports_corpus.npz").write_bytes(b"fixture-corpus")
        self.runner = StubRunner()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_provenance_command_and_receipt_are_explicitly_offline(self) -> None:
        checkout = inspect_checkout(self.root, runner=self.runner)
        self.assertEqual(checkout.commit, "a" * 40)
        inputs = inspect_inputs(self.artifacts)
        command = build_command(self.root, inputs, top_k_retrieval=20, top_k_display=5)
        self.assertEqual(command[:2], ("uv", "run"))
        self.assertIn("--sequence_file", command)
        report = run_replay(
            self.root,
            self.artifacts,
            top_k_retrieval=20,
            top_k_display=5,
            runner=self.runner,
        )
        self.assertEqual(report["mode"], "offline_public_model_replay")
        self.assertFalse(report["live_feed_changed"])
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["checkout"]["commit"], "a" * 40)

    def test_missing_or_invalid_inputs_fail_before_execution(self) -> None:
        (self.artifacts / "sports_corpus.npz").unlink()
        with self.assertRaises(PhoenixCheckoutError):
            inspect_inputs(self.artifacts)
        with self.assertRaises(ValueError):
            build_command(
                self.root,
                inspect_inputs(self.artifacts, corpus_file=self.root / "LICENSE"),
                top_k_retrieval=5,
                top_k_display=6,
            )


if __name__ == "__main__":
    unittest.main()
