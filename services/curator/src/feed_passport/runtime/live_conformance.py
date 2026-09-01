from __future__ import annotations

import argparse
import base64
import importlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from feed_passport.domain.models import (
    ActionOutcome,
    ActionReceipt,
    ActionStatus,
    ActionType,
    ProposedAction,
)
from feed_passport.infrastructure.live_certification import (
    CERTIFICATION_FORMAT,
    LiveCertificationVerifier,
)
from feed_passport.ports.action_journal import (
    ActionJournal,
    RemoteActionAttempt,
    RemoteActionState,
)
from feed_passport.ports.live_platform import (
    LivePlatformAdapter,
    LivePlatformError,
    PreparedRemoteAction,
)


SUPPORTED_ACTIONS: dict[str, frozenset[ActionType]] = {
    "bluesky": frozenset(
        {
            ActionType.FOLLOW_CREATOR,
            ActionType.UNFOLLOW_CREATOR,
            ActionType.MUTE_CREATOR,
            ActionType.UNMUTE_CREATOR,
            ActionType.MUTE_KEYWORD,
            ActionType.UNMUTE_KEYWORD,
        }
    ),
    "reddit": frozenset(
        {ActionType.SUBSCRIBE_CREATOR, ActionType.UNSUBSCRIBE_CREATOR}
    ),
    "x": frozenset(
        {
            ActionType.FOLLOW_CREATOR,
            ActionType.UNFOLLOW_CREATOR,
            ActionType.MUTE_CREATOR,
            ActionType.UNMUTE_CREATOR,
        }
    ),
    "youtube": frozenset(
        {ActionType.SUBSCRIBE_CREATOR, ActionType.UNSUBSCRIBE_CREATOR}
    ),
}

_GIT_REVISION = re.compile(r"^[0-9a-f]{40}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_LEASE_DURATION = timedelta(minutes=5)
_CERTIFICATION_LIFETIME = timedelta(days=30)
_WORKER_ID = "live-conformance-runner"


class LiveConformanceGateError(RuntimeError):
    """The live mutation boundary was not explicitly and completely authorized."""


class LiveConformanceFailure(RuntimeError):
    """A conformance mutation could not be proven safe, complete, and reversible."""


class _AppliedConformanceFailure(LiveConformanceFailure):
    def __init__(
        self,
        message: str,
        *,
        outcome: ActionOutcome,
        attempt: RemoteActionAttempt,
    ) -> None:
        super().__init__(message)
        self.outcome = outcome
        self.attempt = attempt


@dataclass(frozen=True, slots=True)
class ConformanceAction:
    action_type: ActionType
    target: str

    def __post_init__(self) -> None:
        target = self.target.strip()
        if not target or len(target) > 512 or any(ord(character) < 32 for character in target):
            raise ValueError("conformance action target is invalid")
        object.__setattr__(self, "target", target)

    @classmethod
    def parse(cls, value: str) -> ConformanceAction:
        action_name, separator, target = value.partition("=")
        if not separator:
            raise ValueError("conformance actions use ACTION_TYPE=TARGET")
        try:
            action_type = ActionType(action_name.strip())
        except ValueError as exc:
            raise ValueError(f"unknown conformance action type: {action_name.strip()}") from exc
        return cls(action_type=action_type, target=target)


@dataclass(frozen=True, slots=True)
class LiveConformanceRequest:
    acknowledge_authorized_dummy_account_mutations: bool
    owner_id: str
    connection_id: str
    platform: str
    actions: tuple[ConformanceAction, ...]
    code_revision: str
    output_path: Path
    hmac_key: bytes
    provider_approval_ref: str = ""

    def __post_init__(self) -> None:
        owner_id = self.owner_id.strip()
        connection_id = self.connection_id.strip()
        platform = self.platform.strip().lower()
        approval = self.provider_approval_ref.strip()
        if not _SAFE_IDENTIFIER.fullmatch(owner_id):
            raise ValueError("owner ID is invalid")
        if not _SAFE_IDENTIFIER.fullmatch(connection_id):
            raise ValueError("connection ID is invalid")
        if platform not in SUPPORTED_ACTIONS:
            raise ValueError(f"unsupported live conformance platform: {platform}")
        actions = tuple(self.actions)
        if not actions:
            raise ValueError("an explicit non-empty conformance action subset is required")
        action_types = tuple(action.action_type for action in actions)
        if len(set(action_types)) != len(action_types):
            raise ValueError("each conformance action type must appear exactly once")
        unsupported = set(action_types) - SUPPORTED_ACTIONS[platform]
        if unsupported:
            names = ", ".join(sorted(action.value for action in unsupported))
            raise ValueError(f"actions are outside the {platform} candidate surface: {names}")
        revision = self.code_revision.strip()
        if not _GIT_REVISION.fullmatch(revision):
            raise ValueError("an exact lowercase 40-character Git revision is required")
        if platform == "reddit" and not approval:
            raise ValueError("Reddit conformance requires a provider approval reference")
        if platform != "reddit" and approval:
            raise ValueError("provider approval references are accepted only for Reddit")
        if len(approval) > 256 or any(ord(character) < 32 for character in approval):
            raise ValueError("provider approval reference is invalid")
        key = bytes(self.hmac_key)
        if len(key) < 32:
            raise ValueError("external HMAC key must contain at least 32 bytes")
        output_path = Path(self.output_path).expanduser().resolve()
        if output_path.suffix.lower() != ".json":
            raise ValueError("live certification output must be a JSON file")
        object.__setattr__(self, "owner_id", owner_id)
        object.__setattr__(self, "connection_id", connection_id)
        object.__setattr__(self, "platform", platform)
        object.__setattr__(self, "actions", actions)
        object.__setattr__(self, "code_revision", revision)
        object.__setattr__(self, "provider_approval_ref", approval)
        object.__setattr__(self, "hmac_key", key)
        object.__setattr__(self, "output_path", output_path)


@dataclass(frozen=True, slots=True)
class LiveConformanceRuntime:
    adapter: LivePlatformAdapter
    journal: ActionJournal
    close_callbacks: tuple[Callable[[], None], ...] = ()

    def close(self) -> None:
        failures: list[Exception] = []
        for callback in self.close_callbacks:
            try:
                callback()
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise LiveConformanceFailure("live conformance runtime cleanup failed") from failures[0]


class RuntimeFactory(Protocol):
    def __call__(self, request: LiveConformanceRequest) -> LiveConformanceRuntime: ...


Clock = Callable[[], datetime]
IdFactory = Callable[[], str]
CheckoutVerifier = Callable[[LiveConformanceRequest], None]


def run_live_conformance(
    request: LiveConformanceRequest,
    runtime: LiveConformanceRuntime,
    *,
    clock: Clock | None = None,
    id_factory: IdFactory | None = None,
    checkout_verifier: CheckoutVerifier | None = None,
) -> dict[str, object]:
    """Run a gated dummy-account proof and write a certificate only after rollback.

    No adapter or credential provider is constructed here.  The caller must inject
    the live adapter and durable action journal after resolving an owner-scoped
    connection.  This makes the module network-inert unless the explicit gate and
    a deliberate runtime factory are both supplied.
    """

    if not request.acknowledge_authorized_dummy_account_mutations:
        raise LiveConformanceGateError(
            "explicit acknowledgement of authorized dummy-account mutations is required"
        )
    if request.output_path.exists():
        raise LiveConformanceGateError("certification output already exists; refusing to overwrite it")
    if runtime.adapter.platform != request.platform:
        raise LiveConformanceGateError("injected adapter does not match the requested platform")
    (checkout_verifier or verify_clean_checkout)(request)

    now = clock or (lambda: datetime.now(timezone.utc))
    make_id = id_factory or (lambda: uuid4().hex)
    run_id = _safe_run_id(make_id())
    outcomes: list[ActionOutcome] = []
    attempts: list[RemoteActionAttempt] = []

    for index, specification in enumerate(request.actions, start=1):
        try:
            outcome, attempt = _apply_conformance_action(
                request,
                runtime,
                run_id=run_id,
                index=index,
                specification=specification,
                clock=now,
            )
        except Exception as primary_failure:
            cleanup_outcomes = list(outcomes)
            cleanup_attempts = list(attempts)
            if isinstance(primary_failure, _AppliedConformanceFailure):
                cleanup_outcomes.append(primary_failure.outcome)
                cleanup_attempts.append(primary_failure.attempt)
            try:
                _rollback_confirmed_actions(
                    request,
                    runtime,
                    run_id=run_id,
                    outcomes=cleanup_outcomes,
                    attempts=cleanup_attempts,
                    clock=now,
                )
            except LiveConformanceFailure as cleanup_failure:
                raise LiveConformanceFailure(
                    f"{primary_failure}; reverse cleanup of earlier confirmed actions was incomplete; "
                    "no certification was emitted"
                ) from cleanup_failure
            raise
        outcomes.append(outcome)
        attempts.append(attempt)

    _rollback_confirmed_actions(
        request,
        runtime,
        run_id=run_id,
        outcomes=outcomes,
        attempts=attempts,
        clock=now,
    )

    for attempt in attempts:
        terminal = runtime.journal.get_remote_action(attempt.id, owner_id=request.owner_id)
        _validate_owner_binding(request, terminal, terminal_action=None)
        if terminal.state is not RemoteActionState.ROLLED_BACK:
            raise LiveConformanceFailure("journal did not prove a terminal verified rollback")

    certified_at = _aware_now(now)
    action_names = sorted(action.action_type.value for action in request.actions)
    checks = [
        {"name": "explicit_dummy_account_authorization", "result": "passed"},
        {"name": "owner_bound_connection", "result": "passed"},
    ]
    checks.extend(
        {"name": f"prepare_apply_reconcile:{name}", "result": "passed"}
        for name in action_names
    )
    checks.extend(
        {"name": f"reverse_rollback:{name}", "result": "passed"}
        for name in reversed(action_names)
    )
    checks.append({"name": "durable_journal_terminal_state", "result": "passed"})
    unsigned = {
        "format": CERTIFICATION_FORMAT,
        "platform": request.platform,
        "environment": "authorized_live",
        "account_class": "dummy",
        "result": "passed",
        "certified_at": certified_at.isoformat(),
        "expires_at": (certified_at + _CERTIFICATION_LIFETIME).isoformat(),
        "code_revision": request.code_revision,
        "execute": action_names,
        "observe": [],
        "verify": ["post_write_reconciliation", "verified_reverse_rollback"],
        "rollback": action_names,
        "receipt_ref": f"live-conformance/{request.platform}/{run_id}",
        "provider_approval_ref": request.provider_approval_ref,
        "checks": checks,
    }
    signer = LiveCertificationVerifier(
        hmac_key=request.hmac_key,
        expected_revision=request.code_revision,
        now=certified_at,
    )
    certification = signer.sign(unsigned)
    _write_new_json(request.output_path, certification)
    return certification


def _apply_conformance_action(
    request: LiveConformanceRequest,
    runtime: LiveConformanceRuntime,
    *,
    run_id: str,
    index: int,
    specification: ConformanceAction,
    clock: Clock,
) -> tuple[ActionOutcome, RemoteActionAttempt]:
    timestamp = _aware_now(clock)
    action = ProposedAction(
        id=f"{run_id}-action-{index}",
        destination_id=request.connection_id,
        action_type=specification.action_type,
        target=specification.target,
        reason="authorized dummy-account live conformance",
        idempotency_key=f"{run_id}:{index}:{specification.action_type.value}",
        reversible=True,
        parameters={},
    )
    try:
        prepared = runtime.adapter.prepare_remote_action(
            request.connection_id,
            action,
            now=timestamp,
        )
    except Exception as exc:
        raise LiveConformanceFailure(
            f"prepare failed for {specification.action_type.value}; no certification was emitted"
        ) from exc
    _validate_prepared(request, prepared, action)
    attempt = runtime.journal.reserve_remote_action(
        attempt_id=f"{run_id}-attempt-{index}",
        owner_id=request.owner_id,
        connection_id=request.connection_id,
        platform=request.platform,
        migration_id=run_id,
        action_id=action.id,
        operation="live_conformance",
        idempotency_key=action.idempotency_key,
        request_fingerprint=prepared.idempotency_fingerprint,
        action_payload={
            "action_type": action.action_type.value,
            "target": action.target,
            "reversible": True,
        },
        created_at=timestamp,
    )
    _validate_owner_binding(request, attempt, action)
    if attempt.state is not RemoteActionState.PENDING:
        raise LiveConformanceFailure("conformance journal reservation was not fresh")
    attempt = runtime.journal.transition_remote_action(
        attempt.id,
        expected_state=RemoteActionState.PENDING,
        new_state=RemoteActionState.DISPATCHING,
        updated_at=timestamp,
        lease_owner=_WORKER_ID,
        lease_expires_at=timestamp + _LEASE_DURATION,
    )
    try:
        applied = runtime.adapter.apply_prepared_action(prepared, now=_aware_now(clock))
    except LivePlatformError as exc:
        _record_action_exception(runtime.journal, attempt, exc, clock)
        kind = "unknown" if exc.outcome_unknown else "failed"
        raise LiveConformanceFailure(
            f"apply result was {kind} for {action.action_type.value}; no certification was emitted"
        ) from exc
    except Exception as exc:
        _record_unknown_failure(runtime.journal, attempt, "unexpected_adapter_error", clock)
        raise LiveConformanceFailure(
            f"apply outcome is unknown for {action.action_type.value}; no certification was emitted"
        ) from exc
    if applied.status is not ActionStatus.EXECUTED:
        _record_known_failure(
            runtime.journal,
            attempt,
            f"apply_status_{applied.status.value}",
            clock,
        )
        raise LiveConformanceFailure(
            f"apply did not execute {action.action_type.value}; no certification was emitted"
        )
    try:
        _validate_outcome(action, prepared, applied, phase="apply")
    except LiveConformanceFailure as exc:
        cleanup_outcome = _normalized_cleanup_outcome(
            action,
            prepared,
            applied,
            clock=clock,
        )
        unknown = _best_effort_record_applied_unknown(
            runtime.journal,
            attempt,
            "apply_outcome_invalid",
            clock,
        )
        raise _AppliedConformanceFailure(
            str(exc),
            outcome=cleanup_outcome,
            attempt=unknown,
        ) from exc

    try:
        reconciled = runtime.adapter.reconcile_remote_action(prepared, now=_aware_now(clock))
    except LivePlatformError as exc:
        unknown = _best_effort_record_applied_unknown(
            runtime.journal,
            attempt,
            exc.code,
            clock,
        )
        raise _AppliedConformanceFailure(
            f"reconcile outcome is unknown for {action.action_type.value}; no certification was emitted",
            outcome=applied,
            attempt=unknown,
        ) from exc
    except Exception as exc:
        unknown = _best_effort_record_applied_unknown(
            runtime.journal,
            attempt,
            "unexpected_reconcile_error",
            clock,
        )
        raise _AppliedConformanceFailure(
            f"reconcile outcome is unknown for {action.action_type.value}; no certification was emitted",
            outcome=applied,
            attempt=unknown,
        ) from exc
    if reconciled.status is not ActionStatus.EXECUTED:
        unknown = _best_effort_record_applied_unknown(
            runtime.journal,
            attempt,
            f"reconcile_status_{reconciled.status.value}",
            clock,
        )
        raise _AppliedConformanceFailure(
            f"reconcile did not verify {action.action_type.value}; no certification was emitted",
            outcome=applied,
            attempt=unknown,
        )
    try:
        _validate_outcome(action, prepared, reconciled, phase="reconcile")
    except LiveConformanceFailure as exc:
        unknown = _best_effort_record_applied_unknown(
            runtime.journal,
            attempt,
            "reconcile_outcome_invalid",
            clock,
        )
        raise _AppliedConformanceFailure(
            str(exc),
            outcome=applied,
            attempt=unknown,
        ) from exc
    if not _state_matches(reconciled.after_state, prepared.desired_state):
        unknown = _best_effort_record_applied_unknown(
            runtime.journal,
            attempt,
            "reconcile_state_mismatch",
            clock,
        )
        raise _AppliedConformanceFailure(
            f"reconcile state mismatch for {action.action_type.value}; "
            "no certification was emitted",
            outcome=applied,
            attempt=unknown,
        )
    completed_at = _aware_now(clock)
    try:
        completed = runtime.journal.transition_remote_action(
            attempt.id,
            expected_state=RemoteActionState.DISPATCHING,
            new_state=RemoteActionState.SUCCEEDED,
            updated_at=completed_at,
            platform_reference=reconciled.platform_reference,
            before_state=reconciled.before_state,
            after_state=reconciled.after_state,
        )
    except Exception as exc:
        raise _AppliedConformanceFailure(
            f"journal could not finalize {action.action_type.value}; no certification was emitted",
            outcome=reconciled,
            attempt=attempt,
        ) from exc
    return reconciled, completed


def _rollback_confirmed_actions(
    request: LiveConformanceRequest,
    runtime: LiveConformanceRuntime,
    *,
    run_id: str,
    outcomes: Sequence[ActionOutcome],
    attempts: Sequence[RemoteActionAttempt],
    clock: Clock,
) -> None:
    failures: list[tuple[str, Exception]] = []
    pairs = tuple(zip(outcomes, attempts, strict=True))
    for reverse_index, (outcome, attempt) in enumerate(reversed(pairs), start=1):
        action_name = outcome.action.action_type.value
        rollback_started = _aware_now(clock)
        rollback_attempt = attempt
        if attempt.state is RemoteActionState.DISPATCHING:
            try:
                rollback_attempt = runtime.journal.transition_remote_action(
                    attempt.id,
                    expected_state=RemoteActionState.DISPATCHING,
                    new_state=RemoteActionState.SUCCEEDED,
                    updated_at=rollback_started,
                    platform_reference=outcome.platform_reference,
                    before_state=outcome.before_state,
                    after_state=outcome.after_state,
                    error_code="known_applied_cleanup",
                )
            except Exception as exc:
                failures.append((action_name, exc))
                continue
        elif attempt.state is RemoteActionState.UNKNOWN:
            try:
                reconciling = runtime.journal.transition_remote_action(
                    attempt.id,
                    expected_state=RemoteActionState.UNKNOWN,
                    new_state=RemoteActionState.RECONCILING,
                    updated_at=rollback_started,
                    lease_owner=_WORKER_ID,
                    lease_expires_at=rollback_started + _LEASE_DURATION,
                    error_code="validated_apply_cleanup",
                )
                rollback_attempt = runtime.journal.transition_remote_action(
                    reconciling.id,
                    expected_state=RemoteActionState.RECONCILING,
                    new_state=RemoteActionState.SUCCEEDED,
                    updated_at=_aware_now(clock),
                    platform_reference=outcome.platform_reference,
                    before_state=outcome.before_state,
                    after_state=outcome.after_state,
                    error_code="validated_apply_cleanup",
                )
            except Exception as exc:
                failures.append((action_name, exc))
                continue
        elif attempt.state is not RemoteActionState.SUCCEEDED:
            failures.append(
                (
                    action_name,
                    LiveConformanceFailure(
                        f"{action_name} is not eligible for verified reverse cleanup"
                    ),
                )
            )
            continue
        try:
            pending = runtime.journal.transition_remote_action(
                rollback_attempt.id,
                expected_state=RemoteActionState.SUCCEEDED,
                new_state=RemoteActionState.ROLLBACK_PENDING,
                updated_at=rollback_started,
                lease_owner=_WORKER_ID,
                lease_expires_at=rollback_started + _LEASE_DURATION,
            )
        except Exception as exc:
            failures.append((action_name, exc))
            continue
        receipt = ActionReceipt(
            id=f"{run_id}-rollback-{reverse_index}",
            passport_id="live-conformance",
            passport_version=1,
            destination_id=request.connection_id,
            outcomes=(outcome,),
            issued_at=rollback_started,
            trace_id=run_id,
            previous_checkpoint_id=None,
        )
        try:
            rollback = runtime.adapter.rollback(
                request.connection_id,
                receipt,
                now=_aware_now(clock),
            )
        except Exception as exc:
            try:
                runtime.journal.transition_remote_action(
                    pending.id,
                    expected_state=RemoteActionState.ROLLBACK_PENDING,
                    new_state=RemoteActionState.ROLLBACK_UNKNOWN,
                    updated_at=_aware_now(clock),
                    error_code="rollback_result_unknown",
                )
            except Exception as journal_exc:
                failures.append((action_name, journal_exc))
            failures.append((action_name, exc))
            continue
        restored = tuple(rollback.restored_actions)
        if (
            rollback.receipt_id != receipt.id
            or rollback.destination_id != request.connection_id
            or restored != (outcome.action.id,)
            or rollback.failed_actions
            or rollback.caveats
        ):
            error = LiveConformanceFailure(f"rollback was not verified for {action_name}")
            try:
                runtime.journal.transition_remote_action(
                    pending.id,
                    expected_state=RemoteActionState.ROLLBACK_PENDING,
                    new_state=RemoteActionState.ROLLBACK_UNKNOWN,
                    updated_at=_aware_now(clock),
                    error_code="rollback_not_verified",
                )
            except Exception as journal_exc:
                failures.append((action_name, journal_exc))
            failures.append((action_name, error))
            continue
        try:
            runtime.journal.transition_remote_action(
                pending.id,
                expected_state=RemoteActionState.ROLLBACK_PENDING,
                new_state=RemoteActionState.ROLLED_BACK,
                updated_at=_aware_now(clock),
                after_state=outcome.before_state,
            )
        except Exception as exc:
            failures.append((action_name, exc))
    if failures:
        failed_actions = ", ".join(sorted({name for name, _error in failures}))
        raise LiveConformanceFailure(
            f"verified reverse rollback was incomplete for {failed_actions}; "
            "no certification was emitted"
        ) from failures[0][1]


def _validate_prepared(
    request: LiveConformanceRequest,
    prepared: PreparedRemoteAction,
    action: ProposedAction,
) -> None:
    if prepared.platform != request.platform:
        raise LiveConformanceFailure("prepared action platform does not match the gate")
    if prepared.connection_id != request.connection_id:
        raise LiveConformanceFailure("prepared action connection does not match the owner-bound gate")
    if prepared.action != action or prepared.action.destination_id != request.connection_id:
        raise LiveConformanceFailure("adapter changed the gated conformance action")


def _validate_owner_binding(
    request: LiveConformanceRequest,
    attempt: RemoteActionAttempt,
    terminal_action: ProposedAction | None,
) -> None:
    if (
        attempt.owner_id != request.owner_id
        or attempt.connection_id != request.connection_id
        or attempt.platform != request.platform
    ):
        raise LiveConformanceGateError("action journal rejected the owner-bound connection gate")
    if terminal_action is not None and attempt.action_id != terminal_action.id:
        raise LiveConformanceFailure("action journal returned a different action binding")


def _validate_outcome(
    action: ProposedAction,
    prepared: PreparedRemoteAction,
    outcome: ActionOutcome,
    *,
    phase: str,
) -> None:
    if outcome.action != action:
        raise LiveConformanceFailure(f"{phase} returned an outcome for a different action")
    if dict(outcome.before_state) != dict(prepared.before_state):
        raise LiveConformanceFailure(f"{phase} returned an untrusted before-state")


def _record_action_exception(
    journal: ActionJournal,
    attempt: RemoteActionAttempt,
    error: LivePlatformError,
    clock: Clock,
) -> None:
    state = RemoteActionState.UNKNOWN if error.outcome_unknown else RemoteActionState.FAILED_FINAL
    journal.transition_remote_action(
        attempt.id,
        expected_state=RemoteActionState.DISPATCHING,
        new_state=state,
        updated_at=_aware_now(clock),
        error_code=error.code,
    )


def _record_known_failure(
    journal: ActionJournal,
    attempt: RemoteActionAttempt,
    error_code: str,
    clock: Clock,
) -> None:
    journal.transition_remote_action(
        attempt.id,
        expected_state=RemoteActionState.DISPATCHING,
        new_state=RemoteActionState.FAILED_FINAL,
        updated_at=_aware_now(clock),
        error_code=error_code,
    )


def _record_unknown_failure(
    journal: ActionJournal,
    attempt: RemoteActionAttempt,
    error_code: str,
    clock: Clock,
) -> RemoteActionAttempt:
    return journal.transition_remote_action(
        attempt.id,
        expected_state=RemoteActionState.DISPATCHING,
        new_state=RemoteActionState.UNKNOWN,
        updated_at=_aware_now(clock),
        error_code=error_code,
    )


def _best_effort_record_applied_unknown(
    journal: ActionJournal,
    attempt: RemoteActionAttempt,
    error_code: str,
    clock: Clock,
) -> RemoteActionAttempt:
    """Keep cleanup possible when the journal write itself is interrupted.

    The caller already has positive evidence that the provider mutation ran. If
    the UNKNOWN transition fails, returning the still-DISPATCHING reservation
    lets the reverse-cleanup path promote that exact reservation to SUCCEEDED
    before attempting its verified rollback. No certification is emitted.
    """

    try:
        return _record_unknown_failure(journal, attempt, error_code, clock)
    except Exception:
        return attempt


def _normalized_cleanup_outcome(
    action: ProposedAction,
    prepared: PreparedRemoteAction,
    applied: ActionOutcome,
    *,
    clock: Clock,
) -> ActionOutcome:
    """Build a trusted rollback input after an adapter returns malformed evidence."""

    return ActionOutcome(
        action=action,
        status=ActionStatus.EXECUTED,
        before_state=prepared.before_state,
        after_state=prepared.desired_state,
        executed_at=_aware_now(clock),
        platform_reference=applied.platform_reference,
        error_code="apply_outcome_invalid",
    )


def _state_matches(observed: object, expected: object) -> bool:
    if not isinstance(observed, dict) and not hasattr(observed, "get"):
        return False
    if not isinstance(expected, dict) and not hasattr(expected, "items"):
        return False
    return all(observed.get(key) == value for key, value in expected.items())  # type: ignore[union-attr]


def verify_clean_checkout(
    request: LiveConformanceRequest,
    *,
    source_root: Path | None = None,
) -> None:
    root = (source_root or Path(__file__).resolve().parents[5]).resolve()
    if not root.is_dir():
        raise LiveConformanceGateError("live conformance source checkout is unavailable")
    environment = dict(os.environ)
    environment["GIT_OPTIONAL_LOCKS"] = "0"

    def git_output(*arguments: str) -> str:
        try:
            result = subprocess.run(
                [
                    "git",
                    "-c",
                    f"safe.directory={root}",
                    "-C",
                    str(root),
                    *arguments,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise LiveConformanceGateError(
                "the clean Git checkout could not be verified"
            ) from exc
        if result.returncode != 0:
            raise LiveConformanceGateError("the clean Git checkout could not be verified")
        return result.stdout.strip()

    reported_root = Path(git_output("rev-parse", "--show-toplevel")).resolve()
    if os.path.normcase(str(reported_root)) != os.path.normcase(str(root)):
        raise LiveConformanceGateError("live conformance source root is not the Git checkout root")
    revision = git_output("rev-parse", "HEAD")
    if not _GIT_REVISION.fullmatch(revision):
        raise LiveConformanceGateError("the checked-out Git revision is invalid")
    if revision != request.code_revision:
        raise LiveConformanceGateError(
            "the requested Git revision does not match the checked-out HEAD"
        )
    dirty = git_output(
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    if dirty:
        raise LiveConformanceGateError(
            "live conformance requires a clean checkout with no tracked or untracked changes"
        )


def _safe_run_id(value: str) -> str:
    normalized = str(value).strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", normalized):
        raise ValueError("conformance run ID factory returned an unsafe value")
    return f"live-{normalized}"


def _aware_now(clock: Clock) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("live conformance clock must return a timezone-aware timestamp")
    return value.astimezone(timezone.utc)


def _write_new_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise LiveConformanceGateError(
            "certification output appeared during the run; refusing to overwrite it"
        ) from exc


def _read_external_hmac_key(environment_name: str) -> bytes:
    name = environment_name.strip()
    if not _ENVIRONMENT_NAME.fullmatch(name):
        raise LiveConformanceGateError("HMAC key environment variable name is invalid")
    encoded = os.environ.get(name)
    if encoded is None:
        raise LiveConformanceGateError(f"external HMAC key environment variable is not set: {name}")
    try:
        padded = encoded.strip() + "=" * (-len(encoded.strip()) % 4)
        key = base64.b64decode(padded, altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise LiveConformanceGateError(
            "external HMAC key must be URL-safe base64 encoded"
        ) from exc
    if len(key) < 32:
        raise LiveConformanceGateError("external HMAC key must decode to at least 32 bytes")
    return key


def _load_runtime_factory(specification: str) -> RuntimeFactory:
    module_name, separator, attribute_name = specification.partition(":")
    if not separator or not module_name.strip() or not attribute_name.strip():
        raise LiveConformanceGateError("runtime factory must use MODULE:CALLABLE")
    module = importlib.import_module(module_name.strip())
    factory = getattr(module, attribute_name.strip(), None)
    if not callable(factory):
        raise LiveConformanceGateError("runtime factory target is not callable")
    return factory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Gated authorized-dummy-account live conformance. This command can mutate a real "
            "platform account and is inert without the acknowledgement and an injected runtime factory."
        )
    )
    parser.add_argument(
        "--acknowledge-authorized-dummy-account-mutations",
        action="store_true",
        help="Acknowledge that the supplied connection is an authorized dummy account.",
    )
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--connection-id", required=True)
    parser.add_argument("--platform", required=True, choices=sorted(SUPPORTED_ACTIONS))
    parser.add_argument(
        "--action",
        action="append",
        required=True,
        metavar="ACTION_TYPE=TARGET",
        help="Exact reversible mutation to certify; repeat for each distinct action type.",
    )
    parser.add_argument("--git-revision", required=True)
    parser.add_argument("--provider-approval-ref", default="")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--hmac-key-env",
        required=True,
        help="Name of an environment variable containing a URL-safe-base64 external HMAC key.",
    )
    parser.add_argument(
        "--runtime-factory",
        help="Explicit MODULE:CALLABLE factory returning LiveConformanceRuntime.",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runtime_factory: RuntimeFactory | None = None,
) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        runtime: LiveConformanceRuntime | None = None
        try:
            hmac_key = _read_external_hmac_key(arguments.hmac_key_env)
            actions = tuple(ConformanceAction.parse(value) for value in arguments.action)
            request = LiveConformanceRequest(
                acknowledge_authorized_dummy_account_mutations=(
                    arguments.acknowledge_authorized_dummy_account_mutations
                ),
                owner_id=arguments.owner_id,
                connection_id=arguments.connection_id,
                platform=arguments.platform,
                actions=actions,
                code_revision=arguments.git_revision,
                provider_approval_ref=arguments.provider_approval_ref,
                output_path=arguments.output,
                hmac_key=hmac_key,
            )
            if not request.acknowledge_authorized_dummy_account_mutations:
                raise LiveConformanceGateError(
                    "explicit acknowledgement of authorized dummy-account mutations is required"
                )
            if request.output_path.exists():
                raise LiveConformanceGateError(
                    "certification output already exists; refusing to initialize a live runtime"
                )
            verify_clean_checkout(request)
            factory = runtime_factory
            if factory is None:
                if not arguments.runtime_factory:
                    raise LiveConformanceGateError(
                        "an explicit runtime factory is required; no platform transport runs by default"
                    )
                factory = _load_runtime_factory(arguments.runtime_factory)
            runtime = factory(request)
            certification = run_live_conformance(request, runtime)
        finally:
            if runtime is not None:
                runtime.close()
    except (LiveConformanceGateError, LiveConformanceFailure, ValueError) as exc:
        parser.exit(2, f"live conformance refused: {exc}\n")
    print(
        json.dumps(
            {
                "result": "passed",
                "platform": certification["platform"],
                "output": str(request.output_path),
                "evidence_sha256": certification["evidence_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
