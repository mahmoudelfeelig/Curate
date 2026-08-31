from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


OFFICIAL_REPOSITORY = "https://github.com/xai-org/x-algorithm"
RECEIPT_SCHEMA = "feed-passport/x-phoenix-current-reference-generators/v1"
MODE = "current_upstream_reference_generators_only"

# These files identify the current (August 2026) Phoenix export rather than the
# older downloadable-artifact demo. Every tracked file below phoenix/reference
# is also discovered from git and hashed by inspect_checkout.
REQUIRED_MANIFEST_FILES = (
    "LICENSE",
    "phoenix/Cargo.lock",
    "phoenix/Cargo.toml",
    "phoenix/NOTICE",
    "phoenix/pyproject.toml",
    "phoenix/QUICKSTART.md",
    "phoenix/README.md",
    "phoenix/THIRD_PARTY_NOTICES.md",
    "phoenix/TRAINING.md",
)
REQUIRED_REFERENCE_SENTINELS = (
    "phoenix/reference/README.md",
    "phoenix/reference/dump_gen.py",
    "phoenix/reference/gen_recs_artifacts_gen.py",
    "phoenix/reference/oss_recsys_synth.py",
    "phoenix/reference/retrieve_then_rank.py",
    "phoenix/reference/train_synth.py",
    "phoenix/reference/world.py",
    "phoenix/reference/world_snapshots.py",
)

# Only the synthetic-world tools described in phoenix/reference/README.md are
# executable through this bridge. Training, serving, retrieval, and ranking
# entrypoints are deliberately absent.
ALLOWED_GENERATOR_SCRIPTS = frozenset(
    {
        "reference/dump_gen.py",
        "reference/gen_recs_artifacts_gen.py",
        "reference/oss_recsys_synth.py",
        "reference/world.py",
        "reference/world_snapshots.py",
    }
)
SAFE_ENVIRONMENT_KEYS = (
    "CUDA_VISIBLE_DEVICES",
    "JAX_PLATFORMS",
    "PHOENIX_INDEX_BASE",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONHASHSEED",
)


class CurrentPhoenixBridgeError(RuntimeError):
    pass


class CurrentPhoenixCheckoutError(CurrentPhoenixBridgeError):
    pass


class CurrentPhoenixContractError(CurrentPhoenixBridgeError):
    pass


class CurrentPhoenixExecutionError(CurrentPhoenixBridgeError):
    def __init__(self, message: str, *, receipt: dict[str, Any]) -> None:
        super().__init__(message)
        self.receipt = receipt


@dataclass(frozen=True, slots=True)
class SourceFileEvidence:
    path: str
    bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class CurrentPhoenixCheckout:
    repository: str
    checkout_root: str
    commit: str
    tree: str
    remote: str
    git_inspection_backend: str
    source_files: tuple[SourceFileEvidence, ...]


@dataclass(frozen=True, slots=True)
class GeneratorCommand:
    name: str
    script: str
    arguments: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CommandEvidence:
    name: str
    command: tuple[str, ...]
    cwd: str
    started_at: str
    finished_at: str
    duration_ms: float
    exit_code: int | None
    timed_out: bool
    launch_error: bool
    stdout_bytes: int
    stdout_sha256: str
    stderr_bytes: int
    stderr_sha256: str


Runner = Callable[..., Any]
Clock = Callable[[], datetime]
Timer = Callable[[], float]


def inspect_checkout(
    checkout: str | Path,
    *,
    runner: Runner = subprocess.run,
    expected_commit: str | None = None,
) -> CurrentPhoenixCheckout:
    root = Path(checkout).resolve()
    if not root.is_dir() or not (root / ".git").exists():
        raise CurrentPhoenixCheckoutError("expected a git checkout of xai-org/x-algorithm")

    commit = _git_text(root, ("rev-parse", "HEAD"), runner).lower()
    tree = _git_text(root, ("rev-parse", "HEAD^{tree}"), runner).lower()
    remote = _git_text(root, ("config", "--get", "remote.origin.url"), runner)
    _require_full_git_sha(commit, "checkout commit")
    _require_full_git_sha(tree, "checkout tree")
    if not _is_official_remote(remote):
        raise CurrentPhoenixCheckoutError(
            "checkout remote is not the official xai-org/x-algorithm repository"
        )

    if expected_commit is not None:
        normalized_expected = expected_commit.strip().lower()
        _require_full_git_sha(normalized_expected, "expected commit")
        if commit != normalized_expected:
            raise CurrentPhoenixCheckoutError(
                f"checkout commit {commit} does not match expected commit {normalized_expected}"
            )

    tracked_paths = _tracked_source_paths(root, runner)
    required = set(REQUIRED_MANIFEST_FILES) | set(REQUIRED_REFERENCE_SENTINELS)
    missing_from_git = sorted(required - set(tracked_paths))
    if missing_from_git:
        raise CurrentPhoenixCheckoutError(
            "current Phoenix required sources are not tracked: " + ", ".join(missing_from_git)
        )

    missing_on_disk = [relative for relative in tracked_paths if not (root / relative).is_file()]
    if missing_on_disk:
        raise CurrentPhoenixCheckoutError(
            "missing required current Phoenix source files: " + ", ".join(missing_on_disk)
        )

    diff = runner(
        ["git", "-C", str(root), "diff", "--quiet", "HEAD", "--", *tracked_paths],
        capture_output=True,
        text=False,
        check=False,
    )
    diff_exit = int(diff.returncode)
    if diff_exit == 1:
        raise CurrentPhoenixCheckoutError("tracked Phoenix manifest/reference sources differ from commit")
    if diff_exit != 0:
        raise CurrentPhoenixCheckoutError("git diff failed while checking Phoenix source provenance")

    source_files = tuple(_source_file_evidence(root, relative) for relative in tracked_paths)
    return CurrentPhoenixCheckout(
        repository=OFFICIAL_REPOSITORY,
        checkout_root=str(root),
        commit=commit,
        tree=tree,
        remote=remote,
        git_inspection_backend=_execution_backend(runner),
        source_files=source_files,
    )


def build_generator_command(
    checkout: str | Path,
    specification: GeneratorCommand,
    *,
    python_executable: str | Path = sys.executable,
) -> tuple[str, ...]:
    name = _require_plain_string(specification.name, "generator command name")
    if any(ord(character) < 32 for character in name):
        raise CurrentPhoenixContractError("generator command name cannot contain control characters")

    script = _require_plain_string(specification.script, "generator script").replace("\\", "/")
    if script not in ALLOWED_GENERATOR_SCRIPTS:
        raise CurrentPhoenixContractError(f"{script!r} is not an allowlisted Phoenix reference generator")

    executable = _require_plain_string(str(python_executable), "Python executable")
    executable_name = executable.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?", executable_name) is None:
        raise CurrentPhoenixContractError("Python executable must name a python interpreter")

    arguments: list[str] = []
    for index, argument in enumerate(specification.arguments):
        arguments.append(_require_plain_string(argument, f"generator argument {index}"))

    root = Path(checkout).resolve()
    script_path = (root / "phoenix" / script).resolve()
    phoenix_root = (root / "phoenix").resolve()
    if not _is_within(script_path, phoenix_root) or not script_path.is_file():
        raise CurrentPhoenixContractError(f"allowlisted generator is missing from checkout: {script}")
    return (executable, str(script_path), *arguments)


def run_generator_suite(
    checkout: str | Path,
    suite: Sequence[GeneratorCommand],
    *,
    runner: Runner = subprocess.run,
    python_executable: str | Path = sys.executable,
    expected_commit: str | None = None,
    timeout_seconds: float = 300.0,
    environment: Mapping[str, str] | None = None,
    utc_now: Clock | None = None,
    timer: Timer = time.perf_counter,
) -> dict[str, Any]:
    specifications = tuple(suite)
    if not specifications:
        raise CurrentPhoenixContractError("Phoenix generator suite cannot be empty")
    names = [specification.name for specification in specifications]
    if len(names) != len(set(names)):
        raise CurrentPhoenixContractError("Phoenix generator command names must be unique")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise CurrentPhoenixContractError("timeout_seconds must be a positive finite number")

    root = Path(checkout).resolve()
    commands = tuple(
        build_generator_command(root, specification, python_executable=python_executable)
        for specification in specifications
    )
    source = inspect_checkout(root, runner=runner, expected_commit=expected_commit)
    backend = _execution_backend(runner)
    now = utc_now or (lambda: datetime.now(timezone.utc))
    process_environment = _process_environment(environment)
    environment_receipt = _environment_provenance(process_environment, python_executable)

    suite_started_at = _iso_utc(now())
    suite_timer_start = timer()
    evidence: list[CommandEvidence] = []

    for specification, command in zip(specifications, commands):
        command_started_at = _iso_utc(now())
        command_timer_start = timer()
        exit_code: int | None = None
        timed_out = False
        launch_error = False
        stdout = b""
        stderr = b""
        try:
            result = runner(
                list(command),
                cwd=str(root / "phoenix"),
                capture_output=True,
                text=False,
                check=False,
                timeout=timeout_seconds,
                env=process_environment,
            )
            exit_code = int(result.returncode)
            stdout = _as_bytes(result.stdout)
            stderr = _as_bytes(result.stderr)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = _as_bytes(exc.stdout)
            stderr = _as_bytes(exc.stderr)
        except OSError as exc:
            launch_error = True
            stderr = str(exc).encode("utf-8", errors="replace")

        command_finished_at = _iso_utc(now())
        command_duration_ms = _duration_ms(command_timer_start, timer())
        evidence.append(
            CommandEvidence(
                name=specification.name,
                command=command,
                cwd=str(root / "phoenix"),
                started_at=command_started_at,
                finished_at=command_finished_at,
                duration_ms=command_duration_ms,
                exit_code=exit_code,
                timed_out=timed_out,
                launch_error=launch_error,
                stdout_bytes=len(stdout),
                stdout_sha256=hashlib.sha256(stdout).hexdigest(),
                stderr_bytes=len(stderr),
                stderr_sha256=hashlib.sha256(stderr).hexdigest(),
            )
        )
        if exit_code != 0 or timed_out or launch_error:
            report = _build_report(
                source=source,
                backend=backend,
                environment=environment_receipt,
                commands=evidence,
                commands_planned=len(commands),
                suite_started_at=suite_started_at,
                suite_finished_at=_iso_utc(now()),
                duration_ms=_duration_ms(suite_timer_start, timer()),
                runner_reported_success=False,
                suite_completed=False,
            )
            raise CurrentPhoenixExecutionError(
                f"Phoenix reference generator {specification.name!r} did not exit successfully",
                receipt=report,
            )

    return _build_report(
        source=source,
        backend=backend,
        environment=environment_receipt,
        commands=evidence,
        commands_planned=len(commands),
        suite_started_at=suite_started_at,
        suite_finished_at=_iso_utc(now()),
        duration_ms=_duration_ms(suite_timer_start, timer()),
        runner_reported_success=True,
        suite_completed=True,
    )


def write_receipt(report: Mapping[str, Any], output: str | Path) -> Path:
    if report.get("schema") != RECEIPT_SCHEMA or report.get("mode") != MODE:
        raise CurrentPhoenixContractError("refusing to write a receipt with an unknown schema or mode")
    truth = report.get("truth")
    if report.get("ranking_executed") is not False:
        raise CurrentPhoenixContractError(
            "current reference-generator receipts require ranking_executed=false"
        )
    if not isinstance(truth, Mapping) or truth.get("ranking_executed") is not False:
        raise CurrentPhoenixContractError("receipt truth requires ranking_executed=false")
    if report.get("live_feed_changed") is not False or truth.get("live_feed_changed") is not False:
        raise CurrentPhoenixContractError(
            "current reference-generator receipts require live_feed_changed=false"
        )
    commands = report.get("commands")
    if not isinstance(commands, list):
        raise CurrentPhoenixContractError("receipt commands must be a list")
    for command in commands:
        if not isinstance(command, Mapping):
            raise CurrentPhoenixContractError("receipt command evidence must be an object")
        if "stdout" in command or "stderr" in command:
            raise CurrentPhoenixContractError("receipt command evidence must contain hashes, not raw output")
        for stream in ("stdout", "stderr"):
            digest = command.get(f"{stream}_sha256")
            byte_count = command.get(f"{stream}_bytes")
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise CurrentPhoenixContractError(f"receipt command requires a valid {stream}_sha256")
            if not isinstance(byte_count, int) or byte_count < 0:
                raise CurrentPhoenixContractError(f"receipt command requires non-negative {stream}_bytes")

    destination = Path(output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(dict(report), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def _tracked_source_paths(root: Path, runner: Runner) -> tuple[str, ...]:
    result = runner(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "-z",
            "--",
            *REQUIRED_MANIFEST_FILES,
            "phoenix/reference",
        ],
        capture_output=True,
        text=False,
        check=False,
    )
    if int(result.returncode) != 0:
        raise CurrentPhoenixCheckoutError("git ls-files failed while identifying Phoenix sources")
    try:
        values = [
            _as_bytes(value).decode("utf-8")
            for value in _as_bytes(result.stdout).split(b"\0")
            if value
        ]
    except UnicodeDecodeError as exc:
        raise CurrentPhoenixCheckoutError("tracked Phoenix source paths are not valid UTF-8") from exc
    if not values or len(values) != len(set(values)):
        raise CurrentPhoenixCheckoutError("tracked Phoenix source manifest is empty or contains duplicates")

    normalized: list[str] = []
    for value in values:
        path = value.replace("\\", "/")
        parts = path.split("/")
        allowed = path in REQUIRED_MANIFEST_FILES or path.startswith("phoenix/reference/")
        if not allowed or path.startswith("/") or ".." in parts or "." in parts:
            raise CurrentPhoenixCheckoutError(f"unsafe tracked Phoenix source path: {value!r}")
        normalized.append(path)
    return tuple(sorted(normalized))


def _source_file_evidence(root: Path, relative_path: str) -> SourceFileEvidence:
    path = root / relative_path
    resolved = path.resolve()
    if path.is_symlink() or not _is_within(resolved, root):
        raise CurrentPhoenixCheckoutError(
            f"Phoenix source must be a regular in-checkout file: {relative_path}"
        )
    digest = hashlib.sha256()
    byte_count = 0
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            byte_count += len(chunk)
            digest.update(chunk)
    return SourceFileEvidence(path=relative_path, bytes=byte_count, sha256=digest.hexdigest())


def _build_report(
    *,
    source: CurrentPhoenixCheckout,
    backend: str,
    environment: dict[str, Any],
    commands: Sequence[CommandEvidence],
    commands_planned: int,
    suite_started_at: str,
    suite_finished_at: str,
    duration_ms: float,
    runner_reported_success: bool,
    suite_completed: bool,
) -> dict[str, Any]:
    upstream_processes_executed = backend == "subprocess" and bool(commands)
    suite_passed = runner_reported_success and backend == "subprocess"
    return {
        "schema": RECEIPT_SCHEMA,
        "mode": MODE,
        "warning": (
            "This receipt covers current upstream reference generators only. It does not execute Phoenix "
            "training, retrieval, ranking, serving, production data, or a live X account."
        ),
        "execution_backend": backend,
        "suite_started_at": suite_started_at,
        "suite_finished_at": suite_finished_at,
        "duration_ms": duration_ms,
        "suite_passed": suite_passed,
        "runner_reported_success": runner_reported_success,
        "suite_completed": suite_completed,
        "commands_planned": commands_planned,
        "commands_executed": len(commands),
        "ranking_executed": False,
        "live_feed_changed": False,
        "source": asdict(source),
        "environment": environment,
        "commands": [asdict(item) for item in commands],
        "truth": {
            "commands_are_allowlisted_reference_generators": True,
            "current_upstream_sources_hashed": backend == "subprocess",
            "generator_outputs_compared_to_production": False,
            "git_provenance_inspected_with_subprocess": backend == "subprocess",
            "live_account_accessed": False,
            "live_feed_changed": False,
            "network_isolation_enforced": False,
            "production_data_absence_verified": False,
            "production_ranking_fidelity_established": False,
            "ranking_executed": False,
            "retrieval_executed": False,
            "training_executed": False,
            "upstream_processes_executed": upstream_processes_executed,
        },
    }


def _environment_provenance(
    environment: Mapping[str, str], python_executable: str | Path
) -> dict[str, Any]:
    return {
        "bridge_python_executable": sys.executable,
        "bridge_python_implementation": platform.python_implementation(),
        "bridge_python_version": platform.python_version(),
        "command_python_executable": str(python_executable),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "safe_process_environment": {
            key: environment[key] for key in SAFE_ENVIRONMENT_KEYS if key in environment
        },
    }


def _process_environment(environment: Mapping[str, str] | None) -> dict[str, str]:
    source = os.environ if environment is None else environment
    result: dict[str, str] = {}
    for key, value in source.items():
        if not isinstance(key, str) or not isinstance(value, str) or "\0" in key or "\0" in value:
            raise CurrentPhoenixContractError("process environment must contain NUL-free string pairs")
        result[key] = value
    result.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return result


def _git_text(root: Path, arguments: Sequence[str], runner: Runner) -> str:
    result = runner(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=False,
        check=False,
    )
    if int(result.returncode) != 0:
        raise CurrentPhoenixCheckoutError(f"git {' '.join(arguments)} failed")
    try:
        return _as_bytes(result.stdout).decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise CurrentPhoenixCheckoutError(f"git {' '.join(arguments)} returned invalid UTF-8") from exc


def _require_full_git_sha(value: str, label: str) -> None:
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise CurrentPhoenixCheckoutError(f"{label} must be a full 40-character git SHA")


def _is_official_remote(remote: str) -> bool:
    normalized = remote.strip().lower().replace("\\", "/").rstrip("/")
    if normalized.startswith("git@github.com:"):
        normalized = "https://github.com/" + normalized.removeprefix("git@github.com:")
    elif normalized.startswith("ssh://git@github.com/"):
        normalized = "https://github.com/" + normalized.removeprefix("ssh://git@github.com/")
    normalized = normalized.removesuffix(".git")
    return normalized == OFFICIAL_REPOSITORY.lower()


def _require_plain_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise CurrentPhoenixContractError(f"{label} must be a non-empty NUL-free string")
    return value


def _execution_backend(runner: Runner) -> str:
    return "subprocess" if runner is subprocess.run else "injected_runner"


def _as_bytes(value: object) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return str(value).encode("utf-8", errors="replace")


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _duration_ms(start: float, finish: float) -> float:
    return round(max(0.0, finish - start) * 1000.0, 3)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
