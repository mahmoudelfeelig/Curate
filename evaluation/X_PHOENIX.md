# X Phoenix provenance boundaries

The public Phoenix layout changed, so this repository keeps two separate, fail-closed bridges instead of pretending one contract covers both generations.

`evaluation/x_phoenix_bridge.py` is the legacy provenance harness. It expects the former `phoenix/run_pipeline.py` plus separately supplied model configuration, sequence, and corpus inputs. When that historical layout is supplied, it verifies the official repository remote and records checkout, source, and input hashes before invocation. Its `offline_public_model_replay` label describes provenance only and never means that a live For You feed was read or changed.

Historical-layout example from the repository root, for provenance research only:

```python
from evaluation.x_phoenix_bridge import run_replay, write_receipt

report = run_replay(
    checkout=r"D:\external\x-algorithm",
    artifacts_dir=r"D:\external\x-algorithm\phoenix\artifacts\oss-phoenix-artifacts",
)
write_receipt(report, "artifacts/evaluations/x-phoenix-replay.json")
```

`evaluation/current_x_phoenix_bridge.py` is the new current-layout contract. On August 31, 2026, read-only inspection bound it to the official `xai-org/x-algorithm` checkout at commit `bc8e5f0f07b31337bfdcaf690121498e00199b64` and tree `1f608534a68a8041ea42d278cc23545e20a6e100`. The bridge:

- rejects an unofficial remote, a non-full or unexpected commit, modified/missing tracked sources, symlinks, and unsafe paths;
- hashes every tracked Phoenix manifest/reference source used by the contract;
- allows only `world.py`, `oss_recsys_synth.py`, `world_snapshots.py`, `dump_gen.py`, and `gen_recs_artifacts_gen.py`;
- excludes training, checkpointing, retrieval, ranking, and serving programs;
- stores hashes and byte counts rather than raw stdout or stderr; and
- requires every receipt to say `ranking_executed: false` and `live_feed_changed: false`.

The current upstream source documents deterministic synthetic worlds, provider snapshots, a readable single-device training path, and retrieve-then-rank serving. Those are useful research materials, but they are not production inputs, a production checkpoint, production scale, or evidence that Feed Passport controls X.

No current-layout generator execution receipt is checked in. The official source was inspected and the bridge contract is tested, but third-party checkout code was not executed. Therefore the truthful evidence label is **pinned current source inspection**, not “Phoenix replay” or “ranking Lab.” The deterministic local platform twins remain the executable account-free evidence; they are control simulators, not Phoenix or production-ranker replicas.
