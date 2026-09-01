from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Event, Thread

import pytest

from feed_passport.adapters.lab.adapter import LabAccountState, LabAdapter
from feed_passport.application import CuratorApplication, InvalidStateError
from feed_passport.application.model_io import action_from_dict
from feed_passport.domain import ActionStatus
from feed_passport.infrastructure import AesGcmKeyring, EncryptedConnectionRegistry, SQLiteStore
from feed_passport.ports.action_journal import RemoteActionState
from feed_passport.ports.live_platform import (
    LiveAuthenticationError,
    PreparedRemoteAction,
    RemoteOutcomeUnknown,
)


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


class RecoverableLiveLabAdapter(LabAdapter):
    platform = "live-test"

    def __init__(self, *, interrupt_first_write: bool = True) -> None:
        super().__init__(
            accounts=(
                LabAccountState(
                    id="connection-live-test",
                    topic_affinity={"ragebait": 0.8, "research": 0.2},
                    following={"rage-farm"},
                ),
            )
        )
        self.interrupt_first_write = interrupt_first_write
        self.interrupt_before_reservation = False
        self.reconciliation_error: Exception | None = None
        self.rollback_error: Exception | None = None
        self.reconcile_calls = 0
        self.apply_counts: dict[str, int] = {}
        self.rollback_calls = 0
        self.rollback_action_batches: list[tuple[str, ...]] = []

    def prepare_remote_action(self, account_id, action, *, now):
        if self.interrupt_before_reservation:
            self.interrupt_before_reservation = False
            raise RuntimeError("simulated crash before durable reservation")
        preview = self.clone()
        expected = preview.execute(account_id, action, now=now)
        return PreparedRemoteAction(
            platform=self.platform,
            connection_id=account_id,
            action=action,
            before_state=expected.before_state,
            desired_state=expected.after_state,
            prepared_at=now,
        )

    def apply_prepared_action(self, prepared, *, now):
        action_id = prepared.action.id
        self.apply_counts[action_id] = self.apply_counts.get(action_id, 0) + 1
        outcome = super().execute(prepared.connection_id, prepared.action, now=now)
        if self.interrupt_first_write:
            self.interrupt_first_write = False
            raise RemoteOutcomeUnknown(
                platform=self.platform,
                code="simulated_response_lost",
                detail="The local transport intentionally lost the first response.",
                outcome_unknown=True,
            )
        return outcome

    def reconcile_remote_action(self, prepared, *, now):
        self.reconcile_calls += 1
        if self.reconciliation_error is not None:
            raise self.reconciliation_error
        return super().execute(prepared.connection_id, prepared.action, now=now)

    def rollback(self, account_id, receipt, *, now):
        self.rollback_calls += 1
        self.rollback_action_batches.append(
            tuple(outcome.action.id for outcome in receipt.outcomes)
        )
        if self.rollback_error is not None:
            raise self.rollback_error
        return super().rollback(account_id, receipt, now=now)


class CurrentStatePreparingLiveLabAdapter(RecoverableLiveLabAdapter):
    """Prepare from current state, like a certified remote-state reader."""

    def __init__(self) -> None:
        super().__init__()
        self.prepare_counts: dict[str, int] = {}

    def prepare_remote_action(self, account_id, action, *, now):
        self.prepare_counts[action.id] = self.prepare_counts.get(action.id, 0) + 1
        preview = LabAdapter(accounts=(self._accounts[account_id],))
        expected = preview.execute(account_id, action, now=now)
        return PreparedRemoteAction(
            platform=self.platform,
            connection_id=account_id,
            action=action,
            before_state=expected.before_state,
            desired_state=expected.after_state,
            prepared_at=now,
        )


class NonLiveCountingLabAdapter(LabAdapter):
    def __init__(self) -> None:
        super().__init__(
            accounts=(
                LabAccountState(
                    id="lab-account",
                    topic_affinity={"ragebait": 0.8, "research": 0.2},
                    following={"rage-farm"},
                ),
            )
        )
        self.execute_calls = 0

    def execute(self, account_id, action, *, now):
        self.execute_calls += 1
        return super().execute(account_id, action, now=now)


class BlockingNonLiveLabAdapter(NonLiveCountingLabAdapter):
    def __init__(self, entered: Event, release: Event) -> None:
        super().__init__()
        self.entered = entered
        self.release = release

    def execute(self, account_id, action, *, now):
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("test did not release blocked migration")
        return super().execute(account_id, action, now=now)


def _build_live_migration(tmp_path, *, database_name, adapter=None, clock=None):
    store = SQLiteStore(tmp_path / database_name)
    connections = EncryptedConnectionRegistry(
        store,
        AesGcmKeyring(
            active_key_id="connection-v1",
            keys={"connection-v1": b"c" * 32},
            index_key=b"i" * 32,
        ),
        id_factory=lambda: "connection-live-test",
    )
    connections.register_connection(
        owner_id="owner-a",
        platform="live-test",
        external_subject="external-a",
        credential_ref="credential-a",
        metadata={"granted_scopes": ["test"]},
        now=NOW,
    )
    adapter = adapter or RecoverableLiveLabAdapter()
    application = CuratorApplication(
        store=store,
        adapters={adapter.platform: adapter},
        connections=connections,
        action_journal=store,
        clock=clock or (lambda: NOW),
        id_factory=iter(str(index) for index in range(1000)).__next__,
    )
    passport = application.create_passport(
        owner_id="owner-a",
        name="Recovery test",
        intent="Replace ragebait with research.",
        topic_targets={"research": 1.0},
        hard_exclusions=frozenset({"ragebait"}),
    )
    migration = application.prepare_migration(
        passport_id=passport.id,
        platform="live-test",
        destination_account_id="connection-live-test",
        actor_id="owner-a",
    )
    return store, adapter, application, migration


def _build_non_live_migration(tmp_path, *, database_name, actionless=False):
    store = SQLiteStore(tmp_path / database_name)
    adapter = NonLiveCountingLabAdapter()
    application = CuratorApplication(
        store=store,
        adapters={adapter.platform: adapter},
        clock=lambda: NOW,
        id_factory=iter(str(index) for index in range(1000)).__next__,
    )
    passport = application.create_passport(
        owner_id="owner-a",
        name="Finalization test",
        intent="Replace ragebait with research.",
        topic_targets={"research": 1.0},
        hard_exclusions=frozenset({"ragebait"}),
    )
    migration = application.prepare_migration(
        passport_id=passport.id,
        platform=adapter.platform,
        destination_account_id="lab-account",
        actor_id="owner-a",
    )
    if actionless:
        migration = dict(migration)
        migration["plan"] = {**migration["plan"], "actions": []}
        application._record(
            kind="migrations",
            aggregate_id=migration["id"],
            aggregate_type="migration",
            event_type="migration.test_actionless_plan",
            projection=migration,
            payload={"action_count": 0},
            actor_id="owner-a",
        )
    return store, adapter, application, migration


def _inject_one_finalize_crash(application):
    original_record = application._record
    crashed = False

    def faulting_record(**kwargs):
        nonlocal crashed
        if kwargs.get("event_type") == "migration.executed" and not crashed:
            crashed = True
            raise RuntimeError("simulated crash after receipt before migration finalization")
        return original_record(**kwargs)

    application._record = faulting_record
    return original_record


def _inject_post_receipt_crash(application):
    original_record = application._record
    crashed = False

    def faulting_record(**kwargs):
        nonlocal crashed
        result = original_record(**kwargs)
        if kwargs.get("event_type") == "receipt.issued" and not crashed:
            crashed = True
            raise RuntimeError("simulated crash immediately after receipt persistence")
        return result

    application._record = faulting_record


def test_unknown_live_write_is_reconciled_without_duplicate_dispatch(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "live-recovery.db")
    connections = EncryptedConnectionRegistry(
        store,
        AesGcmKeyring(
            active_key_id="connection-v1",
            keys={"connection-v1": b"c" * 32},
            index_key=b"i" * 32,
        ),
        id_factory=lambda: "connection-live-test",
    )
    connections.register_connection(
        owner_id="owner-a",
        platform="live-test",
        external_subject="external-a",
        credential_ref="credential-a",
        metadata={"granted_scopes": ["test"]},
        now=NOW,
    )
    adapter = CurrentStatePreparingLiveLabAdapter()
    application = CuratorApplication(
        store=store,
        adapters={adapter.platform: adapter},
        connections=connections,
        action_journal=store,
        clock=lambda: NOW,
        id_factory=iter(str(index) for index in range(1000)).__next__,
    )
    passport = application.create_passport(
        owner_id="owner-a",
        name="Recovery test",
        intent="Replace ragebait with research.",
        topic_targets={"research": 1.0},
        hard_exclusions=frozenset({"ragebait"}),
    )
    migration = application.prepare_migration(
        passport_id=passport.id,
        platform="live-test",
        destination_account_id="connection-live-test",
        actor_id="owner-a",
    )

    paused = application.execute_migration(migration["id"], approved_by="owner-a")

    assert paused["status"] == "reconciliation_required"
    attempts = store.list_remote_actions(owner_id="owner-a", migration_id=migration["id"])
    assert len(attempts) == 1
    first_action_id = attempts[0].action_id
    assert attempts[0].state is RemoteActionState.UNKNOWN
    assert adapter.apply_counts[first_action_id] == 1
    assert adapter.prepare_counts[first_action_id] == 1

    reconciled = application.reconcile_migration(migration["id"], actor_id="owner-a")
    assert reconciled["status"] == "failed_recoverable"
    assert store.list_remote_actions(
        owner_id="owner-a", migration_id=migration["id"]
    )[0].state is RemoteActionState.SUCCEEDED

    completed = application.execute_migration(migration["id"], approved_by="owner-a")
    assert completed["status"] in {"issued", "issued_with_drift"}
    assert adapter.apply_counts[first_action_id] == 1
    assert adapter.prepare_counts[first_action_id] == 1
    receipt = application.projection_list("receipts")[0]
    first_outcome = next(
        outcome for outcome in receipt["outcomes"] if outcome["action"]["id"] == first_action_id
    )
    assert first_outcome["status"] in {ActionStatus.EXECUTED.value, ActionStatus.SKIPPED.value}
    store.close()


def test_runtime_recovery_observes_unknown_write_without_replaying_it(tmp_path) -> None:
    store, adapter, application, migration = _build_live_migration(
        tmp_path,
        database_name="runtime-observer-recovery.db",
    )
    try:
        paused = application.execute_migration(migration["id"], approved_by="owner-a")
        attempt = store.list_remote_actions(
            owner_id="owner-a",
            migration_id=migration["id"],
        )[0]
        dispatch_count = adapter.apply_counts[attempt.action_id]

        recovered = application.recover_uncertain_remote_actions(at=NOW)

        assert paused["status"] == "reconciliation_required"
        assert recovered == (
            {
                "migration_id": migration["id"],
                "owner_id": "owner-a",
                "status": "failed_recoverable",
                "operation": "reconcile",
            },
        )
        assert adapter.apply_counts[attempt.action_id] == dispatch_count
        assert store.get_remote_action(attempt.id, owner_id="owner-a").state is RemoteActionState.SUCCEEDED
    finally:
        store.close()


def test_runtime_reconciliation_uses_durable_backoff_and_stops_for_a_human(tmp_path) -> None:
    current_time = [NOW]
    store, adapter, application, migration = _build_live_migration(
        tmp_path,
        database_name="runtime-reconciliation-backoff.db",
        clock=lambda: current_time[0],
    )
    try:
        application.execute_migration(migration["id"], approved_by="owner-a")
        adapter.reconciliation_error = LiveAuthenticationError(
            platform=adapter.platform,
            code="platform_unavailable",
            detail="The provider remains unavailable.",
        )

        first = application.recover_uncertain_remote_actions(at=current_time[0])
        attempt = store.list_remote_actions(
            owner_id="owner-a",
            migration_id=migration["id"],
        )[0]
        assert first[0]["status"] == "reconciliation_required"
        assert adapter.reconcile_calls == 1
        assert attempt.state is RemoteActionState.UNKNOWN
        assert attempt.next_attempt_at == NOW + timedelta(seconds=5)

        current_time[0] = NOW + timedelta(seconds=4)
        assert application.recover_uncertain_remote_actions(at=current_time[0]) == ()
        assert adapter.reconcile_calls == 1

        for due in (5, 15, 35):
            current_time[0] = NOW + timedelta(seconds=due)
            application.recover_uncertain_remote_actions(at=current_time[0])

        terminal = store.list_remote_actions(
            owner_id="owner-a",
            migration_id=migration["id"],
        )[0]
        assert terminal.state is RemoteActionState.NEEDS_HUMAN
        assert terminal.error_code == "reconciliation_attempt_limit"
        assert terminal.attempt_count == 5
        assert terminal.next_attempt_at is None
        assert adapter.reconcile_calls == 4

        current_time[0] = NOW + timedelta(days=1)
        assert application.recover_uncertain_remote_actions(at=current_time[0]) == ()
        assert adapter.reconcile_calls == 4
    finally:
        store.close()


def test_runtime_recovery_never_dispatches_a_pending_forward_write(tmp_path) -> None:
    adapter = RecoverableLiveLabAdapter(interrupt_first_write=False)
    store, adapter, application, migration = _build_live_migration(
        tmp_path,
        database_name="runtime-pending-no-dispatch.db",
        adapter=adapter,
    )
    try:
        action = action_from_dict(migration["plan"]["actions"][0])
        prepared = adapter.prepare_remote_action("connection-live-test", action, now=NOW)
        reserved = store.reserve_remote_action(
            attempt_id="pending-user-approval-bound-action",
            owner_id="owner-a",
            connection_id="connection-live-test",
            platform=adapter.platform,
            migration_id=migration["id"],
            action_id=action.id,
            operation=action.action_type.value,
            idempotency_key=action.idempotency_key,
            request_fingerprint=prepared.idempotency_fingerprint,
            action_payload={
                "platform": prepared.platform,
                "connection_id": prepared.connection_id,
                "action": migration["plan"]["actions"][0],
                "before_state": dict(prepared.before_state),
                "desired_state": dict(prepared.desired_state),
                "prepared_at": prepared.prepared_at.isoformat(),
            },
            created_at=NOW,
        )

        assert application.recover_uncertain_remote_actions(at=NOW) == ()
        assert adapter.apply_counts == {}
        assert store.get_remote_action(reserved.id, owner_id="owner-a").state is RemoteActionState.PENDING
    finally:
        store.close()


def test_live_rollback_transitions_durable_attempts(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "live-rollback.db")
    connections = EncryptedConnectionRegistry(
        store,
        AesGcmKeyring(
            active_key_id="connection-v1",
            keys={"connection-v1": b"c" * 32},
            index_key=b"i" * 32,
        ),
        id_factory=lambda: "connection-live-test",
    )
    connections.register_connection(
        owner_id="owner-a",
        platform="live-test",
        external_subject="external-a",
        credential_ref="credential-a",
        metadata={"granted_scopes": ["test"]},
        now=NOW,
    )
    adapter = RecoverableLiveLabAdapter(interrupt_first_write=False)
    application = CuratorApplication(
        store=store,
        adapters={adapter.platform: adapter},
        connections=connections,
        action_journal=store,
        clock=lambda: NOW,
        id_factory=iter(str(index) for index in range(1000)).__next__,
    )
    passport = application.create_passport(
        owner_id="owner-a",
        name="Rollback test",
        intent="Replace ragebait with research.",
        topic_targets={"research": 1.0},
        hard_exclusions=frozenset({"ragebait"}),
    )
    migration = application.prepare_migration(
        passport_id=passport.id,
        platform="live-test",
        destination_account_id="connection-live-test",
        actor_id="owner-a",
    )
    completed = application.execute_migration(migration["id"], approved_by="owner-a")
    receipt_id = completed["receipt_id"]

    rolled_back = application.rollback_receipt(
        receipt_id,
        actor_id="owner-a",
        platform="live-test",
    )

    assert rolled_back["status"] == "rolled_back"
    attempts = store.list_remote_actions(owner_id="owner-a", migration_id=migration["id"])
    assert attempts
    assert all(attempt.state is RemoteActionState.ROLLED_BACK for attempt in attempts)
    store.close()


def test_repeated_migration_gets_distinct_provider_idempotency_after_rollback(tmp_path) -> None:
    adapter = RecoverableLiveLabAdapter(interrupt_first_write=False)
    store, adapter, application, first = _build_live_migration(
        tmp_path,
        database_name="repeated-migration-idempotency.db",
        adapter=adapter,
    )
    try:
        first_result = application.execute_migration(first["id"], approved_by="owner-a")
        first_attempts = store.list_remote_actions(
            owner_id="owner-a",
            migration_id=first["id"],
        )
        application.rollback_receipt(
            first_result["receipt_id"],
            actor_id="owner-a",
            platform="live-test",
        )

        second = application.prepare_migration(
            passport_id=first["passport_id"],
            platform="live-test",
            destination_account_id="connection-live-test",
            actor_id="owner-a",
        )
        second_result = application.execute_migration(second["id"], approved_by="owner-a")
        second_attempts = store.list_remote_actions(
            owner_id="owner-a",
            migration_id=second["id"],
        )

        assert second_result["status"] in {"issued", "issued_with_drift"}
        assert first_attempts and len(second_attempts) == len(first_attempts)
        assert {value.id for value in first_attempts}.isdisjoint(
            value.id for value in second_attempts
        )
        assert {value.idempotency_key for value in first_attempts}.isdisjoint(
            value.idempotency_key for value in second_attempts
        )
        assert all(count == 1 for count in adapter.apply_counts.values())
    finally:
        store.close()


def test_partial_rollback_retry_passes_only_still_eligible_actions_to_provider(tmp_path) -> None:
    adapter = RecoverableLiveLabAdapter(interrupt_first_write=False)
    store, adapter, application, migration = _build_live_migration(
        tmp_path,
        database_name="partial-rollback-subset.db",
        adapter=adapter,
    )
    try:
        completed = application.execute_migration(migration["id"], approved_by="owner-a")
        attempts = store.list_remote_actions(owner_id="owner-a", migration_id=migration["id"])
        assert len(attempts) >= 2
        already_restored = attempts[0]
        pending = store.transition_remote_action(
            already_restored.id,
            expected_state=RemoteActionState.SUCCEEDED,
            new_state=RemoteActionState.ROLLBACK_PENDING,
            updated_at=NOW + timedelta(seconds=1),
            lease_owner="completed-earlier-worker",
            lease_expires_at=NOW + timedelta(minutes=1),
        )
        store.transition_remote_action(
            pending.id,
            expected_state=RemoteActionState.ROLLBACK_PENDING,
            new_state=RemoteActionState.ROLLED_BACK,
            updated_at=NOW + timedelta(seconds=2),
            after_state=already_restored.before_state,
        )

        result = application.rollback_receipt(
            completed["receipt_id"],
            actor_id="owner-a",
            platform="live-test",
        )

        eligible_ids = {value.action_id for value in attempts[1:]}
        receipt = application.projection_get("receipts", completed["receipt_id"])
        expected = tuple(
            value["action"]["id"]
            for value in receipt["outcomes"]
            if value["action"]["id"] in eligible_ids
        )
        assert result["status"] == "rolled_back"
        assert adapter.rollback_action_batches[-1] == expected
        assert already_restored.action_id not in adapter.rollback_action_batches[-1]
        assert all(
            value.state is RemoteActionState.ROLLED_BACK
            for value in store.list_remote_actions(
                owner_id="owner-a",
                migration_id=migration["id"],
            )
        )
    finally:
        store.close()


def test_reconciliation_read_failure_preserves_unknown_remote_outcome(tmp_path) -> None:
    store, adapter, application, migration = _build_live_migration(
        tmp_path,
        database_name="reconciliation-read-failure.db",
    )
    try:
        paused = application.execute_migration(migration["id"], approved_by="owner-a")
        assert paused["status"] == "reconciliation_required"
        adapter.reconciliation_error = LiveAuthenticationError(
            platform=adapter.platform,
            code="platform_authentication_failed",
            detail="The test credential cannot currently read remote state.",
        )

        unresolved = application.reconcile_migration(migration["id"], actor_id="owner-a")

        attempt = store.list_remote_actions(
            owner_id="owner-a",
            migration_id=migration["id"],
        )[0]
        assert unresolved["status"] == "reconciliation_required"
        assert attempt.state is RemoteActionState.UNKNOWN
        assert attempt.error_code == "platform_authentication_failed"

        adapter.reconciliation_error = None
        recovered = application.reconcile_migration(migration["id"], actor_id="owner-a")
        assert recovered["status"] == "failed_recoverable"
        assert store.list_remote_actions(
            owner_id="owner-a",
            migration_id=migration["id"],
        )[0].state is RemoteActionState.SUCCEEDED
    finally:
        store.close()


def test_expired_reconciliation_lease_is_reclaimed_and_observed(tmp_path) -> None:
    current_time = [NOW]
    store, _adapter, application, migration = _build_live_migration(
        tmp_path,
        database_name="expired-reconciliation-lease.db",
        clock=lambda: current_time[0],
    )
    try:
        application.execute_migration(migration["id"], approved_by="owner-a")
        unknown = store.list_remote_actions(
            owner_id="owner-a",
            migration_id=migration["id"],
        )[0]
        store.transition_remote_action(
            unknown.id,
            expected_state=RemoteActionState.UNKNOWN,
            new_state=RemoteActionState.RECONCILING,
            updated_at=NOW + timedelta(seconds=1),
            lease_owner="crashed-reconciler",
            lease_expires_at=NOW + timedelta(minutes=2),
        )
        current_time[0] = NOW + timedelta(minutes=3)

        recovered = application.reconcile_migration(migration["id"], actor_id="owner-a")

        assert recovered["status"] == "failed_recoverable"
        attempt = store.get_remote_action(unknown.id, owner_id="owner-a")
        assert attempt.state is RemoteActionState.SUCCEEDED
    finally:
        store.close()


def test_execution_started_without_reservation_is_safe_to_resume(tmp_path) -> None:
    adapter = RecoverableLiveLabAdapter(interrupt_first_write=False)
    adapter.interrupt_before_reservation = True
    store, adapter, application, migration = _build_live_migration(
        tmp_path,
        database_name="execution-started-no-reservation.db",
        adapter=adapter,
    )
    try:
        with pytest.raises(RuntimeError, match="before durable reservation"):
            application.execute_migration(migration["id"], approved_by="owner-a")

        stored_migration = next(
            value for value in application.projection_list("migrations") if value["id"] == migration["id"]
        )
        assert stored_migration["status"] == "executing"
        assert not store.list_remote_actions(owner_id="owner-a", migration_id=migration["id"])

        completed = application.execute_migration(migration["id"], approved_by="owner-a")

        assert completed["status"] in {"issued", "issued_with_drift"}
        assert adapter.apply_counts
        assert set(adapter.apply_counts.values()) == {1}
    finally:
        store.close()


def test_live_receipt_finalization_recovers_without_duplicate_provider_dispatch(tmp_path) -> None:
    adapter = RecoverableLiveLabAdapter(interrupt_first_write=False)
    store, adapter, application, migration = _build_live_migration(
        tmp_path,
        database_name="live-receipt-finalization.db",
        adapter=adapter,
    )
    try:
        _inject_one_finalize_crash(application)
        with pytest.raises(RuntimeError, match="after receipt"):
            application.execute_migration(migration["id"], approved_by="owner-a")
        dispatches = dict(adapter.apply_counts)
        receipts = application.projection_list("receipts")
        assert len(receipts) == 1

        recovered = application.execute_migration(migration["id"], approved_by="owner-a")
        replay = application.execute_migration(migration["id"], approved_by="owner-a")

        assert recovered["receipt_id"] == receipts[0]["id"]
        assert replay["receipt_id"] == receipts[0]["id"]
        assert adapter.apply_counts == dispatches
        assert len(application.projection_list("receipts")) == 1
    finally:
        store.close()


@pytest.mark.parametrize("actionless", [False, True])
def test_non_live_receipt_finalization_recovers_once_including_actionless_migrations(
    tmp_path,
    actionless,
) -> None:
    store, adapter, application, migration = _build_non_live_migration(
        tmp_path,
        database_name=f"non-live-finalization-{actionless}.db",
        actionless=actionless,
    )
    try:
        _inject_one_finalize_crash(application)
        with pytest.raises(RuntimeError, match="after receipt"):
            application.execute_migration(migration["id"], approved_by="owner-a")
        execute_calls = adapter.execute_calls
        receipts = application.projection_list("receipts")
        assert len(receipts) == 1
        assert bool(receipts[0]["outcomes"]) is not actionless

        recovered = application.execute_migration(migration["id"], approved_by="owner-a")
        replay = application.execute_migration(migration["id"], approved_by="owner-a")

        assert recovered["receipt_id"] == receipts[0]["id"]
        assert replay["receipt_id"] == receipts[0]["id"]
        assert adapter.execute_calls == execute_calls
        assert len(application.projection_list("receipts")) == 1
    finally:
        store.close()


@pytest.mark.parametrize("actionless", [False, True])
def test_restart_after_receipt_restores_exact_non_live_adapter_snapshot_before_finalizing(
    tmp_path,
    actionless,
) -> None:
    database_path = tmp_path / f"post-receipt-restart-{actionless}.db"
    store, adapter, application, migration = _build_non_live_migration(
        tmp_path,
        database_name=database_path.name,
        actionless=actionless,
    )
    _inject_post_receipt_crash(application)
    with pytest.raises(RuntimeError, match="immediately after receipt"):
        application.execute_migration(migration["id"], approved_by="owner-a")
    receipt = application.projection_list("receipts")[0]
    expected_state = receipt["adapter_state_finalization"]
    assert expected_state == adapter.export_state("lab-account")
    store.close()

    restarted_store = SQLiteStore(database_path)
    restarted_adapter = NonLiveCountingLabAdapter()
    restarted = CuratorApplication(
        store=restarted_store,
        adapters={restarted_adapter.platform: restarted_adapter},
        clock=lambda: NOW,
        id_factory=iter(str(index) for index in range(2000, 3000)).__next__,
    )
    try:
        recovered = restarted.execute_migration(migration["id"], approved_by="owner-a")

        assert recovered["receipt_id"] == receipt["id"]
        assert restarted_adapter.execute_calls == 0
        assert restarted_adapter.export_state("lab-account") == expected_state
        persisted = restarted.projection_get(
            "adapter_accounts",
            f"{restarted_adapter.platform}:lab-account",
        )
        assert persisted["state"] == expected_state
        assert len(restarted.projection_list("receipts")) == 1
    finally:
        restarted_store.close()


def test_cross_runtime_claim_prevents_concurrent_non_live_execution_and_duplicate_receipts(
    tmp_path,
) -> None:
    database_path = tmp_path / "cross-runtime-migration-claim.db"
    entered = Event()
    release = Event()
    first_store = SQLiteStore(database_path)
    first_adapter = BlockingNonLiveLabAdapter(entered, release)
    first = CuratorApplication(
        store=first_store,
        adapters={first_adapter.platform: first_adapter},
        clock=lambda: NOW,
        id_factory=iter(str(index) for index in range(1000)).__next__,
    )
    passport = first.create_passport(
        owner_id="owner-a",
        name="Concurrent approval test",
        intent="Replace ragebait with research.",
        topic_targets={"research": 1.0},
        hard_exclusions=frozenset({"ragebait"}),
    )
    migration = first.prepare_migration(
        passport_id=passport.id,
        platform=first_adapter.platform,
        destination_account_id="lab-account",
        actor_id="owner-a",
    )
    second_store = SQLiteStore(database_path)
    second_adapter = NonLiveCountingLabAdapter()
    second = CuratorApplication(
        store=second_store,
        adapters={second_adapter.platform: second_adapter},
        clock=lambda: NOW,
        id_factory=iter(str(index) for index in range(2000, 3000)).__next__,
    )
    first_result: list[dict] = []
    first_failure: list[BaseException] = []

    def run_first() -> None:
        try:
            first_result.append(
                first.execute_migration(migration["id"], approved_by="owner-a")
            )
        except BaseException as exc:
            first_failure.append(exc)

    worker = Thread(target=run_first, daemon=True)
    worker.start()
    try:
        assert entered.wait(timeout=5)
        with pytest.raises(InvalidStateError, match="claimed by another worker"):
            second.execute_migration(migration["id"], approved_by="owner-a")
        assert second_adapter.execute_calls == 0
    finally:
        release.set()
        worker.join(timeout=5)
        first_store.close()
        second_store.close()

    assert not worker.is_alive()
    assert first_failure == []
    assert len(first_result) == 1
    verifier = SQLiteStore(database_path)
    try:
        receipts = tuple(value for _, _, value in verifier.list_projections("receipts"))
        assert len(receipts) == 1
        assert receipts[0]["id"] == first_result[0]["receipt_id"]
    finally:
        verifier.close()


def test_execution_started_with_durable_dispatch_is_not_replayed(tmp_path) -> None:
    adapter = RecoverableLiveLabAdapter(interrupt_first_write=False)
    adapter.interrupt_before_reservation = True
    store, adapter, application, migration = _build_live_migration(
        tmp_path,
        database_name="execution-started-with-dispatch.db",
        adapter=adapter,
    )
    try:
        with pytest.raises(RuntimeError, match="before durable reservation"):
            application.execute_migration(migration["id"], approved_by="owner-a")
        action = action_from_dict(migration["plan"]["actions"][0])
        prepared = adapter.prepare_remote_action("connection-live-test", action, now=NOW)
        reserved = store.reserve_remote_action(
            attempt_id="possibly-dispatched-action",
            owner_id="owner-a",
            connection_id="connection-live-test",
            platform=adapter.platform,
            migration_id=migration["id"],
            action_id=action.id,
            operation=action.action_type.value,
            idempotency_key=action.idempotency_key,
            request_fingerprint=prepared.idempotency_fingerprint,
            action_payload={
                "platform": prepared.platform,
                "connection_id": prepared.connection_id,
                "action": migration["plan"]["actions"][0],
                "before_state": dict(prepared.before_state),
                "desired_state": dict(prepared.desired_state),
                "prepared_at": prepared.prepared_at.isoformat(),
            },
            created_at=NOW,
        )
        store.transition_remote_action(
            reserved.id,
            expected_state=RemoteActionState.PENDING,
            new_state=RemoteActionState.DISPATCHING,
            updated_at=NOW + timedelta(seconds=1),
            lease_owner="crashed-dispatch-worker",
            lease_expires_at=NOW + timedelta(minutes=2),
        )

        with pytest.raises(InvalidStateError, match="must reconcile first"):
            application.execute_migration(migration["id"], approved_by="owner-a")

        assert adapter.apply_counts == {}
        assert store.get_remote_action(reserved.id, owner_id="owner-a").state is RemoteActionState.DISPATCHING
    finally:
        store.close()


def test_active_rollback_lease_is_not_replayed_but_expired_lease_is_reclaimed(tmp_path) -> None:
    current_time = [NOW]
    adapter = RecoverableLiveLabAdapter(interrupt_first_write=False)
    store, adapter, application, migration = _build_live_migration(
        tmp_path,
        database_name="rollback-lease-recovery.db",
        adapter=adapter,
        clock=lambda: current_time[0],
    )
    try:
        completed = application.execute_migration(migration["id"], approved_by="owner-a")
        receipt_id = completed["receipt_id"]
        attempts = store.list_remote_actions(owner_id="owner-a", migration_id=migration["id"])
        assert attempts
        for attempt in attempts:
            assert attempt.state is RemoteActionState.SUCCEEDED
            store.transition_remote_action(
                attempt.id,
                expected_state=RemoteActionState.SUCCEEDED,
                new_state=RemoteActionState.ROLLBACK_PENDING,
                updated_at=NOW + timedelta(seconds=1),
                lease_owner="crashed-rollback-worker",
                lease_expires_at=NOW + timedelta(minutes=2),
            )

        current_time[0] = NOW + timedelta(minutes=1)
        with pytest.raises(InvalidStateError, match="rollback is already in progress"):
            application.rollback_receipt(
                receipt_id,
                actor_id="owner-a",
                platform="live-test",
            )
        assert adapter.rollback_calls == 0

        current_time[0] = NOW + timedelta(minutes=3)
        recovered = application.rollback_receipt(
            receipt_id,
            actor_id="owner-a",
            platform="live-test",
        )

        assert recovered["status"] == "rolled_back"
        assert adapter.rollback_calls == 1
        assert all(
            attempt.state is RemoteActionState.ROLLED_BACK
            for attempt in store.list_remote_actions(
                owner_id="owner-a",
                migration_id=migration["id"],
            )
        )
    finally:
        store.close()


def test_runtime_recovery_resumes_only_an_expired_receipt_bound_rollback(tmp_path) -> None:
    current_time = [NOW]
    adapter = RecoverableLiveLabAdapter(interrupt_first_write=False)
    store, adapter, application, migration = _build_live_migration(
        tmp_path,
        database_name="runtime-rollback-recovery.db",
        adapter=adapter,
        clock=lambda: current_time[0],
    )
    try:
        completed = application.execute_migration(migration["id"], approved_by="owner-a")
        for attempt in store.list_remote_actions(owner_id="owner-a", migration_id=migration["id"]):
            store.transition_remote_action(
                attempt.id,
                expected_state=RemoteActionState.SUCCEEDED,
                new_state=RemoteActionState.ROLLBACK_PENDING,
                updated_at=NOW + timedelta(seconds=1),
                lease_owner="crashed-rollback-worker",
                lease_expires_at=NOW + timedelta(minutes=2),
            )

        current_time[0] = NOW + timedelta(minutes=1)
        assert application.recover_uncertain_remote_actions(at=current_time[0]) == ()
        assert adapter.rollback_calls == 0

        current_time[0] = NOW + timedelta(minutes=3)
        recovered = application.recover_uncertain_remote_actions(at=current_time[0])

        assert recovered == (
            {
                "migration_id": migration["id"],
                "owner_id": "owner-a",
                "status": "rolled_back",
                "operation": "rollback",
            },
        )
        assert application.projection_get("receipts", completed["receipt_id"])["status"] == "rolled_back"
        assert adapter.rollback_calls == 1
    finally:
        store.close()


def test_runtime_rollback_recovery_backs_off_and_stops_before_unbounded_mutation(tmp_path) -> None:
    current_time = [NOW]
    adapter = RecoverableLiveLabAdapter(interrupt_first_write=False)
    store, adapter, application, migration = _build_live_migration(
        tmp_path,
        database_name="runtime-rollback-backoff.db",
        adapter=adapter,
        clock=lambda: current_time[0],
    )
    try:
        completed = application.execute_migration(migration["id"], approved_by="owner-a")
        adapter.rollback_error = RuntimeError("persistent provider outage")
        first = application.rollback_receipt(
            completed["receipt_id"],
            actor_id="owner-a",
            platform="live-test",
        )
        attempts = store.list_remote_actions(owner_id="owner-a", migration_id=migration["id"])
        assert first["status"] == "rollback_reconciliation_required"
        assert adapter.rollback_calls == 1
        assert {attempt.next_attempt_at for attempt in attempts} == {
            NOW + timedelta(seconds=5)
        }

        current_time[0] = NOW + timedelta(seconds=4)
        assert application.recover_uncertain_remote_actions(at=current_time[0]) == ()
        assert adapter.rollback_calls == 1

        final_recovery = ()
        for due in (5, 15, 35):
            current_time[0] = NOW + timedelta(seconds=due)
            final_recovery = application.recover_uncertain_remote_actions(at=current_time[0])

        terminal = store.list_remote_actions(owner_id="owner-a", migration_id=migration["id"])
        assert final_recovery[0]["status"] == "rollback_needs_human"
        assert all(value.state is RemoteActionState.NEEDS_HUMAN for value in terminal)
        assert all(value.attempt_count == 5 for value in terminal)
        assert adapter.rollback_calls == 4

        current_time[0] = NOW + timedelta(days=1)
        assert application.recover_uncertain_remote_actions(at=current_time[0]) == ()
        assert adapter.rollback_calls == 4
    finally:
        store.close()
