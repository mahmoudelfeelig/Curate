from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from feed_passport.domain.models import (
    ActionOutcome,
    ActionReceipt,
    ActionStatus,
    ActionType,
    ProposedAction,
    RollbackOutcome,
)
from feed_passport.infrastructure.live_certification import LiveCertificationVerifier
from feed_passport.ports.action_journal import (
    RemoteActionAttempt,
    RemoteActionState,
    validate_remote_action_transition,
)
from feed_passport.ports.live_platform import PreparedRemoteAction, RemoteOutcomeUnknown
from feed_passport.runtime.live_conformance import (
    ConformanceAction,
    LiveConformanceFailure,
    LiveConformanceGateError,
    LiveConformanceRequest,
    LiveConformanceRuntime,
    main,
    run_live_conformance,
    verify_clean_checkout,
)
from feed_passport.runtime.live_conformance_cli import main as cli_main


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
HMAC_KEY = b"live-conformance-test-hmac-key-0001"
REVISION = "a" * 40


class FakeActionJournal:
    def __init__(self) -> None:
        self.records: dict[str, RemoteActionAttempt] = {}
        self.transitions: list[tuple[str, RemoteActionState, RemoteActionState]] = []
        self.fail_transition_once: RemoteActionState | None = None

    def reserve_remote_action(
        self,
        *,
        attempt_id: str,
        owner_id: str,
        connection_id: str,
        platform: str,
        migration_id: str,
        action_id: str,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
        action_payload: dict[str, object],
        created_at: datetime,
    ) -> RemoteActionAttempt:
        record = RemoteActionAttempt(
            id=attempt_id,
            owner_id=owner_id,
            connection_id=connection_id,
            platform=platform,
            migration_id=migration_id,
            action_id=action_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            state=RemoteActionState.PENDING,
            action_payload=action_payload,
            created_at=created_at,
            updated_at=created_at,
        )
        self.records[record.id] = record
        return record

    def get_remote_action(
        self,
        attempt_id: str,
        *,
        owner_id: str | None = None,
    ) -> RemoteActionAttempt:
        record = self.records[attempt_id]
        if owner_id is not None and record.owner_id != owner_id:
            raise KeyError(attempt_id)
        return record

    def transition_remote_action(
        self,
        attempt_id: str,
        *,
        expected_state: RemoteActionState,
        new_state: RemoteActionState,
        updated_at: datetime,
        lease_owner: str | None = None,
        lease_expires_at: datetime | None = None,
        next_attempt_at: datetime | None = None,
        platform_reference: str | None = None,
        before_state: dict[str, object] | None = None,
        after_state: dict[str, object] | None = None,
        error_code: str | None = None,
    ) -> RemoteActionAttempt:
        current = self.records[attempt_id]
        if current.state is not expected_state:
            raise RuntimeError("stale fake journal transition")
        if self.fail_transition_once is new_state:
            self.fail_transition_once = None
            raise RuntimeError(f"simulated journal failure before {new_state.value}")
        validate_remote_action_transition(current.state, new_state)
        updated = replace(
            current,
            state=new_state,
            attempt_count=current.attempt_count
            + int(new_state in {RemoteActionState.DISPATCHING, RemoteActionState.ROLLBACK_PENDING}),
            platform_reference=platform_reference or current.platform_reference,
            before_state=before_state if before_state is not None else current.before_state,
            after_state=after_state if after_state is not None else current.after_state,
            error_code=error_code,
            lease_owner=lease_owner,
            lease_expires_at=lease_expires_at,
            next_attempt_at=next_attempt_at,
            updated_at=updated_at,
        )
        self.records[attempt_id] = updated
        self.transitions.append((attempt_id, expected_state, new_state))
        return updated

    def list_remote_actions(self, **_kwargs: object) -> tuple[RemoteActionAttempt, ...]:
        return tuple(self.records.values())

    def list_recoverable_remote_actions(
        self,
        _now: datetime,
        *,
        limit: int = 100,
    ) -> tuple[RemoteActionAttempt, ...]:
        return tuple(self.records.values())[:limit]


class FakeLiveAdapter:
    platform = "x"

    def __init__(
        self,
        *,
        unknown_apply: bool = False,
        unknown_apply_action: ActionType | None = None,
        unknown_reconcile_action: ActionType | None = None,
        invalid_apply_action: ActionType | None = None,
        rollback_failure: bool = False,
    ) -> None:
        self.unknown_apply = unknown_apply
        self.unknown_apply_action = unknown_apply_action
        self.unknown_reconcile_action = unknown_reconcile_action
        self.invalid_apply_action = invalid_apply_action
        self.rollback_failure = rollback_failure
        self.calls: list[tuple[str, str]] = []
        self.rollback_order: list[str] = []
        self.access_token = "dummy-token-that-must-never-appear"

    def capabilities(self, _account_id: str) -> object:
        raise AssertionError("the conformance runner must use only the explicit action lifecycle")

    def prepare_remote_action(
        self,
        account_id: str,
        action: ProposedAction,
        *,
        now: datetime,
    ) -> PreparedRemoteAction:
        self.calls.append(("prepare", action.action_type.value))
        return PreparedRemoteAction(
            platform=self.platform,
            connection_id=account_id,
            action=action,
            before_state={"enabled": False, "kind": action.action_type.value},
            desired_state={"enabled": True, "kind": action.action_type.value},
            prepared_at=now,
        )

    def apply_prepared_action(
        self,
        prepared: PreparedRemoteAction,
        *,
        now: datetime,
    ) -> ActionOutcome:
        self.calls.append(("apply", prepared.action.action_type.value))
        if self.unknown_apply or prepared.action.action_type is self.unknown_apply_action:
            raise RemoteOutcomeUnknown(
                platform=self.platform,
                code="transport_interrupted",
                detail="The mutation result is unknown.",
                outcome_unknown=True,
            )
        outcome = self._executed(prepared, now)
        if prepared.action.action_type is self.invalid_apply_action:
            return replace(outcome, before_state={"untrusted": True})
        return outcome

    def reconcile_remote_action(
        self,
        prepared: PreparedRemoteAction,
        *,
        now: datetime,
    ) -> ActionOutcome:
        self.calls.append(("reconcile", prepared.action.action_type.value))
        if prepared.action.action_type is self.unknown_reconcile_action:
            raise RemoteOutcomeUnknown(
                platform=self.platform,
                code="reconcile_interrupted",
                detail="The verification result is unknown.",
                outcome_unknown=True,
            )
        return self._executed(prepared, now)

    def rollback(
        self,
        account_id: str,
        receipt: ActionReceipt,
        *,
        now: datetime,
    ) -> RollbackOutcome:
        action = receipt.outcomes[0].action
        self.calls.append(("rollback", action.action_type.value))
        self.rollback_order.append(action.action_type.value)
        if self.rollback_failure:
            return RollbackOutcome(
                receipt_id=receipt.id,
                destination_id=account_id,
                restored_actions=(),
                failed_actions=(action.id,),
                completed_at=now,
            )
        return RollbackOutcome(
            receipt_id=receipt.id,
            destination_id=account_id,
            restored_actions=(action.id,),
            failed_actions=(),
            completed_at=now,
        )

    @staticmethod
    def _executed(prepared: PreparedRemoteAction, now: datetime) -> ActionOutcome:
        return ActionOutcome(
            action=prepared.action,
            status=ActionStatus.EXECUTED,
            before_state=prepared.before_state,
            after_state=prepared.desired_state,
            executed_at=now,
            platform_reference=f"fake/{prepared.action.id}",
        )


class LiveConformanceRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name) / "x-certification.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def request(
        self,
        *,
        acknowledged: bool = True,
        actions: tuple[ConformanceAction, ...] | None = None,
    ) -> LiveConformanceRequest:
        return LiveConformanceRequest(
            acknowledge_authorized_dummy_account_mutations=acknowledged,
            owner_id="owner-dummy",
            connection_id="connection-dummy-x",
            platform="x",
            actions=actions
            or (ConformanceAction(ActionType.FOLLOW_CREATOR, "target-one"),),
            code_revision=REVISION,
            output_path=self.output,
            hmac_key=HMAC_KEY,
        )

    def git_checkout(self) -> tuple[Path, str]:
        root = Path(self.temp.name) / "checkout"
        root.mkdir()

        def git(*arguments: str) -> str:
            result = subprocess.run(
                ["git", "-C", str(root), *arguments],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip()

        git("init")
        (root / "README.md").write_text("clean conformance fixture\n", encoding="utf-8")
        git("add", "README.md")
        git(
            "-c",
            "user.name=Feed Passport Test",
            "-c",
            "user.email=feed-passport@example.invalid",
            "commit",
            "-m",
            "test fixture",
        )
        return root, git("rev-parse", "HEAD")

    def test_cli_gate_refuses_before_runtime_factory_or_network_boundary(self) -> None:
        encoded_key = base64.urlsafe_b64encode(HMAC_KEY).decode("ascii")
        factory_calls: list[LiveConformanceRequest] = []

        def factory(request: LiveConformanceRequest) -> LiveConformanceRuntime:
            factory_calls.append(request)
            return LiveConformanceRuntime(FakeLiveAdapter(), FakeActionJournal())

        args = [
            "--owner-id",
            "owner-dummy",
            "--connection-id",
            "connection-dummy-x",
            "--platform",
            "x",
            "--action",
            "follow_creator=target-one",
            "--git-revision",
            REVISION,
            "--output",
            str(self.output),
            "--hmac-key-env",
            "FEED_PASSPORT_TEST_LIVE_HMAC",
        ]
        with patch.dict(os.environ, {"FEED_PASSPORT_TEST_LIVE_HMAC": encoded_key}, clear=False):
            with self.assertRaises(SystemExit) as raised:
                main(args, runtime_factory=factory)
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(factory_calls, [])
        self.assertFalse(self.output.exists())

    def test_cli_wrapper_uses_the_canonical_conformance_module(self) -> None:
        self.assertIs(cli_main, main)

    def test_checkout_verifier_rejects_a_revision_other_than_head(self) -> None:
        root, revision = self.git_checkout()
        self.assertNotEqual(revision, REVISION)
        with self.assertRaisesRegex(LiveConformanceGateError, "does not match"):
            verify_clean_checkout(self.request(), source_root=root)

    def test_checkout_verifier_rejects_untracked_or_modified_source(self) -> None:
        root, revision = self.git_checkout()
        clean_request = replace(self.request(), code_revision=revision)
        verify_clean_checkout(clean_request, source_root=root)
        (root / "untracked.py").write_text("raise SystemExit\n", encoding="utf-8")
        with self.assertRaisesRegex(LiveConformanceGateError, "clean checkout"):
            verify_clean_checkout(clean_request, source_root=root)

    def test_pass_emits_verifiable_receipt_after_reverse_rollback(self) -> None:
        actions = (
            ConformanceAction(ActionType.FOLLOW_CREATOR, "target-one"),
            ConformanceAction(ActionType.MUTE_CREATOR, "target-two"),
        )
        adapter = FakeLiveAdapter()
        journal = FakeActionJournal()
        certification = run_live_conformance(
            self.request(actions=actions),
            LiveConformanceRuntime(adapter, journal),
            clock=lambda: NOW,
            id_factory=lambda: "proof-one",
            checkout_verifier=lambda _request: None,
        )

        self.assertTrue(self.output.is_file())
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8")), certification)
        validated = LiveCertificationVerifier(
            hmac_key=HMAC_KEY,
            expected_revision=REVISION,
            now=NOW,
        ).verify(certification)
        self.assertEqual(
            validated.execute,
            frozenset({ActionType.FOLLOW_CREATOR, ActionType.MUTE_CREATOR}),
        )
        self.assertEqual(adapter.rollback_order, ["mute_creator", "follow_creator"])
        self.assertEqual(
            [record.state for record in journal.records.values()],
            [RemoteActionState.ROLLED_BACK, RemoteActionState.ROLLED_BACK],
        )

    def test_unknown_apply_fails_closed_without_receipt(self) -> None:
        adapter = FakeLiveAdapter(unknown_apply=True)
        journal = FakeActionJournal()
        with self.assertRaises(LiveConformanceFailure):
            run_live_conformance(
                self.request(),
                LiveConformanceRuntime(adapter, journal),
                clock=lambda: NOW,
                id_factory=lambda: "proof-unknown",
                checkout_verifier=lambda _request: None,
            )

        self.assertFalse(self.output.exists())
        self.assertEqual(
            tuple(record.state for record in journal.records.values()),
            (RemoteActionState.UNKNOWN,),
        )
        self.assertNotIn(("reconcile", "follow_creator"), adapter.calls)

    def test_later_unknown_apply_rolls_back_every_earlier_confirmed_action(self) -> None:
        actions = (
            ConformanceAction(ActionType.FOLLOW_CREATOR, "target-one"),
            ConformanceAction(ActionType.MUTE_CREATOR, "target-two"),
        )
        adapter = FakeLiveAdapter(unknown_apply_action=ActionType.MUTE_CREATOR)
        journal = FakeActionJournal()

        with self.assertRaises(LiveConformanceFailure):
            run_live_conformance(
                self.request(actions=actions),
                LiveConformanceRuntime(adapter, journal),
                clock=lambda: NOW,
                id_factory=lambda: "proof-partial-failure",
                checkout_verifier=lambda _request: None,
            )

        self.assertFalse(self.output.exists())
        self.assertEqual(adapter.rollback_order, ["follow_creator"])
        self.assertEqual(
            tuple(record.state for record in journal.records.values()),
            (RemoteActionState.ROLLED_BACK, RemoteActionState.UNKNOWN),
        )

    def test_later_reconcile_failure_rolls_back_current_then_every_prior_action(self) -> None:
        actions = (
            ConformanceAction(ActionType.FOLLOW_CREATOR, "target-one"),
            ConformanceAction(ActionType.MUTE_CREATOR, "target-two"),
        )
        adapter = FakeLiveAdapter(unknown_reconcile_action=ActionType.MUTE_CREATOR)
        journal = FakeActionJournal()

        with self.assertRaises(LiveConformanceFailure):
            run_live_conformance(
                self.request(actions=actions),
                LiveConformanceRuntime(adapter, journal),
                clock=lambda: NOW,
                id_factory=lambda: "proof-reconcile-failure",
                checkout_verifier=lambda _request: None,
            )

        self.assertFalse(self.output.exists())
        self.assertEqual(adapter.rollback_order, ["mute_creator", "follow_creator"])
        self.assertEqual(
            tuple(record.state for record in journal.records.values()),
            (RemoteActionState.ROLLED_BACK, RemoteActionState.ROLLED_BACK),
        )

    def test_invalid_known_apply_uses_normalized_current_cleanup_then_prior_cleanup(self) -> None:
        actions = (
            ConformanceAction(ActionType.FOLLOW_CREATOR, "target-one"),
            ConformanceAction(ActionType.MUTE_CREATOR, "target-two"),
        )
        adapter = FakeLiveAdapter(invalid_apply_action=ActionType.MUTE_CREATOR)
        journal = FakeActionJournal()

        with self.assertRaises(LiveConformanceFailure):
            run_live_conformance(
                self.request(actions=actions),
                LiveConformanceRuntime(adapter, journal),
                clock=lambda: NOW,
                id_factory=lambda: "proof-invalid-apply",
                checkout_verifier=lambda _request: None,
            )

        self.assertFalse(self.output.exists())
        self.assertEqual(adapter.rollback_order, ["mute_creator", "follow_creator"])
        self.assertEqual(
            tuple(record.state for record in journal.records.values()),
            (RemoteActionState.ROLLED_BACK, RemoteActionState.ROLLED_BACK),
        )

    def test_success_journal_failure_still_rolls_back_known_applied_action(self) -> None:
        adapter = FakeLiveAdapter()
        journal = FakeActionJournal()
        journal.fail_transition_once = RemoteActionState.SUCCEEDED

        with self.assertRaises(LiveConformanceFailure):
            run_live_conformance(
                self.request(),
                LiveConformanceRuntime(adapter, journal),
                clock=lambda: NOW,
                id_factory=lambda: "proof-journal-finalize-failure",
                checkout_verifier=lambda _request: None,
            )

        self.assertFalse(self.output.exists())
        self.assertEqual(adapter.rollback_order, ["follow_creator"])
        self.assertEqual(
            tuple(record.state for record in journal.records.values()),
            (RemoteActionState.ROLLED_BACK,),
        )

    def test_unknown_journal_failure_still_rolls_back_known_applied_action(self) -> None:
        adapter = FakeLiveAdapter(unknown_reconcile_action=ActionType.FOLLOW_CREATOR)
        journal = FakeActionJournal()
        journal.fail_transition_once = RemoteActionState.UNKNOWN

        with self.assertRaises(LiveConformanceFailure):
            run_live_conformance(
                self.request(),
                LiveConformanceRuntime(adapter, journal),
                clock=lambda: NOW,
                id_factory=lambda: "proof-journal-unknown-failure",
                checkout_verifier=lambda _request: None,
            )

        self.assertFalse(self.output.exists())
        self.assertEqual(adapter.rollback_order, ["follow_creator"])
        self.assertEqual(
            tuple(record.state for record in journal.records.values()),
            (RemoteActionState.ROLLED_BACK,),
        )

    def test_unverified_rollback_fails_closed_without_receipt(self) -> None:
        adapter = FakeLiveAdapter(rollback_failure=True)
        journal = FakeActionJournal()
        with self.assertRaises(LiveConformanceFailure):
            run_live_conformance(
                self.request(),
                LiveConformanceRuntime(adapter, journal),
                clock=lambda: NOW,
                id_factory=lambda: "proof-rollback-failure",
                checkout_verifier=lambda _request: None,
            )

        self.assertFalse(self.output.exists())
        self.assertEqual(
            tuple(record.state for record in journal.records.values()),
            (RemoteActionState.ROLLBACK_UNKNOWN,),
        )

    def test_signed_output_contains_no_owner_connection_target_or_secret_material(self) -> None:
        adapter = FakeLiveAdapter()
        run_live_conformance(
            self.request(),
            LiveConformanceRuntime(adapter, FakeActionJournal()),
            clock=lambda: NOW,
            id_factory=lambda: "proof-secret-scan",
            checkout_verifier=lambda _request: None,
        )
        raw = self.output.read_text(encoding="utf-8")
        for forbidden in (
            HMAC_KEY.decode("ascii"),
            adapter.access_token,
            "owner-dummy",
            "connection-dummy-x",
            "target-one",
            "access_token",
            "refresh_token",
            "client_secret",
        ):
            self.assertNotIn(forbidden, raw)


if __name__ == "__main__":
    unittest.main()
