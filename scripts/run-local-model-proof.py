from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from feed_passport.domain import AgentMissionAcceptance, AgentMissionBudget
from feed_passport.runtime import build_service_bundle


MODEL_REVISION = "90862c4b9d2787eaed51d12237eafdfe7c5f6077"
MODEL_SHA256 = "061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a"
MODEL_LICENSE = "Apache-2.0"
RUNTIME_RELEASE = "b8184"
RUNTIME_COMMIT = "319146247e643695f94a558e8ae686277dd4f8da"
RUNTIME_SHA256 = "94254e58f4f73cdf978dcfaa04007eabc38337c839bc674b54161b5ba3cffd3b"
RUNTIME_ARCHIVE_SHA256 = "2d60828f4b90bdd1e93698837c163b54f40e7d682e8018dc40f52eb444c3cceb"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def build_proof(model_path: Path, server_path: Path) -> dict[str, Any]:
    if os.getenv("FEED_PASSPORT_MODEL_PROVIDER") != "llamacpp":
        raise RuntimeError("FEED_PASSPORT_MODEL_PROVIDER=llamacpp is required")
    model_hash = file_sha256(model_path)
    if model_hash != MODEL_SHA256:
        raise RuntimeError(
            f"local model hash mismatch: expected {MODEL_SHA256}, received {model_hash}"
        )
    server_hash = file_sha256(server_path)
    if server_hash != RUNTIME_SHA256:
        raise RuntimeError(
            f"llama.cpp runtime hash mismatch: expected {RUNTIME_SHA256}, received {server_hash}"
        )
    with tempfile.TemporaryDirectory(prefix="feed-passport-local-model-proof-") as directory:
        bundle = build_service_bundle(
            database_path=Path(directory) / "proof.db",
            consent_secret="isolated-local-model-proof-secret",
            seed_demo=True,
        )
        try:
            if bundle.mission_planner is None:
                raise RuntimeError("the explicit local model planner was not composed")
            status = await bundle.model_provider.status(probe=True)
            if not status["online"]:
                raise RuntimeError(status["reason"])
            passport = bundle.application.list_passports()[0]
            mission = await bundle.mission_planner.preview(
                actor_id=passport.owner_id,
                goal=(
                    "Make this fresh account feel like my useful internet, prioritize research and "
                    "the creators I deliberately chose, reduce ragebait, and stop at the locked target."
                ),
                passport_id=passport.id,
                destination_twin="twin:youtube",
                destination_account_id="destination-new",
                budget=AgentMissionBudget(
                    total_actions=6,
                    per_iteration_actions=3,
                    max_iterations=3,
                ),
                acceptance=AgentMissionAcceptance(
                    max_total_variation_distance=0.18,
                    max_unwanted_rate=0.05,
                    max_source_concentration=0.45,
                    min_serendipity_rate=0.12,
                    max_serendipity_rate=0.28,
                ),
                min_improvement=0.02,
            )
            evidence = mission["planner_evidence"]
            expected_tools = [
                "inspect_selected_passport",
                "inspect_selected_control_surface",
                "submit_mission_proposal",
            ]
            assertions = {
                "genuine_strands_model_cycles": evidence["cycles"] == 3,
                "required_tool_sequence": [item["name"] for item in evidence["tools"]]
                == expected_tools,
                "deterministic_validation_passed": evidence["deterministic_validation"] == "passed",
                "human_consent_still_required": mission["status"] == "awaiting_approval"
                and mission["approved_by"] is None,
                "loopback_only": evidence["endpoint_scope"] == "loopback_only",
                "zero_paid_or_external_model_calls": not evidence["external_model_calls"]
                and not evidence["paid_model_calls"],
                "model_cannot_widen_action_scope": set(evidence["admitted_action_types"])
                <= set(evidence["requested_action_types"]),
                "no_approval_token_in_evidence": "approval_token"
                not in json.dumps(evidence).lower(),
                "model_hash_matches_pinned_artifact": model_hash == MODEL_SHA256,
                "runtime_hash_matches_pinned_artifact": server_hash == RUNTIME_SHA256,
            }
            if not all(assertions.values()):
                raise RuntimeError(f"local model proof assertions failed: {assertions}")
            return {
                "schema": "feed-passport/local-model-agent-proof/v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "mode": "genuine_local_model_inference",
                "external_accounts": 0,
                "external_services_during_inference": 0,
                "network_scope": "loopback_only",
                "model": {
                    "publisher": "Qwen",
                    "repository": "Qwen/Qwen3-1.7B-GGUF",
                    "revision": MODEL_REVISION,
                    "file": model_path.name,
                    "sha256": model_hash,
                    "expected_sha256": MODEL_SHA256,
                    "license": MODEL_LICENSE,
                },
                "runtime": {
                    "name": "llama.cpp",
                    "file": server_path.name,
                    "sha256": server_hash,
                    "release": RUNTIME_RELEASE,
                    "commit": RUNTIME_COMMIT,
                    "archive_sha256": RUNTIME_ARCHIVE_SHA256,
                    "offline_flag": True,
                    "listen_host": "127.0.0.1",
                },
                "mission": {
                    "id": mission["id"],
                    "status": mission["status"],
                    "goal_interpretation": mission["goal_interpretation"],
                    "allowed_action_types": mission["allowed_action_types"],
                    "action_count": len(mission["action_envelope"]),
                    "consent_required": mission["status"] == "awaiting_approval",
                },
                "planner_evidence": evidence,
                "assertions": assertions,
            }
        finally:
            bundle.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Record a genuine local Strands planning proof.")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--server", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    proof = asyncio.run(build_proof(args.model.resolve(), args.server.resolve()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "model": proof["model"]["repository"],
                "tool_sequence": [
                    item["name"] for item in proof["planner_evidence"]["tools"]
                ],
                "assertions_passed": all(proof["assertions"].values()),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
