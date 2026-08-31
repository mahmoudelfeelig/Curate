from __future__ import annotations

import json
import socket
from pathlib import Path

from evaluation.cli import DEFAULT_REPORT_PATH, main
from evaluation.suite import SCENARIO_IDS, run_suite


EXPECTED_SCENARIOS = {
    "passport_portability_and_checkpoints",
    "migration_quality",
    "policy_safety",
    "rollback_fidelity",
    "temporary_overlay_isolation_expiry",
    "temporary_visa_early_revoke",
    "consent_scoped_companion_blending",
    "idempotency",
    "capability_honesty",
    "local_platform_twin_matrix",
    "bounded_agent_mission_lifecycle",
    "drift_detection",
    "scheduled_drift_monitor",
    "creator_continuity",
}


def _deny_network(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("the deterministic evaluation suite must not use the network")


def test_suite_is_deterministic_complete_and_offline(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(socket, "create_connection", _deny_network)
    monkeypatch.setattr(socket.socket, "connect", _deny_network)

    first = run_suite(work_dir=tmp_path / "first")
    second = run_suite(work_dir=tmp_path / "second")

    assert first == second
    assert set(SCENARIO_IDS) == EXPECTED_SCENARIOS
    assert first["summary"] == {"failed": 0, "passed": 14, "status": "passed", "total": 14}
    assert {item["id"] for item in first["scenarios"]} == EXPECTED_SCENARIOS
    assert all(item["status"] == "passed" for item in first["scenarios"])
    assert all(all(item["checks"].values()) for item in first["scenarios"])
    assert not tuple(tmp_path.rglob("*.json")), "run_suite must not write report artifacts"
    assert first["execution"]["adapter"] == (
        "feed_passport_lab plus ten local platform control twins"
    )
    assert first["execution"]["adapters"] == [
        "feed_passport_lab",
        "twin:bluesky",
        "twin:facebook",
        "twin:instagram",
        "twin:linkedin",
        "twin:reddit",
        "twin:snapchat",
        "twin:threads",
        "twin:tiktok",
        "twin:x",
        "twin:youtube",
    ]
    assert first["execution"]["mission_adapter"] == "twin:x"

    scenarios = {item["id"]: item for item in first["scenarios"]}
    migration = scenarios["migration_quality"]["metrics"]
    assert migration["after_topic_distance"] < migration["before_topic_distance"]
    assert migration["improvement"] > 0

    safety = scenarios["policy_safety"]["metrics"]
    assert set(safety["denied_public_actions"]) == {"comment", "like", "post", "repost", "send_message"}
    assert safety["allowed_control_action"] == "follow_creator"

    assert scenarios["rollback_fidelity"]["metrics"]["state_restored_exactly"] is True
    assert scenarios["temporary_overlay_isolation_expiry"]["metrics"]["base_passport_unchanged"] is True
    assert scenarios["temporary_visa_early_revoke"]["metrics"]["final_status"] == "revoked"
    assert scenarios["consent_scoped_companion_blending"]["metrics"]["private_fields_leaked"] == []
    assert scenarios["idempotency"]["metrics"]["executions_recorded"] == 1
    assert scenarios["capability_honesty"]["metrics"]["declared_level"] == "lab"
    twin_matrix = scenarios["local_platform_twin_matrix"]["metrics"]
    assert twin_matrix["platform_count"] == 10
    assert twin_matrix["external_accounts_used"] == 0
    assert all(
        item["state_restored_exactly"] for item in twin_matrix["twins"].values()
    )
    mission = scenarios["bounded_agent_mission_lifecycle"]["metrics"]
    assert mission["platform"] == "twin:x"
    assert mission["submitted_actions"] <= 2
    assert mission["receipt_count"] == 2
    assert mission["state_restored_exactly"] is True
    assert mission["external_accounts_used"] == 0
    assert scenarios["drift_detection"]["metrics"]["drift_status"] == "decision_required"
    assert scenarios["scheduled_drift_monitor"]["metrics"]["status_after_stop"] == "stopped"
    portability = scenarios["passport_portability_and_checkpoints"]
    assert portability["metrics"]["source_identifier_reused"] is False
    assert portability["metrics"]["source_owner_reused"] is False
    assert portability["metrics"]["portable_intent_preserved"] is True
    assert portability["metrics"]["export_schema_version"] == "1.0.0"
    assert portability["checks"]["public_export_hides_local_identity"] is True
    assert scenarios["creator_continuity"]["metrics"]["unknown_requires_human_confirmation"] is True


def test_cli_writes_only_the_explicit_report_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(socket, "create_connection", _deny_network)
    monkeypatch.setattr(socket.socket, "connect", _deny_network)
    output = tmp_path / "artifacts" / "evaluations" / "report.json"

    exit_code = main(["--output", str(output), "--work-dir", str(tmp_path / "work")])

    assert exit_code == 0
    assert output.is_file()
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["status"] == "passed"
    assert tuple(tmp_path.rglob("*.json")) == (output,)
    assert DEFAULT_REPORT_PATH.parent.name == "evaluations"
    assert DEFAULT_REPORT_PATH.parent.parent.name == "artifacts"
