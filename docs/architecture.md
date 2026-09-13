# Feed Passport architecture

## Structure

```text
React client and WebMCP surface
        |
        | JSON over HTTP
        v
FastAPI application boundary
        |
        +--> application services --> pure domain policy
        |           |                       |
        |           |                       +--> Passport, overlay, blend, envelope,
        |           |                            receipt, evaluation, checkpoint
        |           |
        |           +--> persisted mission runner --> observe / evaluate / plan /
        |           |                              act / re-observe / adapt / stop
        |           +--> request-bound Strands planners --> bounded proposal-only tools
        |           +--> guided handoff --> immutable native steps / user attestation
        |           +--> Instagram import --> ephemeral normalized following subset
        |           +--> independent evaluator
        |
        +--> platform port --> deterministic Lab / 10 twin:<platform> simulators /
        |                    10 guided planners / 4 certification-gated transports
        |                                      ^
        |                                      +-- internal YouTube/Bluesky commission
        |
        +--> OIDC owner boundary --> encrypted connection metadata / credential broker
        +--> SQLite event, projection, action-journal, and scheduled-job store
        +--> FastAPI lifespan --> autonomous DueJobRunner --> application due jobs
        +--> HMAC one-time consent broker
        +--> proposal-only Strands AgentCore Runtime seam
```

## Module boundaries

`domain`
: Pure models and deterministic functions. It owns validation, composition normalization, overlays, consent state, blending, action policy, budgets, expiry, evaluation thresholds, idempotency, rollback invariants, and the owner-bound Guided handoff lifecycle.

`application`
: Use cases and transactions. It coordinates repositories, adapters, clocks, the persisted mission runner, planners, evaluator, ephemeral Instagram import sessions, and Guided handoffs without importing concrete infrastructure.

`agent`
: Two user-callable request-bound Strands protocols plus one internal live-commission protocol, all with separate typed outputs. The feature clerk proposes one migration, temporary-visa, or companion configuration from a server-derived safe catalogue. The local-twin mission planner proposes a subset of reversible private controls. Each creates a fresh agent and enforces its bounded tool sequence; neither can authorize, create partner consent, execute, approve, or roll back. The internal commission planner is local-only and sees privacy-reduced Passport-derived demand buckets plus certified action-family names/counts for one already compiled YouTube or Bluesky migration. Its entire output is a complete permutation of those families; it never receives or authors exact targets, identity, credentials, goals, rationale, or authority, and it has no public API route.

`ports`
: Stable platform, identity, connection, credential, OAuth, live-transport, and durable action-journal boundaries. Credentials are non-serializable leases and never enter model context.

`adapters`
: Concrete platform implementations. Each publishes a capability manifest and passes the same conformance suite. Feed Passport Lab is the reference closed loop. Ten `twin:<platform>` adapters also close the control loop over isolated deterministic state while limiting actions to each profile's declared control semantics and explicitly denying ranking fidelity. Every unprefixed external adapter remains Guided by default. Bounded live candidates for YouTube, X, Reddit, and Bluesky can load only with an owner-bound connection and a fresh signed exact-revision authorized-dummy-account receipt; generic migration authority still cannot execute them. Runtime construction injects one declared deterministic dummy snapshot per external platform for capture tests; arbitrary account identifiers remain unobserved.

`infrastructure`
: SQLite event/projection/scheduled-job/action-journal and Guided-handoff storage, encrypted connection metadata with blind indexes, a local AES-GCM OAuth credential vault, strict serialization helpers, and no-ambient-network HTTP boundaries. AT Protocol OAuth tokens and DPoP keys remain in the separate Node sidecar. Instagram import previews are deliberately not persisted: normalized handles live only in an owner-bound 15-minute in-memory session, and raw uploads are never retained.

`api`
: FastAPI routes with strict Pydantic request models. API models are mapped from domain objects rather than becoming the domain. Local `demo` mode accepts `actor_id` as a deterministic test principal. Credentialed/live surfaces normally require `oidc`, which verifies an exact issuer, audience/client ID, JWKS signature, scope, and expiry, derives the owner from `sub`, and rejects conflicting caller-supplied identity fields. An explicit insecure one-person dummy-account override exists only on a loopback bind with loopback-only origins and client IPs; it is never a proxy, deployment, LAN, team, or production authentication boundary. Instagram upload routes additionally require `FEED_PASSPORT_ENABLE_LOCAL_IMPORT=1` and a loopback client. Guided-handoff and live-commission inspection, consent, recovery, and rollback routes are owner-bound.

`runtime`
: Service bootstrap, certification-gated adapter restoration, the optional proposal-only AgentCore entrypoint, live conformance runner, and `DueJobRunner`. The app-owned FastAPI lifespan starts and stops the runner; it does not authorize a social account without a user-completed OAuth transaction.

## Stable core contract

Every platform implements:

```text
capabilities(account) -> PlatformCapabilityManifest
observe(account, scope) -> AccountObservation
compile(passport, observation) -> TranslationPlan
execute(account, approved_action) -> ActionOutcome
sample(account, sample_spec) -> FeedSample
rollback(account, receipt) -> RollbackOutcome
health() -> AdapterHealth
```

Every unsupported action produces a typed capability failure. Guided external adapters never report executed success. A `twin:<platform>` adapter may report success only for an explicitly labeled deterministic local mutation, never as external-platform success.

A Guided migration creates a sealed handoff from the plan's exact native actions. The lifecycle is `previewed -> consented -> awaiting_handoff -> user_resolved -> finalized`. The user resolves every step as `completed_by_user`, `skipped_by_user`, or `control_not_found`; repeated identical resolution is idempotent and conflicting resolution fails. Finalization alone issues a receipt, whose outcomes are only `GUIDED` or `SKIPPED` and whose summary fixes API writes and verified recommendation outcomes at zero with `platform_verified=false`.

Destination cards marked “Selected” or “Included” are local demo state. They are not part of this adapter contract and do not mean OAuth, an authenticated social account, or live conformance exists. The Authorization Desk reports connection state separately, and a connection still does not promote an adapter without a signed conformance receipt.

## Closed-loop Lab migration

The following sequence is implemented against deterministic Feed Passport Lab. An unconfigured external adapter stops after capability-aware compilation with a Guided handoff or translation loss. Even when a certified live transport is loaded, generic migration authority rejects its execution. Only the separately bound owner-authenticated live-commission service and routes can reach the transport. No live account receipt exists until a fresh authorized-dummy-account conformance run is completed.

```text
observe source
  -> infer or edit Passport
  -> observe destination
  -> compile translation plan
  -> deterministic policy validation
  -> human approval envelope
  -> idempotent execution
  -> sample destination
  -> independent evaluation
  -> adapt within remaining budget or stop
  -> issue receipt and checkpoint
```

The general feed evaluator records `target_reached`, `budget_exhausted`, `capability_unavailable`, `low_confidence`, `permission_required`, `adapter_failure`, `cancelled`, `human_judgment`, or `continue`. The persisted local-twin mission runner has its own terminal reasons: `acceptance_reached`, `budget_exhausted`, `max_iterations_reached`, `minimum_improvement_not_met`, `no_actionable_plan`, `policy_blocked`, `capability_unavailable`, `adapter_failure`, or `cancelled`. These are separate from a model-runtime stop reason.

## Persisted account-free agent mission

The primary autonomy proof runs only against a registered `twin:<platform>` adapter. Preview observes a seeded local scenario, evaluates it against the active Passport and acceptance thresholds, compiles an exact initial reversible plan, records the approved action families plus hard total/per-pass/iteration budgets, and stops for consent without mutating state. A one-time token bound to the local demo principal and complete preview scope allows the runner to execute a pass. Immediately before mutation, it persists only a namespaced SHA-256 fingerprint of the deterministic local control state. It then re-observes, measures, and may recompile targets only inside the same Passport version, approved action families, and remaining budgets before stopping with a reason and receipts. Rollback requires a different one-time token, applies receipts in reverse order, recomputes the local control-state fingerprint with a constant-time digest comparison, and records re-observation as separate behavioral evidence. A mismatch returns `rollback_partial`. No raw twin state is stored in the fingerprint proof. These owner and token checks are deterministic local boundaries, not production authentication.

The canonical model-assisted planning route creates a fresh Strands agent for that request. Its entire tool surface is `inspect_selected_passport`, `inspect_selected_control_surface`, and `submit_mission_proposal`, and the server requires that exact sequence exactly once. The first inspection returns preference intent without owner identity, credentials, or raw history. The second returns the server-selected twin, available reversible control families, and locked budgets and thresholds. The third can submit only a refined goal, a concise rationale, a subset of those control families, focus order, capability notes, and stop conditions. There are no approval, execution, cancellation, credential, account-selection, or rollback tools in model context.

The Feed evidence desk has its own optional three-tool Strands protocol: `inspect_selected_passport`, `inspect_sanitized_evidence`, and `submit_feed_goal_proposal`. The evidence service first records 1–12 owner-selected public links, canonicalizes stored URLs to remove query and fragment tracking data, and labels provider metadata, owner notes, and deterministic keyword inference separately. Only bounded public title/description text with URLs removed and deterministic topic/ragebait/confidence labels enter model context; source URLs, owner notes, author/account identifier fields, credentials, and private history do not. Public text remains untrusted and may itself mention people. The agent may propose normalized topic weights, allowlisted exclusions, and a rationale. Deterministic code locks explicit percentages, preserves stricter server-derived guardrails, requires an unchanged Passport version, and leaves application behind a separate local consent action. An after-sample is comparison-only and cannot be applied.

After the proposal is accepted, the deterministic mission runner recompiles and intersects the proposal with the selected Passport, twin capabilities, any caller allowlist, fixed budgets, and acceptance thresholds. The runner owns identity, destination, scenario, consent, execution, evaluation, receipts, and rollback. Planner evidence is display-only and does not enter the approval scope. A model error, timeout, extra tool, reordered tool, duplicate submission, unexpected field, unsupported action family, non-twin destination, or non-loopback provider fails closed before mutation.

## Top-level feature proposal clerk

`POST /api/agent/features/plan` accepts only a local test principal, an already selected Passport ID, and untrusted request prose. The server resolves ownership before model invocation and derives a non-identifying catalogue from registered non-twin destination manifests plus the product's bounded temporary and companion choices. The model must call `inspect_selected_passport`, `inspect_safe_feature_catalog`, and exactly one matching typed submission tool: `submit_migration_proposal`, `submit_temporary_visa_proposal`, or `submit_companion_sync_proposal`.

The model-authored tool payload contains categorical and numeric choices only. Migration may select one catalogued destination. Temporary Visa may select a bounded duration and isolated or reversible-Lab mode. Companion may select field categories, strategy, 10–50 percent companion input, and a bounded expiry. Model-authored prose never crosses the API boundary: deterministic server templates create the displayed goal, purpose, and rationale, and migration embeds the exact server-selected `{destination_id, evidence_level, execute_mode, limitations}` capability record. The agent therefore cannot name a partner, invent capability or ranking-fidelity claims, create a slice or overlay, approve, execute, or roll back. The endpoint creates no event or projection; accepting a proposal in the browser only pre-fills the corresponding deterministic desk.

## Internal certified live commission boundary

The internal `LiveCommissionService` accepts only YouTube or Bluesky and first requires an active owner-bound connection plus a fresh exact-revision signed live certification. The request is bound to one Passport version, effective-policy fingerprint, destination connection version, compiled migration fingerprint, total-action budget, and the certification's exact observe/execute/verify/rollback subset. Public-engagement actions are rejected. A fresh certification is mandatory for every plan or mutation. An expired but otherwise valid exact-revision receipt can be restored only as an observation-only adapter for reconciliation of a write that was durably journaled before the crash.

The local-only model receives privacy-reduced Passport-derived demand buckets and the names/counts of every certified action family present in the compiled plan, together with the locked budget. It must return only a unique complete permutation of those families. The schema has no model-authored goal, rationale, target, authority, approval, or executable-action field, and configurations that allow external model calls are rejected before model construction. Deterministic code applies that ordering to the withheld exact actions, truncates under the locked budget, and seals the exact target sequence. Execution revalidates every binding and marks the commission stale if the Passport, plan, connection, or certification changed; generic migration authority cannot use a live adapter. Reconciliation and rollback reuse the existing migration journal and receipt mechanisms. Crash recovery never blindly replays an expired or outcome-unknown forward or rollback lease: it observes when allowed, otherwise terminalizes the record for human review. Active journal leases cover the whole migration, and receipt creation is deterministic and create-only so competing reconcilers cannot issue two receipts. The owner-bound API and Connected Agent desk expose preview, approval, one-shot execute, reconcile, cancel, and receipt rollback only in service mode, with no fixture fallback. No live account or provider result is claimed until a fresh authorized-dummy-account conformance run passes.

## Instagram portability intake

The opt-in local parser accepts a direct Instagram `following.json` or a ZIP with exactly one recognized `connections/followers_and_following/following.json`. It validates archive paths, entry types, compression, sizes, JSON depth, duplicate keys, handle/URL agreement, and bounded record counts before returning normalized followed handles. All other export members and preference fields are ignored and reported as unobserved rather than inferred.

The API streams at most 64 MiB from a loopback client into the parser, stages only the normalized preview in an owner-bound in-memory session, and advertises that the raw source is not retained and no platform account was accessed. The upload is a user-supplied Accounts Center-format file, not a provider-authenticated export. Apply requires the same Passport version and a unique user-selected subset within the 500-creator capacity, adds those handles at positive creator intent, and records parser provenance plus a digest of the normalized selected subset rather than the raw file or ZIP. It changes neither the Instagram account nor its recommendation feed.

## Continuous companion projection

A continuous Companion requires exactly two active consent slices from distinct owners and distinct source Passports. Each local test principal selects fields, opts into revision refresh, and may target only a Passport they own. The blend stores source versions, selected-field metadata, consent references, and a monotonic sync revision. Revising either source refreshes its slice and recomputes the effective projection; the base Passports are never rewritten. Either consent's revocation or expiry invalidates the Companion immediately. Migration preview fingerprints the effective policy, so a stale plan cannot execute even if integer versions happen to collide after a blend expires. These local owner checks demonstrate protocol structure; caller-supplied principals are not production authentication.

The only supported model provider is an explicitly enabled llama.cpp server over plain HTTP on the loopback interface; otherwise the provider is disabled. It inherits no proxy configuration and has no paid or external fallback. The proof harness hash-verifies and launches the pinned Apache-2.0 `Qwen/Qwen3-1.7B-GGUF` model with the pinned llama.cpp `b8184` runtime on a reserved loopback port, probes that owned process, records process and log evidence, and terminates only that child. Both proposal protocols complete genuine Strands cycles and exact three-tool sequences while deterministic code retains authority. The Strands stop reason `limit_turns` is the expected end of a three-turn proposal protocol, not an execution outcome and not evidence that a mission stopped on a budget.

## Persistence model

The local SQLite store contains an append-only event table plus rebuildable projections. Each event records aggregate ID, version, event type, public payload, trace ID, actor, and timestamp. Secrets and raw private feed bodies are never event payloads.

`feed-passport/v1` is a separate strict public codec rather than a dump of the internal dataclass. Export pseudonymizes local Passport and owner identifiers and omits internal event/history IDs. Import validates the published JSON schema, creates a new local Passport identity owned by the importing actor, and preserves every supported policy field. Provenance carries an issuer-specific key ID and is restored only when an HMAC over the complete exported document verifies through the deployment trust store. Unsigned provenance or provenance from an unknown issuer is stripped and reported while the non-provenance policy still imports; an invalid hash or signature from a trusted issuer rejects the document.

Platform conformance is fail-closed. Runtime listings remain `not_run` unless a structured receipt and its canonical run evidence are supplied together. The serializer validates result/check consistency, hashes the evidence itself, verifies an HMAC binding platform, suite, environment, result, timestamp, evidence hash, and key, then confirms that record matches the manifest. No current external adapter has such an authorized live receipt.

Mutable projections provide current Passports, platform manifests, migrations, visas, companion sessions, alerts, receipts, templates, and evaluation summaries. Optimistic versions prevent concurrent rollback or expiry from applying twice.

## Autonomous due-job runtime

An app-owned FastAPI instance starts `DueJobRunner` in its lifespan and cancels and awaits that task during shutdown. The runner calls `process_due_jobs` in a worker thread, logs an individual pass failure, and continues polling. `FEED_PASSPORT_SCHEDULER_ENABLED` defaults to `1` for this standalone app-owned service; dependency-injected app instances default to disabled unless the environment explicitly sets `1`.

`FEED_PASSPORT_SCHEDULER_INTERVAL_SECONDS` defaults to `5` and must be positive. This is only the persisted-job polling interval. Each drift monitor retains its own 15–1440 minute cadence, action allowlist, per-run budget, confidence threshold, mode, and expiry.

The SQLite store atomically claims due jobs before the application handles overlay activation and expiry, share and companion expiry, or a drift-monitor check. The manual `/api/visas/process-due` route remains available for diagnostics, but normal local operation does not depend on calling it.

## Creator-continuity fixtures

The local browser catalogue maps deterministically into the backend canonical directory. Its client lookups currently route Studio A (`studio-a`) to YouTube `@studio-a`, Paper Lab (`paper-lab`) to YouTube `@paper-lab`, City Zine (`city.zine`) to Bluesky `city-zine.example`, and Studio A (`studio-a`) to X `studio_a`. The destination identities match the directory. The Studio A and Paper Lab cards display Bluesky as their source while the client uses each creator's Lab alias, so the source labels and lookup identities are not textually identical. Service hydration marks a creator preserved only when the destination platform and identity match a catalogue entry. This proves deterministic fixture routing; the backend field name `verified_links` is not verification of a real external profile.

## AgentCore deployment seam

The AgentCore direct-code package exposes only strict `health`, `plan_feature`, and `plan_feed` commands. `plan_feature` accepts one bounded request inside a typed Passport payload. `plan_feed` accepts the same owner-bound Passport plus a bounded natural-language goal and sanitized evidence records containing only source kind, bounded URL-free public metadata text, owner-note-derived labels, deterministic topics, and confidence. It has no source-URL, author/account-identifier, credential, private-history, consent, or exact-social-target field. Both planning commands create a proposal-only Strands agent with one explicitly configured Bedrock model, derive the owner only from the Runtime-validated JWT `sub`, and reject a mismatched Passport owner. Arbitrary chat/unstructured commands and every execute, approval, rollback, credential, browser-control, and live-platform mutation operation are unavailable. There is no provider fallback.

The CDK stack declares only the custom-JWT AgentCore Runtime/Gateway path, its Cognito verifier, and the narrowly scoped roles needed to invoke that Runtime and one explicitly selected Bedrock model. It deliberately omits unused DynamoDB, Secrets Manager, token-vault, and workload-identity resources and permissions. Local tests use a scripted no-network Strands model and synthesize/validate the stack. No AWS account, credential, managed model, competition credit, or deployment is required for that proof. Managed JWT validation, Gateway routing, Bedrock entitlement, AgentCore service behavior, credit coverage, and billing remain unverified. See [the plan-first AgentCore runbook](../infra/agentcore/README.md).

## Testing layers

- Pure unit and property checks for percentages, overlays, blends, policy, expiry, distance metrics, and idempotency.
- Contract tests for JSON schemas and every platform adapter.
- SQLite integration tests including restart, optimistic conflict, expiry-once, and rollback.
- FastAPI lifespan wiring for the due-job runner, unit/application/SQLite coverage for the loop and atomic claims, and a live-service smoke that observes a short-lived visa expire without calling the manual processing route.
- API tests for complete workflows and failure responses.
- Deterministic agent-protocol tests with a scripted Strands model, including exact tool order, duplicate and missing calls, unexpected fields, action-scope narrowing, loopback enforcement, and disabled parallel tool calls.
- A reproducible real-model proof with pinned Qwen GGUF and llama.cpp artifacts, loopback-only inference, hash verification, genuine token use, the exact three-tool proposal sequence, and consent still required. It has no AWS or paid-service gate.
- Optional authorized live-platform tests, serialized and never part of ordinary CI.
- Browser workflow, accessibility heuristics, responsive behavior, history, console/network state, and rendered-interface visual QA are tracked in `design-qa.md`; final user-authorized Playwright reports cover the passive viewport matrix, fixture interactions, and an isolated service-backed Agent mission.

## Delivery slices

The dependency order is contracts, deterministic Lab, policy and receipts, application services, Strands loop, API, interface, certification-gated external transports, AgentCore packaging, full evaluation, and submission evidence. A later slice may depend on an earlier stable interface; it may not change domain authority to accommodate an external platform shortcut.
