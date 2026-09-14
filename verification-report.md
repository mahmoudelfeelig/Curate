# Verification report

Date: 14 September 2026

This report covers the current append-only integration work built on the public,
sanitized 40-commit baseline
`d6727618fbe9339d1c95281806498002096dbdbd`. The public repository is
[mahmoudelfeelig/feed-passport](https://github.com/mahmoudelfeelig/feed-passport).
The source-binding manifest is committed only after the implementation and
documentation commits so its clean-tree gate can bind the final indexed source.

## Verdict

- **Social-account-free practice demo:** Pass. Vague and exact goals, visible six-card before/after comparison, a real loopback-model plan, deterministic execution, measurement, and verified rollback appear in the final 62.891-second Curate capture.
- **Live integration implementation:** Historical live conformance pass at revision `60a2d149`. Authorized dummy YouTube and Bluesky accounts completed one reversible subscription/follow, post-write reconciliation, reverse rollback, and connection revocation. Exact-revision policy intentionally keeps those older certificates from promoting the current revision.
- **Instagram boundary:** Pass and deliberately narrower. The app parses only a user-supplied Accounts Center-format following export, retains no raw upload, and merges only explicitly selected handles into Passport intent. It does not log in to Instagram, write an Instagram control, or claim access to consumer recommendation state.
- **Agent boundary:** Pass. Owner-authenticated commission routes are exposed for certified YouTube/Bluesky connections, while the retained managed AgentCore path remains proposal-only. One final managed health result and one Bedrock `PlanFeed` result are preserved locally; both report no execution authority.
- **Release readiness:** Product, public judge build, retained AWS proposal service, silent video, responsive matrix, and local verification are ready. Entrant-owned finishing work is narration/public video upload, Builder ID/disclosures, and Devpost submission.

## Fresh verification gates

| Gate | Fresh result |
| --- | --- |
| Full Curator suite | 550 passed, 159 subtests passed, two upstream dependency deprecation warnings |
| Independent live recovery/concurrency focus | 146 passed; no remaining material race, mutation bypass, lock inversion, or deadlock found |
| Root browser/API contract suite | 108 passed |
| AT Protocol sidecar suite | 58 passed |
| Responsive browser matrix | Five viewports passed with zero overflow, clipped audited controls, unlabeled controls, failed requests, console errors, page errors, or rollback collisions |
| Final silent video | 62.891 seconds, 1440 by 900, no audio track; independent eight-frame audit matched SHA-256 `22f3df13821771ce0625a0fe8ff7e20513592aa2ddba09dd4a813216c0b846cf` |
| Narration finalizer | Passed against the final silent video with a synthetic 60-second audio fixture: VP9 video preserved, Opus audio normalized, duration held at 62.9 seconds, captions and all inputs hashed into the release report |
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

## External boundary and retained proof

The live YouTube and Bluesky proof used only newly created dummy accounts. Each final conformance run applied one reversible control, verified the provider state, restored the original state, and revoked the connection. The ignored receipts are revision-bound and contain no reusable credential. Instagram's honest boundary remains local export intake plus guided native controls; no general consumer recommendation-feed write API is claimed.

The minimal AgentCore stack remains deployed in `eu-north-1`. Exactly one final managed health invocation and one final Bedrock `PlanFeed` invocation were made under the user's explicit authorization. The redacted receipts record a healthy proposal-only runtime, no mutation tools, `approved=false`, and `executed=false`. They are not repeated by any normal test. The USD 5 AWS Budget is an alert, not a hard cap, and promotional-credit coverage is not claimed.

Cloudflare Pages currently serves [curate.elfeel.me](https://curate.elfeel.me/) with the Curate title, branded assets, CSP, `no-referrer`, and frame-denial headers. The public workspace is private-judge-sign-in-gated. The social-account-free Lab and managed proposal path become available after that Cognito sign-in.

Judge credentials and the expired token artifact remain ignored and now have owner-only Windows ACLs plus SYSTEM access. Builder ID, public video upload, disclosure confirmation, and Devpost submission remain entrant-owned actions.

## Curated evidence provenance

The large curated browser/model/evaluation artifacts under `artifacts/evidence/` remain immutable evidence from the sanitized 40-commit baseline. They are not relabeled as outputs of the new integration source. The current manifest records that archived provenance separately while binding the complete current source snapshot and fresh integration gate results. Generated databases, raw exports, OAuth state, keys, logs, caches, normal browser runs, and build output remain ignored.
