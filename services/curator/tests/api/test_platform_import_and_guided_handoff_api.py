from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from feed_passport.adapters.platforms.instagram import InstagramAdapter
from feed_passport.agent import ConsentBroker, CuratorAgentService
from feed_passport.api.app import create_app
from feed_passport.api import cli as api_cli
from feed_passport.application import CuratorApplication
from feed_passport.application.model_io import action_from_dict
from feed_passport.infrastructure import SQLiteStore
from feed_passport.ports.identity import AuthenticatedPrincipal
from feed_passport.runtime import ServiceBundle


class _StaticBearerVerifier:
    def verify(self, token: str, *, now: datetime) -> AuthenticatedPrincipal:
        subjects = {"token-a": "person-a", "token-b": "person-b"}
        return AuthenticatedPrincipal(
            subject=subjects[token],
            issuer="https://issuer.example.test",
            authenticated_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(minutes=10),
        )


def _existing_bundle(path: Path) -> ServiceBundle:
    store = SQLiteStore(path)
    adapter = InstagramAdapter()
    application = CuratorApplication(store=store, adapters={adapter.platform: adapter})
    broker = ConsentBroker(application, secret="platform-import-api-test")
    return ServiceBundle(
        store=store,
        application=application,
        broker=broker,
        agent_service=CuratorAgentService(application, broker),
    )


def _bundle(path: Path) -> tuple[ServiceBundle, str]:
    bundle = _existing_bundle(path)
    application = bundle.application
    passport = application.create_passport(
        owner_id="person-a",
        name="Private portable intent",
        intent="Carry explicitly selected creator intent without importing private activity history.",
        topic_targets={"research": 0.7, "culture": 0.3},
        creator_preferences={"already_here": 1.0},
        hard_exclusions=frozenset({"ragebait"}),
    )
    return bundle, passport.id


def _following_export() -> bytes:
    fixture = (
        Path(__file__).parents[1]
        / "fixtures"
        / "instagram"
        / "accounts_center_following.json"
    )
    return fixture.read_bytes()


def test_loopback_instagram_import_is_ephemeral_selective_and_provenanced() -> None:
    with tempfile.TemporaryDirectory() as directory:
        bundle, passport_id = _bundle(Path(directory) / "import.db")
        app = create_app(bundle, local_import_enabled=True)
        with TestClient(app, client=("127.0.0.1", 45123)) as client:
            preview_response = client.post(
                "/api/platform-imports/instagram/preview",
                params={
                    "actor_id": "person-a",
                    "passport_id": passport_id,
                    "filename": "following.json",
                },
                content=_following_export(),
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-Feed-Passport-Local-Import": "1",
                },
            )
            assert preview_response.status_code == 201
            preview = preview_response.json()
            assert preview["status"] == "ready"
            assert preview["followed_handles"] == ["city_zine", "paper.lab"]
            assert preview["selection_limit"] == 499
            assert preview["raw_source_retained"] is False
            assert preview["platform_account_accessed"] is False

            apply_response = client.post(
                f"/api/platform-imports/instagram/{preview['session_id']}/apply",
                json={
                    "actor_id": "person-a",
                    "passport_id": passport_id,
                    "expected_passport_version": 1,
                    "selected_handles": ["paper.lab"],
                },
                headers={"X-Feed-Passport-Local-Import": "1"},
            )
            assert apply_response.status_code == 200
            result = apply_response.json()
            assert result["import"]["status"] == "consumed"
            assert result["import"]["selected_relationship_count"] == 1
            assert result["platform_account_changed"] is False
            assert result["passport"]["version"] == 2
            assert result["passport"]["creator_preferences"] == {
                "already_here": 1.0,
                "paper.lab": 1.0,
            }
            assert result["passport"]["topic_targets"] == {
                "culture": 0.3,
                "research": 0.7,
            }
            assert result["passport"]["provenance"][-1]["source"] == (
                "user_supplied_instagram_following_export"
            )
            assert result["passport"]["provenance"][-1]["confidence"] == 0.8

            unavailable = client.post(
                f"/api/platform-imports/instagram/{preview['session_id']}/apply",
                json={
                    "actor_id": "person-a",
                    "passport_id": passport_id,
                    "expected_passport_version": 2,
                    "selected_handles": ["paper.lab"],
                },
                headers={"X-Feed-Passport-Local-Import": "1"},
            )
            assert unavailable.status_code == 404
            exported = bundle.application.export_passport(passport_id)
            assert exported["provenance"][-1]["kind"] == "user_provided"
        bundle.close()


def test_instagram_import_preview_cannot_cross_passports_and_remains_retryable() -> None:
    with tempfile.TemporaryDirectory() as directory:
        bundle, passport_id = _bundle(Path(directory) / "binding.db")
        other = bundle.application.create_passport(
            owner_id="person-a",
            name="Other Passport",
            intent="A separate destination policy.",
            topic_targets={"science": 1.0},
        )
        with TestClient(
            create_app(bundle, local_import_enabled=True),
            client=("127.0.0.1", 45123),
        ) as client:
            preview = client.post(
                "/api/platform-imports/instagram/preview",
                params={
                    "actor_id": "person-a",
                    "passport_id": passport_id,
                    "filename": "following.json",
                },
                content=_following_export(),
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-Feed-Passport-Local-Import": "1",
                },
            ).json()
            crossed = client.post(
                f"/api/platform-imports/instagram/{preview['session_id']}/apply",
                json={
                    "actor_id": "person-a",
                    "passport_id": other.id,
                    "expected_passport_version": other.version,
                    "selected_handles": ["paper.lab"],
                },
                headers={"X-Feed-Passport-Local-Import": "1"},
            )
            assert crossed.status_code == 409

            applied = client.post(
                f"/api/platform-imports/instagram/{preview['session_id']}/apply",
                json={
                    "actor_id": "person-a",
                    "passport_id": passport_id,
                    "expected_passport_version": 1,
                    "selected_handles": ["paper.lab"],
                },
                headers={"X-Feed-Passport-Local-Import": "1"},
            )
            assert applied.status_code == 200
        bundle.close()


def test_post_commit_companion_refresh_failure_does_not_make_import_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        bundle, passport_id = _bundle(Path(directory) / "post-commit.db")

        def fail_derived_refresh(_passport_id: str) -> None:
            raise RuntimeError("derived projection refresh failed")

        monkeypatch.setattr(
            bundle.application,
            "_refresh_continuous_companions_for_passport",
            fail_derived_refresh,
        )
        with TestClient(
            create_app(bundle, local_import_enabled=True),
            client=("127.0.0.1", 45123),
        ) as client:
            preview = client.post(
                "/api/platform-imports/instagram/preview",
                params={
                    "actor_id": "person-a",
                    "passport_id": passport_id,
                    "filename": "following.json",
                },
                content=_following_export(),
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-Feed-Passport-Local-Import": "1",
                },
            ).json()
            payload = {
                "actor_id": "person-a",
                "passport_id": passport_id,
                "expected_passport_version": 1,
                "selected_handles": ["paper.lab"],
            }
            applied = client.post(
                f"/api/platform-imports/instagram/{preview['session_id']}/apply",
                json=payload,
                headers={"X-Feed-Passport-Local-Import": "1"},
            )
            assert applied.status_code == 200
            assert applied.json()["passport"]["version"] == 2

            replay = client.post(
                f"/api/platform-imports/instagram/{preview['session_id']}/apply",
                json={**payload, "expected_passport_version": 2},
                headers={"X-Feed-Passport-Local-Import": "1"},
            )
            assert replay.status_code == 404
            assert bundle.application.get_passport(passport_id).version == 2
        bundle.close()


def test_instagram_import_is_disabled_by_default_and_rejects_non_loopback_clients() -> None:
    with tempfile.TemporaryDirectory() as directory:
        disabled_bundle, passport_id = _bundle(Path(directory) / "disabled.db")
        disabled_app = create_app(disabled_bundle)
        with TestClient(disabled_app, client=("127.0.0.1", 45123)) as client:
            response = client.post(
                "/api/platform-imports/instagram/preview",
                params={
                    "actor_id": "person-a",
                    "passport_id": passport_id,
                    "filename": "following.json",
                },
                content=_following_export(),
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-Feed-Passport-Local-Import": "1",
                },
            )
            assert response.status_code == 503
        disabled_bundle.close()

        remote_bundle, remote_passport_id = _bundle(Path(directory) / "remote.db")
        remote_app = create_app(remote_bundle, local_import_enabled=True)
        with TestClient(remote_app, client=("203.0.113.7", 45123)) as client:
            response = client.post(
                "/api/platform-imports/instagram/preview",
                params={
                    "actor_id": "person-a",
                    "passport_id": remote_passport_id,
                    "filename": "following.json",
                },
                content=_following_export(),
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-Feed-Passport-Local-Import": "1",
                },
            )
            assert response.status_code == 403
        remote_bundle.close()


def test_instagram_import_rejects_safelisted_cross_origin_shape_and_nonloopback_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        bundle, passport_id = _bundle(Path(directory) / "csrf.db")
        app = create_app(bundle, local_import_enabled=True)
        with TestClient(app, client=("127.0.0.1", 45123)) as client:
            missing_marker = client.post(
                "/api/platform-imports/instagram/preview",
                params={
                    "actor_id": "person-a",
                    "passport_id": passport_id,
                    "filename": "following.json",
                },
                content=_following_export(),
                headers={"Content-Type": "text/plain"},
            )
            assert missing_marker.status_code == 403

            safelisted_type = client.post(
                "/api/platform-imports/instagram/preview",
                params={
                    "actor_id": "person-a",
                    "passport_id": passport_id,
                    "filename": "following.json",
                },
                content=_following_export(),
                headers={"X-Feed-Passport-Local-Import": "1", "Content-Type": "text/plain"},
            )
            assert safelisted_type.status_code == 415
        bundle.close()

        blocked_bundle, _ = _bundle(Path(directory) / "remote-bind.db")
        monkeypatch.setenv("FEED_PASSPORT_BIND_HOST", "0.0.0.0")
        with pytest.raises(ValueError, match="loopback FEED_PASSPORT_BIND_HOST"):
            create_app(blocked_bundle, local_import_enabled=True)
        blocked_bundle.close()


def test_supported_cli_makes_actual_bind_host_authoritative_for_local_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEED_PASSPORT_ENABLE_LOCAL_IMPORT", "1")
    monkeypatch.setattr(sys, "argv", ["feed-passport-api", "--host", "0.0.0.0"])
    with pytest.raises(SystemExit) as blocked:
        api_cli.main()
    assert blocked.value.code == 2

    run_arguments: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        run_arguments.update(kwargs)

    monkeypatch.setattr(api_cli.uvicorn, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["feed-passport-api", "--host", "127.0.0.1"])
    api_cli.main()
    assert run_arguments["host"] == "127.0.0.1"
    assert os.environ["FEED_PASSPORT_BIND_HOST"] == "127.0.0.1"


def test_demo_mode_rejects_remote_clients_before_exposing_owner_state() -> None:
    with tempfile.TemporaryDirectory() as directory:
        bundle, _ = _bundle(Path(directory) / "remote-demo.db")
        with TestClient(
            create_app(bundle),
            client=("203.0.113.7", 45123),
        ) as client:
            response = client.get("/api/demo")
            assert response.status_code == 403
            assert response.json()["error"] == "demo_loopback_required"
            assert "person-a" not in response.text
        bundle.close()


def test_guided_instagram_migration_records_only_user_attestation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "guided.db"
        bundle, passport_id = _bundle(database_path)
        with TestClient(
            create_app(bundle), client=("127.0.0.1", 45123)
        ) as client:
            preview_response = client.post(
                "/api/migrations/preview",
                json={
                    "actor_id": "person-a",
                    "passport_id": passport_id,
                    "platform": "instagram",
                    "destination_account_id": "unconnected-dummy",
                },
            )
            assert preview_response.status_code == 201
            migration = preview_response.json()
            approval = client.post(
                f"/api/migrations/{migration['id']}/approval",
                json={"actor_id": "person-a"},
            ).json()
            executed_response = client.post(
                f"/api/migrations/{migration['id']}/execute",
                json={
                    "actor_id": "person-a",
                    "approval_token": approval["token"],
                },
            )
            assert executed_response.status_code == 200
            executed = executed_response.json()
            assert executed["status"] == "awaiting_handoff"
            assert executed["receipt_id"] is None
            handoff = executed["guided_handoff"]
            assert handoff["state"] == "awaiting_handoff"
            awaiting_snapshot = client.get("/api/demo").json()
            awaiting_migration = next(
                item
                for item in awaiting_snapshot["migrations"]
                if item["id"] == migration["id"]
            )
            assert awaiting_migration["guided_handoff"] == handoff
            assert awaiting_snapshot["receipts"] == []

            stale_intermediate_handoff = None
            for step in handoff["steps"]:
                resolved_response = client.post(
                    f"/api/guided-handoffs/{handoff['id']}/steps/{step['id']}/resolve",
                    json={
                        "actor_id": "person-a",
                        "resolution": "completed_by_user",
                    },
                )
                assert resolved_response.status_code == 200
                handoff = resolved_response.json()
                if stale_intermediate_handoff is None:
                    stale_intermediate_handoff = dict(handoff)
            assert handoff["state"] == "user_resolved"
            resolved_snapshot = client.get("/api/demo").json()
            resolved_migration = next(
                item
                for item in resolved_snapshot["migrations"]
                if item["id"] == migration["id"]
            )
            assert resolved_migration["guided_handoff"] == handoff
            assert resolved_snapshot["receipts"] == []

            finalized_response = client.post(
                f"/api/guided-handoffs/{handoff['id']}/finalize",
                json={"actor_id": "person-a"},
            )
            assert finalized_response.status_code == 200
            finalized = finalized_response.json()
            assert finalized["state"] == "finalized"
            assert finalized["receipt"]["summary"] == {
                "total_steps": len(handoff["steps"]),
                "completed_by_user": len(handoff["steps"]),
                "skipped_by_user": 0,
                "control_not_found": 0,
                "api_writes": 0,
                "recommendation_outcomes_verified": 0,
                "platform_verified": False,
            }
            stored_migration = bundle.application.projection_get(
                "migrations", migration["id"]
            )
            assert stored_migration["status"] == "guided_recorded"
            assert stored_migration["receipt_id"] is None
            assert stored_migration["guided_receipt_id"] == finalized["receipt"]["id"]
            finalized_snapshot = client.get("/api/demo").json()
            finalized_migration = next(
                item
                for item in finalized_snapshot["migrations"]
                if item["id"] == migration["id"]
            )
            assert finalized_migration["guided_handoff"] == finalized
            assert finalized_snapshot["receipts"] == []
            assert stale_intermediate_handoff is not None
            bundle.application._update_migration_handoff(
                stale_intermediate_handoff,
                actor_id="person-a",
                event_type="migration.test_stale_guided_projection",
            )
            after_stale_update = bundle.application.projection_get(
                "migrations", migration["id"]
            )
            assert after_stale_update["status"] == "guided_recorded"
            assert after_stale_update["guided_handoff"]["revision"] == finalized["revision"]
        bundle.close()

        restarted_bundle = _existing_bundle(database_path)
        try:
            with TestClient(
                create_app(restarted_bundle), client=("127.0.0.1", 45123)
            ) as restarted_client:
                restarted_response = restarted_client.get("/api/demo")
                assert restarted_response.status_code == 200
                restarted_snapshot = restarted_response.json()
                restarted_migration = next(
                    item
                    for item in restarted_snapshot["migrations"]
                    if item["id"] == migration["id"]
                )
                assert restarted_migration["guided_handoff"] == finalized
                assert restarted_migration["receipt_id"] is None
                assert (
                    restarted_migration["guided_receipt_id"]
                    == finalized["receipt"]["id"]
                )
                assert restarted_snapshot["receipts"] == []
        finally:
            restarted_bundle.close()


def test_one_active_guided_handoff_per_owner_passport_allows_same_session_resume() -> None:
    with tempfile.TemporaryDirectory() as directory:
        bundle, passport_id = _bundle(Path(directory) / "one-active.db")
        try:
            with TestClient(
                create_app(bundle), client=("127.0.0.1", 45123)
            ) as client:
                first = client.post(
                    "/api/migrations/preview",
                    json={
                        "actor_id": "person-a",
                        "passport_id": passport_id,
                        "platform": "instagram",
                        "destination_account_id": "unconnected-dummy",
                    },
                ).json()
                actions = tuple(
                    action_from_dict(value) for value in first["plan"]["actions"]
                )

                # Simulate a stop after the authoritative session begins but
                # before the migration projection embeds it. Re-entering the
                # same deterministic session must be idempotent.
                opened = bundle.application._open_guided_handoff(
                    migration=first,
                    actions=actions,
                    actor_id="person-a",
                )
                resumed = bundle.application._open_guided_handoff(
                    migration=first,
                    actions=actions,
                    actor_id="person-a",
                )
                assert resumed == opened
                assert resumed["state"] == "awaiting_handoff"

                first_approval = client.post(
                    f"/api/migrations/{first['id']}/approval",
                    json={"actor_id": "person-a"},
                ).json()
                first_execution = client.post(
                    f"/api/migrations/{first['id']}/execute",
                    json={
                        "actor_id": "person-a",
                        "approval_token": first_approval["token"],
                    },
                )
                assert first_execution.status_code == 200
                assert first_execution.json()["guided_handoff"]["id"] == opened["id"]

                second = client.post(
                    "/api/migrations/preview",
                    json={
                        "actor_id": "person-a",
                        "passport_id": passport_id,
                        "platform": "instagram",
                        "destination_account_id": "another-unconnected-dummy",
                    },
                ).json()
                second_approval = client.post(
                    f"/api/migrations/{second['id']}/approval",
                    json={"actor_id": "person-a"},
                ).json()
                blocked = client.post(
                    f"/api/migrations/{second['id']}/execute",
                    json={
                        "actor_id": "person-a",
                        "approval_token": second_approval["token"],
                    },
                )
                assert blocked.status_code == 409
                assert "active guided handoff already exists" in blocked.json()["detail"]
                assert "skip every step and finalize" in blocked.json()["detail"]
                assert (
                    bundle.application.projection_get("migrations", second["id"])["status"]
                    == "awaiting_approval"
                )
                active = [
                    value
                    for value in bundle.application.projection_list("guided_handoffs")
                    if value["state"] in {"awaiting_handoff", "user_resolved"}
                ]
                assert [value["id"] for value in active] == [opened["id"]]
        finally:
            bundle.close()


@pytest.mark.parametrize("crash_point", ["previewed", "consented"])
def test_demo_recovers_pre_attach_guided_handoff_crash_points(
    crash_point: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        bundle, passport_id = _bundle(Path(directory) / f"recover-{crash_point}.db")
        try:
            with TestClient(
                create_app(bundle), client=("127.0.0.1", 45123)
            ) as client:
                migration = client.post(
                    "/api/migrations/preview",
                    json={
                        "actor_id": "person-a",
                        "passport_id": passport_id,
                        "platform": "instagram",
                        "destination_account_id": "unconnected-dummy",
                    },
                ).json()
                actions = tuple(
                    action_from_dict(value) for value in migration["plan"]["actions"]
                )
                service = bundle.application.guided_handoffs
                method_name = "consent" if crash_point == "previewed" else "begin_handoff"
                original = getattr(service, method_name)

                def interrupt(*args, **kwargs):
                    raise RuntimeError(f"simulated {crash_point} crash point")

                monkeypatch.setattr(service, method_name, interrupt)
                with pytest.raises(RuntimeError, match=crash_point):
                    bundle.application._open_guided_handoff(
                        migration=migration,
                        actions=actions,
                        actor_id="person-a",
                    )
                monkeypatch.setattr(service, method_name, original)

                interrupted = bundle.application.projection_list("guided_handoffs")[0]
                assert interrupted["state"] == crash_point
                raw_migration = bundle.application.projection_get(
                    "migrations", migration["id"]
                )
                assert raw_migration.get("guided_handoff_id") is None
                assert raw_migration["status"] == "awaiting_approval"

                response = client.get("/api/demo")
                assert response.status_code == 200
                repaired = next(
                    value
                    for value in response.json()["migrations"]
                    if value["id"] == migration["id"]
                )
                handoff = repaired["guided_handoff"]
                assert repaired["status"] == "awaiting_handoff"
                assert repaired["guided_handoff_id"] == interrupted["id"]
                assert handoff["state"] == "awaiting_handoff"
                assert handoff["steps_sha256"] == interrupted["steps_sha256"]
                assert handoff["consent_reference"] == (
                    bundle.application._guided_handoff_consent_reference(
                        migration,
                        interrupted["steps_sha256"],
                    )
                )
                assert handoff["revision"] == 3
                assert repaired["execution_summary"] == {
                    "mode": "guided_handoff_recovered",
                    "executed_action_count": 0,
                    "remote_write_count": 0,
                    "guided_action_count": len(handoff["steps"]),
                    "skipped_action_count": 0,
                    "failed_action_count": 0,
                }

                second_read = next(
                    value
                    for value in client.get("/api/demo").json()["migrations"]
                    if value["id"] == migration["id"]
                )
                assert second_read["guided_handoff"] == handoff
                assert (
                    bundle.application.projection_get(
                        "migrations", migration["id"]
                    )["guided_handoff"]
                    == handoff
                )
        finally:
            bundle.close()


def test_authenticated_migration_read_never_recovers_or_validates_a_foreign_orphan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        bundle, _ = _bundle(Path(directory) / "owner-scoped-recovery.db")
        try:
            passport_b = bundle.application.create_passport(
                owner_id="person-b",
                name="B's private intent",
                intent="Keep another owner's recovery work isolated.",
                topic_targets={"design": 1.0},
                creator_preferences={"designer-b": 1.0},
            )
            with TestClient(
                create_app(bundle), client=("127.0.0.1", 45123)
            ) as demo_client:
                migration_b = demo_client.post(
                    "/api/migrations/preview",
                    json={
                        "actor_id": "person-b",
                        "passport_id": passport_b.id,
                        "platform": "instagram",
                        "destination_account_id": "person-b-unconnected-dummy",
                    },
                ).json()

            actions_b = tuple(
                action_from_dict(value) for value in migration_b["plan"]["actions"]
            )
            guided_service = bundle.application.guided_handoffs
            original_consent = guided_service.consent

            def interrupt_consent(*args, **kwargs):
                raise RuntimeError("simulated foreign preview crash")

            monkeypatch.setattr(guided_service, "consent", interrupt_consent)
            with pytest.raises(RuntimeError, match="foreign preview crash"):
                bundle.application._open_guided_handoff(
                    migration=migration_b,
                    actions=actions_b,
                    actor_id="person-b",
                )
            monkeypatch.setattr(guided_service, "consent", original_consent)

            valid_foreign_orphan = next(
                value
                for value in bundle.application.projection_list("guided_handoffs")
                if value["owner_id"] == "person-b"
            )
            assert valid_foreign_orphan["state"] == "previewed"

            # This second foreign projection is deliberately not bound to the
            # deterministic migration session id. A global reconciliation
            # would fail the request after potentially advancing the valid
            # orphan; an owner-scoped read must inspect neither.
            plan_b = bundle.application._guided_translation_plan(
                migration_b,
                actions=actions_b,
            )
            guided_service.preview(
                session_id="foreign-unbound-guided-handoff",
                owner_id="person-b",
                platform="instagram",
                plan=plan_b,
                now=bundle.application._now(),
            )

            oidc_app = create_app(
                bundle,
                auth_mode="oidc",
                bearer_verifier=_StaticBearerVerifier(),
            )
            with TestClient(
                oidc_app, client=("127.0.0.1", 45123)
            ) as owner_a_client:
                response = owner_a_client.get(
                    "/api/demo",
                    headers={"Authorization": "Bearer token-a"},
                )

            assert response.status_code == 200
            assert all(
                value["owner_id"] == "person-a"
                for value in response.json()["migrations"]
            )
            still_previewed = bundle.application.guided_handoffs.get(
                valid_foreign_orphan["id"],
                actor_id="person-b",
            )
            assert still_previewed.state.value == "previewed"
            raw_foreign_migration = bundle.application.projection_get(
                "migrations", migration_b["id"]
            )
            assert raw_foreign_migration["status"] == "awaiting_approval"
            assert raw_foreign_migration.get("guided_handoff_id") is None
        finally:
            bundle.close()


def test_demo_repairs_stale_migration_handoff_from_authoritative_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "guided-read-repair.db"
        bundle, passport_id = _bundle(database_path)
        try:
            with TestClient(
                create_app(bundle), client=("127.0.0.1", 45123)
            ) as client:
                migration = client.post(
                    "/api/migrations/preview",
                    json={
                        "actor_id": "person-a",
                        "passport_id": passport_id,
                        "platform": "instagram",
                        "destination_account_id": "unconnected-dummy",
                    },
                ).json()
                approval = client.post(
                    f"/api/migrations/{migration['id']}/approval",
                    json={"actor_id": "person-a"},
                ).json()
                executed = client.post(
                    f"/api/migrations/{migration['id']}/execute",
                    json={
                        "actor_id": "person-a",
                        "approval_token": approval["token"],
                    },
                ).json()
                embedded_before = executed["guided_handoff"]

                # Reproduce a process stop between the authoritative handoff
                # write and its denormalized migration mirror.
                monkeypatch.setattr(
                    bundle.application,
                    "_update_migration_handoff",
                    lambda *args, **kwargs: None,
                )
                handoff = embedded_before
                for step in handoff["steps"]:
                    handoff = client.post(
                        f"/api/guided-handoffs/{handoff['id']}/steps/{step['id']}/resolve",
                        json={
                            "actor_id": "person-a",
                            "resolution": "skipped_by_user",
                        },
                    ).json()
                assert handoff["state"] == "user_resolved"
                raw_resolved = bundle.application.projection_get(
                    "migrations", migration["id"]
                )
                assert raw_resolved["guided_handoff"] == embedded_before
                resolved_view = next(
                    value
                    for value in client.get("/api/demo").json()["migrations"]
                    if value["id"] == migration["id"]
                )
                assert resolved_view["guided_handoff"] == handoff
                assert resolved_view["status"] == "handoff_resolved"

                finalized = client.post(
                    f"/api/guided-handoffs/{handoff['id']}/finalize",
                    json={"actor_id": "person-a"},
                ).json()
                assert finalized["state"] == "finalized"
                raw_finalized = bundle.application.projection_get(
                    "migrations", migration["id"]
                )
                assert raw_finalized["guided_handoff"] == embedded_before
                snapshot = client.get("/api/demo").json()
                finalized_view = next(
                    value
                    for value in snapshot["migrations"]
                    if value["id"] == migration["id"]
                )
                assert finalized_view["guided_handoff"] == finalized
                assert finalized_view["status"] == "guided_recorded"
                assert finalized_view["receipt_id"] is None
                assert finalized_view["guided_receipt_id"] == finalized["receipt"]["id"]
                assert snapshot["receipts"] == []
        finally:
            bundle.close()

        restarted = _existing_bundle(database_path)
        try:
            with TestClient(
                create_app(restarted), client=("127.0.0.1", 45123)
            ) as client:
                snapshot = client.get("/api/demo").json()
                repaired = next(
                    value
                    for value in snapshot["migrations"]
                    if value["id"] == migration["id"]
                )
                assert repaired["guided_handoff"] == finalized
                assert repaired["status"] == "guided_recorded"
                assert repaired["receipt_id"] is None
                assert snapshot["receipts"] == []
        finally:
            restarted.close()
