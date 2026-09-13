# Local Strands agent

Feed Passport can run genuine model inference without an AWS account, hosted API, paid model service, social account, or competition credit. The supported provider is a verified Qwen model served by a pinned llama.cpp release on loopback. The model is a planning clerk only; deterministic application code remains the authority for identity, resources, budgets, thresholds, consent, execution, and rollback.

The deterministic mission preview remains usable when the model is disabled. Fixture mode never fabricates model evidence.

## Pinned local artifacts

The acquisition and proof scripts contain the authoritative pins. Models, runtime binaries, archives, databases, and raw proof runs live under `artifacts/` and are ignored by git.

| Artifact | Pinned value |
| --- | --- |
| Model repository | `Qwen/Qwen3-1.7B-GGUF` |
| Model file | `Qwen3-1.7B-Q8_0.gguf` |
| Model revision | `90862c4b9d2787eaed51d12237eafdfe7c5f6077` |
| Model size | `1,834,426,016` bytes |
| Model SHA-256 | `061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a` |
| Model license | Apache-2.0 |
| llama.cpp release | `b8184` |
| llama.cpp commit | `319146247e643695f94a558e8ae686277dd4f8da` |
| Runtime archive | `llama-b8184-bin-win-vulkan-x64.zip` |
| Runtime archive SHA-256 | `2d60828f4b90bdd1e93698837c163b54f40e7d682e8018dc40f52eb444c3cceb` |
| `llama-server.exe` SHA-256 | `94254e58f4f73cdf978dcfaa04007eabc38337c839bc674b54161b5ba3cffd3b` |
| `ggml-vulkan.dll` SHA-256 | `626e3b50d37104169a900fd1d8a6286c1278cb0b535592d1962c8714dab20f60` |
| `llama.dll` SHA-256 | `847db2ce70ea3a63e45d94230e4a83c38bc688e32d4287f69a3c0155fcff2c57` |

The model comes from the pinned [Qwen publisher repository](https://huggingface.co/Qwen/Qwen3-1.7B-GGUF). The runtime comes from the pinned [official llama.cpp b8184 release](https://github.com/ggml-org/llama.cpp/releases/tag/b8184). Do not replace either pin with “latest” and continue to call the result the same proof; the model/tool protocol is version-bound.

## Prepare the Python service

From the repository root in PowerShell:

```powershell
py -3.13 -m venv services\curator\.venv
.\services\curator\.venv\Scripts\python.exe -m pip install --upgrade pip==26.0.1
$env:PIP_CONSTRAINT = (Resolve-Path .\services\curator\constraints-py313-win.txt).Path
$env:PIP_BUILD_CONSTRAINT = $env:PIP_CONSTRAINT
.\services\curator\.venv\Scripts\python.exe -m pip install -e ".\services\curator[dev]"
Remove-Item Env:PIP_CONSTRAINT, Env:PIP_BUILD_CONSTRAINT
```

`constraints-py313-win.txt` freezes the exact Windows x64/CPython 3.13 dependency graph used by the committed proof. `PIP_CONSTRAINT` fixes runtime resolution and `PIP_BUILD_CONSTRAINT` fixes pip's isolated build environment, where `pyproject.toml` pins setuptools `84.0.0`. Regenerate a platform-specific lock before claiming reproducibility on a non-Windows host.

The model and runtime acquisition commands fetch their public artifacts directly from pinned publisher releases. They enforce the declared model size and artifact hashes before accepting the downloads.

```powershell
.\scripts\acquire-local-model.ps1
.\scripts\acquire-llama-runtime.ps1
```

The expected installed files are:

```text
artifacts/local/models/Qwen3-1.7B-Q8_0.gguf
artifacts/local/runtime/b8184/llama-server.exe
artifacts/local/runtime/b8184/ggml-vulkan.dll
artifacts/local/runtime/b8184/llama.dll
```

Inference itself stays offline and loopback-only. No hosted provider or competition credit is used.

## Start the verified inference server

Keep this PowerShell window open:

```powershell
.\scripts\start-local-model.ps1
```

The launcher rechecks the model and server hashes before starting. It binds only to `127.0.0.1:8080`, passes llama.cpp's offline flag, uses one parallel slot, disables the web UI and slots endpoint, disables multimodal loading, and disables Qwen thinking output for the tool protocol.

An initial runtime probe is available at `http://127.0.0.1:8080/health`.

## Start the Curator API with the local provider

Set the provider variables in the same PowerShell window that will start the API. They are read while the service bundle is composed.

```powershell
$env:FEED_PASSPORT_MODEL_PROVIDER="llamacpp"
$env:FEED_PASSPORT_LLAMACPP_BASE_URL="http://127.0.0.1:8080"
$env:FEED_PASSPORT_LLAMACPP_MODEL_ID="feed-passport-local-qwen3-1.7b"
$env:FEED_PASSPORT_LOCAL_MODEL_TIMEOUT_SECONDS="120"
.\services\curator\.venv\Scripts\feed-passport-api.exe --reload
```

Check the service boundary:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/agent/model/status | ConvertTo-Json -Depth 5
```

A ready response has these invariants:

```json
{
  "configured": true,
  "provider": "llamacpp",
  "endpoint_scope": "loopback_only",
  "mode": "local_only",
  "online": true,
  "readiness": "ready",
  "external_model_calls": false,
  "paid_model_calls": false
}
```

Only `disabled` and `llamacpp` are accepted provider values. A llama.cpp URL must use plain HTTP, have no credentials, path, query, or fragment, and resolve to `127.0.0.1`, `localhost`, or `::1`. The HTTP client ignores proxy environment variables, so a local request cannot silently route through an external proxy.

## Use the browser

Start the frontend in another PowerShell window:

```powershell
$env:VITE_CURATOR_API_URL="http://127.0.0.1:8000"
npm ci
npm run dev
```

Open the **Feature clerk** or **Agent mission** desk. The readiness card must say **LOCAL MODEL READY** before a genuine model action becomes available. Feature Clerk turns one natural-language outcome into a typed migration, Temporary Visa, or Companion proposal and shows its sanitized tool trace, token use, latency, proposal-only authority, deterministic text source, and bound capability. Applying it only pre-fills another desk. Agent mission narrows one local twin's reversible control families and still stops before consent.

A successful mission plan displays:

- the configured model identifier and provider;
- an explicit user-facing rationale rather than hidden reasoning;
- the sanitized three-call tool trace;
- input, output, and total token usage;
- planning latency;
- requested, admitted, and rejected action families;
- `proposal_only` authority and deterministic validation;
- a consent checkpoint with execution still disabled.

**PREVIEW WITHOUT MODEL** uses the deterministic planner and does not show fabricated model evidence. If the frontend cannot complete its initial service probe, it switches to visibly labeled fixtures. It never converts an interrupted mutation into fixture success.

## Feature Clerk proposal-only protocol

Every Feature Clerk request creates a fresh `strands.Agent`. It must inspect the selected Passport, inspect the safe feature catalogue, and call exactly one matching submission tool:

```text
inspect_selected_passport
inspect_safe_feature_catalog
submit_migration_proposal | submit_temporary_visa_proposal | submit_companion_sync_proposal
```

Submission tools deliberately contain no free-text fields. Migration accepts only a catalogue destination. Temporary Visa accepts only duration and mode. Companion accepts only selected field categories, strategy, input percentage, and duration. Deterministic server templates render goal, purpose, and rationale after validation; a migration embeds the exact selected capability record. This prevents partner names, handles, consent or approval claims, automatic-execution claims, and ranking-fidelity claims from becoming user-facing model output.

The endpoint snapshots every mutable SQLite table before and after planning and has no mutation tool. Applying its browser result only pre-fills the corresponding deterministic desk; it does not preview, create consent, approve, execute, or roll back.

## Mission proposal-only protocol

Every model plan creates a fresh, request-bound `strands.Agent`. It must call these tools once each and in this exact order:

```text
inspect_selected_passport
inspect_selected_control_surface
submit_mission_proposal
```

The first inspection returns portable preference intent, the human's untrusted outcome text, and no owner identifier, credentials, or raw history. The second returns the already selected local twin's available local control families and server-locked budgets and thresholds, but no destination account identifier. The final tool accepts only:

- a refined goal;
- an explicit concise rationale;
- a subset of listed action families;
- an ordered evaluation focus;
- capability notes;
- stop conditions.

The agent does not receive approval, execution, cancellation, rollback, credential, identity, account-selection, budget-editing, or threshold-editing tools. Unexpected tools, fields, duplicate calls, an incorrect order, an out-of-scope action family, a timeout, or a missing proposal fails the planning request.

The provider uses temperature `0`, seed `42`, a maximum of `600` tokens per model response, prompt caching, and `parallel_tool_calls: false`. The Strands invocation is bounded to three turns, `1,800` output tokens, `16,000` total tokens, and the configured timeout. The timeout must be between 1 and 300 seconds.

Browser calls that invoke this loopback model allow up to five minutes for the complete bounded tool loop. This is a client deadline, not additional model authority: the planner retains the same tool, turn, token, action, and consent limits. The longer deadline accommodates CPU-only inference and a valid final retry turn without converting a healthy in-progress proposal into an unknown-outcome browser timeout.

After the proposal passes its schema and sequence checks, deterministic code intersects its requested action families with the selected twin's available reversible families. The server, not the model, locks:

- actor and Passport ownership;
- Passport identifier and version;
- destination twin and seeded account scenario;
- total, per-pass, and iteration budgets;
- acceptance thresholds and minimum improvement;
- consent, execution, and rollback.

The resulting mission remains `awaiting_approval`. A separate one-time approval is required for execution, and another approval is required for rollback. Planner evidence is display evidence only and remains unchanged through execution and rollback.

## Internal live-commission priority protocol

The separate one-shot YouTube/Bluesky commission planner is source- and test-level infrastructure, not a user-callable chat surface. It accepts only the explicitly local model configuration; any execution profile that permits external model calls is rejected before model or commission state is created.

Its model context contains only privacy-reduced Passport-derived demand buckets, the names and counts of every certified action family in an already compiled plan, and the locked action budget. Exact targets remain in deterministic application code. The only accepted model result is a unique complete permutation of all supplied families. There is no model field for a goal, rationale, target, approval, authority claim, executable action, or revised budget.

Deterministic code uses the permutation to order the withheld exact actions, truncates that sequence under the fixed budget, and seals the resulting plan. The later commission boundary revalidates the owner connection, Passport version, effective-policy fingerprint, compiled plan, certification, and one-time exact-plan consent. Generic migration authority cannot execute a live adapter. The Connected Agent desk and owner-authenticated API expose commission preview, approval, execution, reconciliation, cancellation, and rollback, but no live account has yet been validated and execution remains certification-gated.

## API behavior

The supported routes are:

| Route | Behavior |
| --- | --- |
| `GET /api/agent/model/status` | Probes only the configured loopback server and reports readiness. |
| `POST /api/agent/features/plan` | Classifies one untrusted request into a typed, server-rendered, proposal-only top-level feature configuration. |
| `POST /api/agent/feed-evidence/analyze` | Records a bounded owner-selected before/after sample, deterministic inference, and capability translation without claiming FYP access. |
| `POST /api/agent/feed-evidence/{proposal_id}/model-plan` | Runs the exact three-tool local Strands feed-goal protocol over sanitized evidence only. |
| `POST /api/agent/feed-evidence/{proposal_id}/apply` | Applies a reviewed before-proposal to the Passport only; comparison records and changed Passport versions fail closed. |
| `POST /api/agent/missions/plan` | Canonical request-bound local-model planning route. |
| `POST /api/agent/missions/model-preview` | Compatibility alias for the same planning route. |
| `POST /api/agent/missions/preview` | Deterministic mission preview without model inference. |
| `POST /api/agent/command` | Validated deterministic command boundary. |
| `POST /api/agent/strands` | Always returns `503`; broad free-text model chat is disabled. |

When no local provider is configured, model planning returns `503` rather than selecting another provider. A proposal-protocol failure returns `502` with `local_model_protocol_failed`. The deterministic preview remains available in both cases.

The AgentCore Runtime entrypoint follows the same fail-closed authority boundary through three strict discriminated commands. `health` performs no model construction. `plan_feature` accepts one bounded natural-language feature request. `plan_feed` accepts a bounded feed goal plus sanitized evidence without URLs, account identifiers, credentials, consent, or exact social targets. Each planning operation returns only a validated proposal. The Gateway validates the Cognito JWT and scope, a request interceptor rechecks those claims and writes only its bounded `sub` into the allowlisted actor header, and the IAM-ingress Runtime rejects owner mismatches. The Runtime exposes no execution, approval, rollback, credential, browser-control, or live-platform mutation operation. Arbitrary chat payloads and unknown commands are rejected. No AgentCore deployment is claimed by this checkout until a managed receipt exists.

## Record the genuine model proofs

The Feature Clerk proof owns its inference process. No pre-existing server or provider environment variables are required: the script hash-verifies the supplied binary and model, launches a child on a reserved loopback port with `--offline`, probes that exact process, runs adversarial requests for all three feature kinds, fingerprints every mutable SQLite table before and after each request, records process/model/log evidence, and terminates only its own child.

```powershell
.\services\curator\.venv\Scripts\python.exe .\scripts\run-local-feature-planner-proof.py `
  --model .\artifacts\local\models\Qwen3-1.7B-Q8_0.gguf `
  --server .\artifacts\local\runtime\b8184\llama-server.exe `
  --output .\artifacts\evaluations\local-feature-planner-proof.json
```

The proof fails unless migration, Temporary Visa, and Companion each complete the exact typed protocol with positive model usage, all identity/authority/capability-overclaim fragments remain absent, migration capability equals the runtime manifest, every mutable table remains unchanged, and the owned process reports the pinned model before clean termination. Run it twice for independent cold-start receipts when recording repeatability evidence.

For the separate mission-planning proof, keep the verified llama.cpp server running with the four provider variables set, then run:

```powershell
.\services\curator\.venv\Scripts\python.exe .\scripts\run-local-model-proof.py `
  --model .\artifacts\local\models\Qwen3-1.7B-Q8_0.gguf `
  --server .\artifacts\local\runtime\b8184\llama-server.exe `
  --output .\artifacts\evaluations\local-model-agent-proof.json
```

The command uses a temporary SQLite database and fails unless all of these assertions hold:

- the model and runtime hashes match the pins above;
- Strands completes three genuine model cycles;
- the exact three-tool sequence completes;
- deterministic proposal validation passes;
- the mission still awaits human consent;
- the endpoint scope is loopback-only;
- paid and external model-call flags remain false;
- admitted action families cannot exceed the proposal;
- no approval token appears in persisted evidence.

The JSON records model/runtime provenance, tool trace, explicit proposal, duration, cycle count, token usage, server-locked fields, and the assertion map. It does not record hidden chain-of-thought.

## Exercise the complete browser lifecycle

The service browser harness expects isolated local ports and a disposable database. Keep the verified model on port `8080`, then start a dedicated API:

```powershell
$env:FEED_PASSPORT_MODEL_PROVIDER="llamacpp"
$env:FEED_PASSPORT_LLAMACPP_BASE_URL="http://127.0.0.1:8080"
$env:FEED_PASSPORT_LLAMACPP_MODEL_ID="feed-passport-local-qwen3-1.7b"
$env:FEED_PASSPORT_LOCAL_MODEL_TIMEOUT_SECONDS="120"
$env:FEED_PASSPORT_DB_PATH="$PWD\artifacts\local\browser-model-qa.db"
$env:FEED_PASSPORT_ALLOWED_ORIGINS="http://127.0.0.1:5175"
.\services\curator\.venv\Scripts\feed-passport-api.exe --host 127.0.0.1 --port 8002
```

Start a dedicated frontend in another window:

```powershell
$env:VITE_CURATOR_API_URL="http://127.0.0.1:8002"
npm run dev -- --host 127.0.0.1 --port 5175
```

`npm ci` installs `playwright-core` without downloading a browser binary. The harness uses an installed Chrome or Edge executable. It can still use the Codex-bundled runtime or an explicit `FEED_PASSPORT_PLAYWRIGHT_MODULE` as a final fallback.

Run the model-aware browser proof in a third window:

```powershell
$env:FEED_PASSPORT_BASE_URL="http://127.0.0.1:5175"
$env:FEED_PASSPORT_API_URL="http://127.0.0.1:8002"
$env:FEED_PASSPORT_EXPECT_LOCAL_MODEL="1"
node .\scripts\browser-service-qa.mjs --run=final-official-b8184-model
```

The harness fails if the browser falls back to fixtures, the model is not ready, token usage is zero, the tool order changes, policy admits an unrequested action, execution becomes available before consent, planner evidence changes during execution or rollback, the rollback fingerprint does not restore exactly, or the browser attempts an external request.

Stop the dedicated processes before reusing or deleting the disposable database. Do not point this harness at a local database whose state you want to keep.

## Evidence handling

Raw model weights, runtime binaries, archives, SQLite files, evaluation output, and browser captures under `artifacts/` are ignored. Do not commit the 1.8 GB model or runtime bundle. Copy only small, final, source-bound proof into `artifacts/evidence/`, such as:

- the genuine model proof JSON;
- the deterministic evaluation JSON;
- the final model browser report;
- a small set of legible screenshots;
- a manifest containing the canonical tracked-source digest, canonical Git-blob artifact hashes, commands, the exact history count for that source revision, and declared local-only boundaries. The archived public baseline manifest records 40 commits at `d6727618fbe9339d1c95281806498002096dbdbd`; append-only integration commits require a newly generated manifest rather than editing that historical proof by hand.

Neither a local twin run nor a local model proof is evidence of a live social-platform account, ranking-system fidelity, AgentCore deployment, or external-platform conformance.

## Failure guide

`readiness: "disabled"`
: Set `FEED_PASSPORT_MODEL_PROVIDER=llamacpp` in the API process and restart that process.

`readiness: "offline"`
: Confirm `scripts/start-local-model.ps1` is still running on the configured port and that its `/health` endpoint responds.

Hash mismatch
: Treat the artifact as unverified. Do not bypass the check. Inspect or remove the mismatching ignored download, then use the pinned acquisition script again.

Planning returns `502`
: The model timed out or violated the exact proposal protocol. Retain the error as a failed proof; do not silently substitute deterministic output while claiming model inference.

Planning returns `503`
: The provider is disabled, the broad `/strands` route was called, or the local planner was not composed. Use deterministic preview or correctly configure the verified loopback provider.

Browser says **LOCAL MODEL NOT READY**
: Check the API's model-status route first. The browser intentionally disables **ASK LOCAL MODEL TO PLAN** until readiness is proven.
