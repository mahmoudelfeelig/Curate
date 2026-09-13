# Feed Passport demo script

Target runtime: **4 minutes 45 seconds**. This leaves a 15-second buffer under the official five-minute limit.

## Recording truth rules

- Record the browser in visibly labeled **Local service** mode after freshly verifying the API integration. If it falls back to **Deterministic fixture**, stop or explicitly identify the fallback.
- Confirm both Feature Clerk and Agent mission say the local model is ready. Feature Clerk must show one kind-specific three-tool trace, positive token/latency evidence, `proposal_only` authority, deterministic server-rendered text, and the bound capability where relevant. Applying its result must only pre-fill a desk. Agent mission must separately show deterministic admission and consent before execution. Do not replace either with a fixture or deterministic preview while narrating it as model inference.
- Use the Agent mission desk with a `twin:<platform>` destination for the primary autonomy proof, and Feed Passport Lab for the established migration/overlay flows. A control twin is deterministic local state restricted to declared control semantics; it is not the named platform's ranker and never accesses an account. Never present an external guided plan as a completed social-platform action.
- Confirm the Visa Ledger matches the runtime manifests before recording: every unprefixed external adapter is Guided unless the running exact revision has loaded its own fresh signed dummy-account conformance receipt. Feed Passport Lab and `twin:<platform>` adapters are closed loops only over local simulated state. Candidate transports and OAuth scaffolding are not live proof by themselves. If X reads **Guided + Lab**, explain that “Guided” is the external X plan and “Lab” is local/offline evidence, not a live X adapter.
- Treat **Selected** or **Included** destination cards as local demo scenarios, never as authenticated external accounts. WebMCP reports them as `selectedDestinations`; its separate safe `authorizedAccounts` summary still does not imply conformance or expose an account identifier.
- If Creator Continuity is shown, call Studio A, Paper Lab, and City Zine canonical synthetic fixtures. The client maps each fixture deterministically into the canonical directory; Studio A and Paper Lab use Lab lookup aliases even though their cards display Bluesky as the source. No external profile was queried.
- Describe AgentCore as an optional proposal-only Strands/Bedrock Runtime package behind strict `health`, `plan_feature`, and `plan_feed` commands. The feed planner receives sanitized evidence rather than source URLs, account identifiers, credentials, or exact social targets. Arbitrary chat/unstructured commands, provider fallback, approval, execution, and rollback are unavailable. Do not claim deployment unless a managed invocation receipt exists.
- If Phoenix is mentioned, distinguish the legacy replay harness from the current source-bound bridge. The current official tree is pinned and inspected, but no generator or ranking execution receipt exists; call it current reference-source provenance, not a Phoenix run or ranking Lab.
- Keep the problem, intended user, and importance in the opening 25 seconds because judges may not watch twice.

## Preflight

- Run the repository's complete Node and Python suites, `npm run build`, the 14-scenario evaluation, and the live-service smoke against a fresh local API database. Copy the exact fresh totals into the recording notes only after the integrated run; do not reuse an older count.
- Run `scripts/run-local-feature-planner-proof.py` twice after the final commit. Each run must own and terminate its hash-verified loopback process, cover all three feature kinds with adversarial prose, show positive model usage, prove every mutable SQLite table stayed unchanged, and replace—not merely accompany—receipts bound to an older source snapshot.
- Run `python -m evaluation --output artifacts/evaluations/feed-passport-evaluation.json` and keep the summary ready.
- Verify the pinned model and runtime are present, then start `scripts/start-local-model.ps1`. It must report an offline, loopback-only server and must not be replaced with a paid or hosted endpoint.
- Start the API with `$env:FEED_PASSPORT_MODEL_PROVIDER="llamacpp"`, `$env:FEED_PASSPORT_LLAMACPP_BASE_URL="http://127.0.0.1:8080"`, and `feed-passport-api --reload`. In a second PowerShell window set `$env:VITE_CURATOR_API_URL="http://127.0.0.1:8000"`, then run `npm run dev`.
- Open `http://127.0.0.1:8000/health` and confirm it reports `"scheduler": "active"`; the standalone service polls persisted due jobs every five seconds by default.
- Open `http://127.0.0.1:8000/api/agent/model/status` and confirm the provider is `llamacpp`, readiness is `ready`, endpoint scope is `loopback_only`, and paid/external model calls are false.
- Open the browser overview, Feed evidence, Feature Clerk, Agent mission, Connected Agent, Migration Desk, Temporary Visa Office, Companion, Action Archive, `http://127.0.0.1:8000/api/demo`, and the architecture diagram in advance.
- Reset the browser so no mission is open, the mission route uses a seeded `twin:<platform>` account-free scenario, and the Migration Desk starts with Feed Passport Lab as source, YouTube as destination, with no preview applied.

## Timed recording

| Time | Screen and operator action | Narration |
| --- | --- | --- |
| 0:00–0:25 | Start on the open-passport overview. Slowly point to the Feed Constitution and Destination Visas. | “When someone changes accounts or platforms, they lose years of recommendation tuning and must retrain a black box. Feed Passport is for that person. They describe the feed they want once, then an agent carries that intent forward without copying private history or pretending it owns a platform’s algorithm.” |
| 0:25–1:20 | Open **Feed evidence**. Paste three owner-selected links with notes and ask: “Make my feed 60% pet science, 20% cute drawing, keep the rest exploratory, and reduce ragebait.” Capture before. Point to provenance badges, inferred distribution, target mix, and per-platform controls/losses. Approve and apply only the Passport revision. | “I choose a few posts I actually saw; the product never claims it can download a private FYP. Provider metadata, my notes, and deterministic inference stay visibly separate. Natural language becomes a measurable composition and safety constraint. YouTube, Bluesky, and Instagram receive different translations because their official surfaces differ. This approval changes only my portable Passport—no social account has been touched.” |
| 1:20–2:15 | Open **Agent mission**. Choose a platform control twin and use the model planner. Show its exact three-tool trace and admitted controls, then approve once, execute, inspect before/after evidence, separately approve rollback, and show the exact fingerprint match. | “The request-bound Strands agent proposes a strategy, but deterministic code owns identity, budgets, consent, execution, measurement, and rollback. This account-free twin proves the complete control loop without pretending to be a platform’s private ranker.” |
| 2:15–2:45 | Open **Connected Agent** and **Migration Desk**. Show the exact YouTube/Bluesky certified-commission gate and Instagram’s guided-only boundary, then show capability translation loss. | “A real destination can run only after owner OAuth, an exact-revision dummy-account certification, and one-time consent to a sealed action list. The agent may reorder allowed action families; it never invents targets or authority. Instagram remains guided because its consumer recommendation and arbitrary follow surfaces are not available through the official integration used here.” |
| 2:45–3:10 | Open **Feature Clerk**, request an isolated 48-hour research context, show the exact three-tool proposal, then apply it to pre-fill Temporary Visa without issuing it. | “The same agent architecture supports temporary feeds, migration, and companion sync. The model returns typed bounded choices. Applying its answer prepares a deterministic desk; it cannot approve or execute itself.” |
| 3:10–3:35 | Issue the pre-filled **Temporary Visa**, then show revoke/expiry. Briefly open **Companion** and show the two-principal consent boundary. | “A Temporary Visa changes the effective local policy and expires without rewriting the base Passport. Companion requires two distinct owners to consent to specific policy fields; either person can revoke.” |
| 3:35–3:55 | Point to WebMCP status and the platform Visa Ledger. | “WebMCP exposes seven site-owned inspection and preparation tools, including bounded feed-evidence preview, with no approval or execution tool. Ten local twins stay explicitly separate from authenticated destinations.” |
| 3:55–4:20 | Show the architecture diagram, local model receipts, and, only if completed, the managed AgentCore receipt. | “Model proposals and deterministic authority are separate. The local proof is offline and hash-bound. AgentCore adds a strict proposal-only `plan_feed` command using sanitized evidence; it still has no social execution tool.” |
| 4:20–4:35 | Show fresh terminal summaries and a newly verified evidence manifest bound to the same final commit. | “These are the exact final checks for this revision: complete Python and Node suites, fourteen offline scenarios, responsive browser flows, model tool traces, one-time run checkpoints, receipts, and rollback. Live-provider claims appear only when their matching redacted conformance receipt exists.” |
| 4:35–4:45 | Return to the overview and center the passport. | “Feed Passport gives an agent real work, but gives the human the authority: your feed, your rules, portable wherever platforms permit.” |

## Recording inserts

Use only fresh, legible evidence:

- The browser placard and Agent mission desk showing **Local service**
- Feature Clerk showing **Local model ready**, one exact three-tool proposal, positive token use, deterministic text source, bound capability, and proposal-only desk prefill
- The Agent mission planner evidence showing **Local model ready**, exactly three completed tools, positive token use, proposal-only authority, and deterministic validation
- A local mission showing its exact budgets, before/counterfactual/after measures, one-time consent, stop reason, receipt, and exact local-control-state fingerprint match after separately approved rollback
- A migration preview with visible translation loss
- A Feed evidence before/after comparison showing provider metadata, owner context, inference, explicit sampling limits, and no causality claim
- A Guided handoff whose exact steps are honestly marked skipped or control-not-found, followed by a user-attestation receipt showing zero API writes and zero verified recommendation outcomes
- Optionally, an opt-in loopback user-supplied Accounts Center-format `following.json` fixture import showing that only selected handles change Passport intent; show the unobserved-fields list and no-retention/account-access labels, and never call the file provider-authenticated or the flow an Instagram API run
- A Feed Passport Lab receipt with its approval scope and rollback outcome
- The `/health` response showing the autonomous scheduler active
- The evaluation summary produced immediately before recording
- The Mermaid architecture diagram from the README
- The curated `artifacts/evidence/` reports after `manifest.json` binds their hashes to the tested source revision
- Omit AgentCore deployment entirely unless a real managed `health` and `plan_feed` invocation/trace receipt exists for the recorded revision

The recorded browser flow performs no external-account mutation. A seeded twin account is deterministic local state, not a dummy account on a social platform. A separately captured redacted receipt may establish an authorized dummy-account conformance run, but never show the account, credential, OAuth callback, or private feed. Do not present a selected destination card, local export fixture, Guided user-attestation receipt, or twin run as proof that an external platform was accessed. The owner-authenticated YouTube/Bluesky Connected Agent routes count as live-account evidence only while a fresh certification is loaded for the exact running revision. The Instagram import is local portability intake, not account observation or mutation.

## Final upload check

The video must be public on YouTube or Vimeo, no longer than five minutes, in English or accompanied by English translations, and accessible throughout judging. Verify audio, small text, URL visibility, and that no credentials, account identifiers, browser history, or private content appear in the recording.
