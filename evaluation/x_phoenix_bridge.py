from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence


OFFICIAL_REPOSITORY = "https://github.com/xai-org/x-algorithm"


class PhoenixCheckoutError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PhoenixCheckout:
    repository: str
    commit: str
    remote: str
    pipeline_path: str
    pipeline_sha256: str
    license_sha256: str


@dataclass(frozen=True, slots=True)
class PhoenixInputs:
    artifacts_dir: str
    sequence_file: str
    sequence_sha256: str
    corpus_file: str
    corpus_sha256: str
    retrieval_config_sha256: str
    ranker_config_sha256: str


Runner = Callable[..., Any]


def inspect_checkout(
    checkout: str | Path,
    *,
    runner: Runner = subprocess.run,
    require_official_remote: bool = True,
) -> PhoenixCheckout:
    root = Path(checkout).resolve()
    pipeline = root / "phoenix" / "run_pipeline.py"
    license_path = root / "LICENSE"
    if not pipeline.is_file() or not license_path.is_file() or not (root / ".git").exists():
        raise PhoenixCheckoutError("expected a git checkout with LICENSE and phoenix/run_pipeline.py")
    commit = _git_output(root, ("rev-parse", "HEAD"), runner)
    remote = _git_output(root, ("config", "--get", "remote.origin.url"), runner)
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit.lower()):
        raise PhoenixCheckoutError("checkout commit must be a full git SHA")
    if require_official_remote and not _is_official_remote(remote):
        raise PhoenixCheckoutError("checkout remote is not xai-org/x-algorithm")
    return PhoenixCheckout(
        repository=OFFICIAL_REPOSITORY,
        commit=commit.lower(),
        remote=remote,
        pipeline_path=str(pipeline),
        pipeline_sha256=_sha256(pipeline),
        license_sha256=_sha256(license_path),
    )


def inspect_inputs(
    artifacts_dir: str | Path,
    *,
    sequence_file: str | Path | None = None,
    corpus_file: str | Path | None = None,
) -> PhoenixInputs:
    artifacts = Path(artifacts_dir).resolve()
    sequence = Path(sequence_file).resolve() if sequence_file else artifacts / "example_sequence.json"
    corpus = Path(corpus_file).resolve() if corpus_file else artifacts / "sports_corpus.npz"
    retrieval_config = artifacts / "retrieval" / "config.json"
    ranker_config = artifacts / "ranker" / "config.json"
    required = (sequence, corpus, retrieval_config, ranker_config)
    missing = [str(item) for item in required if not item.is_file()]
    if missing:
        raise PhoenixCheckoutError(f"missing Phoenix replay inputs: {', '.join(missing)}")
    try:
        sequence_value = json.loads(sequence.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhoenixCheckoutError("Phoenix sequence must be valid UTF-8 JSON") from exc
    if not isinstance(sequence_value, dict):
        raise PhoenixCheckoutError("Phoenix sequence root must be an object")
    return PhoenixInputs(
        artifacts_dir=str(artifacts),
        sequence_file=str(sequence),
        sequence_sha256=_sha256(sequence),
        corpus_file=str(corpus),
        corpus_sha256=_sha256(corpus),
        retrieval_config_sha256=_sha256(retrieval_config),
        ranker_config_sha256=_sha256(ranker_config),
    )


def build_command(
    checkout: str | Path,
    inputs: PhoenixInputs,
    *,
    top_k_retrieval: int = 200,
    top_k_display: int = 30,
) -> tuple[str, ...]:
    if top_k_retrieval < 1 or top_k_display < 1 or top_k_display > top_k_retrieval:
        raise ValueError("Phoenix top-k values must be positive and display cannot exceed retrieval")
    pipeline = Path(checkout).resolve() / "phoenix" / "run_pipeline.py"
    return (
        "uv",
        "run",
        str(pipeline),
        "--artifacts_dir",
        inputs.artifacts_dir,
        "--sequence_file",
        inputs.sequence_file,
        "--corpus_file",
        inputs.corpus_file,
        "--top_k_retrieval",
        str(top_k_retrieval),
        "--top_k_display",
        str(top_k_display),
    )


def run_replay(
    checkout: str | Path,
    artifacts_dir: str | Path,
    *,
    sequence_file: str | Path | None = None,
    corpus_file: str | Path | None = None,
    top_k_retrieval: int = 200,
    top_k_display: int = 30,
    runner: Runner = subprocess.run,
    require_official_remote: bool = True,
) -> dict[str, Any]:
    root = Path(checkout).resolve()
    checkout_receipt = inspect_checkout(
        root,
        runner=runner,
        require_official_remote=require_official_remote,
    )
    inputs = inspect_inputs(
        artifacts_dir,
        sequence_file=sequence_file,
        corpus_file=corpus_file,
    )
    command = build_command(
        root,
        inputs,
        top_k_retrieval=top_k_retrieval,
        top_k_display=top_k_display,
    )
    result = runner(
        list(command),
        cwd=str(root / "phoenix"),
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "schema": "feed-passport/x-phoenix-replay/v1",
        "mode": "offline_public_model_replay",
        "live_feed_changed": False,
        "warning": (
            "This is an offline replay of the public Phoenix release. It is not the account's live For You feed "
            "and does not copy X's private serving state or continuously trained production checkpoint."
        ),
        "checkout": asdict(checkout_receipt),
        "inputs": asdict(inputs),
        "command": list(command),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "exit_code": int(result.returncode),
        "stdout": str(result.stdout),
        "stderr": str(result.stderr),
    }


def write_receipt(report: dict[str, Any], output: str | Path) -> Path:
    destination = Path(output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def _git_output(root: Path, arguments: Sequence[str], runner: Runner) -> str:
    result = runner(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if int(result.returncode) != 0:
        raise PhoenixCheckoutError(f"git {' '.join(arguments)} failed")
    return str(result.stdout).strip()


def _is_official_remote(remote: str) -> bool:
    normalized = remote.strip().lower().removesuffix(".git").replace("git@github.com:", "https://github.com/")
    return normalized == OFFICIAL_REPOSITORY.lower()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
