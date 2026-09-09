# Verification report

Date: 9 September 2026

This report covers the current append-only integration work built on the public,
sanitized 40-commit baseline
`d6727618fbe9339d1c95281806498002096dbdbd`. The public repository is
[mahmoudelfeelig/feed-passport](https://github.com/mahmoudelfeelig/feed-passport).
The source-binding manifest is committed only after the implementation and
documentation commits so its clean-tree gate can bind the final indexed source.

## Verdict

- **Account-free deadline demo:** Pass. Instagram portability intake plus exact Guided handoffs for Instagram, YouTube, and Bluesky run locally without a social account. The owned browser proof uses a disposable API/database and makes no non-local request.
- **Live integration implementation:** Pass at the source and contract-test boundary. YouTube subscriptions and Bluesky follows, actor mutes, and muted words have owner-bound OAuth transports, signed exact-revision certification, consent, journals, reconciliation, and rollback safeguards. No live account or provider call was used, so neither platform is promoted beyond Guided.
- **Instagram boundary:** Pass and deliberately narrower. The app parses only a user-supplied Accounts Center-format following export, retains no raw upload, and merges only explicitly selected handles into Passport intent. It does not log in to Instagram, write an Instagram control, or claim access to consumer recommendation state.
- **Agent boundary:** Pass locally. The internal YouTube/Bluesky Strands planner may return only a complete priority ordering of certified action families for an already compiled plan. Deterministic code withholds exact targets, fixes the budget, seals the action sequence, owns consent, and performs any later write. External model profiles fail closed. This commission lifecycle is not exposed through HTTP or the mounted browser UI pending an explicit live-endpoint authorization decision.
- **Release readiness:** Ready for the local hackathon demo and source review. Not live-provider validated and not AWS-deployed. Provider registrations, dummy-account OAuth, signed live receipts, AWS deployment/invocation, and the entrant's Builder ID remain external gates.

## Fresh verification gates

| Gate | Fresh result |
| --- | --- |
| Full Curator suite | 519 passed, 155 subtests passed, two upstream dependency deprecation warnings |
| Independent live recovery/concurrency focus | 146 passed; no remaining material race, mutation bypass, lock inversion, or deadlock found |
| Root browser/API contract suite | 76 passed |
| AT Protocol sidecar suite | 52 passed; every sidecar source module also passed `node --check` |
| Production client build | 48 modules transformed; `dist/client`, `dist/server`, and `dist/.openai` prepared |
| Python bytecode compilation | Passed for `services/curator/src` and `evaluation` |
| Python dependency consistency | `pip check` reported no broken requirements |
| Owned local service proof | 14 assertions passed against a disposable loopback API and database; the owned process and database were removed afterward |
| Deterministic evaluation suite | 14 scenarios passed with no provider, model, or other external request |
| Owned browser platform proof | 8 assertions and 6 screenshots passed at desktop and 390 CSS pixels; zero external HTTP/WebSocket requests, console errors, or page errors |
| External-readiness inspection | Passed as a read-only inventory with zero network calls and no secret values printed |
| Source-bound evidence | The clean-tree indexed-blob verifier is the final post-commit gate; its exact source digest, file count, artifact hashes, and commit count live in `artifacts/evidence/manifest.json` |

## Owned deadline browser proof

The full Python command was:

```powershell
.\services\curator\.venv\Scripts\python.exe -m pytest services/curator/tests -o addopts= -q -p no:cacheprovider
```

The account-free deadline browser command was:

```powershell
npm run test:browser:platforms
```

The browser runner strips inherited Feed Passport, Vite, and AWS configuration, forces loopback demo authentication with model inference disabled, allocates fresh ports, and creates a new temporary SQLite database. The API returns a one-run UUID and HMAC database-path binding through `/health`; both the parent and the browser child verify that proof before any state revision. The child blocks Service Workers and non-local HTTP or WebSocket traffic, has a 180-second hard limit, and refuses reused or path-traversing output names. The final run then confirmed both owned processes stopped and its temporary directory was removed.

The local ignored report is `artifacts/browser-qa/final-platform-portability-owned-v3/report.json`. It records the strict Instagram preview/selection/consumption flow, a no-overflow 390px state, and exact server-bound Instagram, YouTube, and Bluesky handoff steps. Every completed handoff in that account-free proof used `CONTROL NOT FOUND` and finalized with zero API writes and zero verified recommendation outcomes.

## Safety and recovery evidence

- The Instagram parser rejects ambiguous JSON, duplicate recognized archive members, encrypted or unsafe paths, oversized/deep input, invalid scalar types, and archive expansion abuse. A 15-minute owner/Passport/version-bound session retains normalized preview data only; apply consumes and redacts it.
- Guided handoffs durably preserve the exact target and instruction before approval. One active handoff locks its owner/Passport route, restart recovery is owner-scoped, and final receipts remain explicit user attestations rather than platform-write evidence.
- Live commission recovery resumes a durable sealing transition without asking the model twice, and can reconstruct completion only from an exact matching receipt or journal outcome. A valid but expired certification permits only observation of an already-journaled uncertain write; it grants no planning, execution, replay, or rollback authority.
- Expired forward and rollback leases, provider outcome-unknown results, untyped rollback failures, and legacy unknown rollback records terminate in `needs_human` rather than replaying a mutation. Active journal leases cover the whole migration, action state is refreshed before each compare-and-swap, and deterministic create-only receipt IDs prevent duplicate receipts across competing processes.
- OAuth callback restoration during an active Guided handoff discards the pending provider callback instead of exchanging or storing it. Initial browser hydration keeps mutation and WebMCP tools fail-closed until authoritative owner state is loaded.

## External boundary and deliberately unperformed actions

During this integration and verification work:

- social accounts used: 0;
- OAuth authorizations or platform API requests: 0;
- external or paid model calls: 0;
- AWS deployments or AgentCore invocations: 0;
- competition credits or other paid services used: 0.

YouTube still needs an owner-created Google Cloud project, enabled YouTube Data API v3, testing-mode consent screen, OAuth web client, and dummy YouTube account. Bluesky still needs the account owner's dummy account plus a public HTTPS metadata/JWKS/callback origin and private sidecar configuration. Instagram has no general consumer recommendation-control write API represented here; its honest deadline path is the local export intake plus exact native-control handoff.

AWS credentials were not inspected and SSO was not refreshed. The local readiness checker found partial AgentCore inputs invalid and classified actual deployment/invocation as `zero_spend_blocked`, because a budget or promotional credit is not a hard guarantee of zero charges. Builder ID is an entrant-owned identity step and cannot be created or accepted by the repository.

## Curated evidence provenance

The large curated browser/model/evaluation artifacts under `artifacts/evidence/` remain immutable evidence from the sanitized 40-commit baseline. They are not relabeled as outputs of the new integration source. The current manifest records that archived provenance separately while binding the complete current source snapshot and fresh integration gate results. Generated databases, raw exports, OAuth state, keys, logs, caches, normal browser runs, and build output remain ignored.
