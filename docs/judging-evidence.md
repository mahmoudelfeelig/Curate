# Agents for Humans judging evidence

This page maps the current Curate repository to the controlling Agents for Humans requirements. Hackathon facts were rechecked against the official Devpost pages on **August 31, 2026**. Where the overview, FAQ, or form language differs, the Official Rules prevail.

## Recommended track and submission position

**Track:** Everyday Agents

The primary user is an individual who wants to regain control of everyday recommendation feeds while changing an account, changing a platform, sharing a temporary context, or avoiding weeks of manual retraining. Professional and community uses are possible, but the [official FAQ](https://agentsforhumans.devpost.com/details/faqs) says track selection should follow the primary user and each project may enter only one track.

**Core claim:** Curate is a portable policy for feed intent, with capability-aware translation, measurement, and rollback. It does not copy a proprietary algorithm or claim access to hidden ranking state.

## Controlling event facts

| Requirement | Verified official fact | Curate action |
| --- | --- | --- |
| Submission window | August 10, 2026 at 9:00 a.m. PT through September 14, 2026 at 5:00 p.m. PT | Submit before the deadline; do not rely on a last-minute upload |
| AWS promotional credits | The [official FAQ](https://agentsforhumans.devpost.com/details/faqs) says registered participants may request $50; requests close September 11, 2026 at 12:00 p.m. PT, while supplies last, and remain subject to the promotional-credit terms | The bounded managed proof uses the entrant's AWS account under a fixed USD 5 alert budget. Credits and Budgets are not a hard cap; the minimal application stack is retained for judging and receives no routine background invocation |
| Judging | September 15 at 9:00 a.m. PT through October 8 at 5:00 p.m. PT | Keep free, unrestricted judging access working through October 8 |
| Winner announcement | On or around October 14, 2026 at 2:00 p.m. PT | No build dependency |
| Required technology | A new, working AI agent built with the Strands Agents SDK that performs real work end-to-end | Show Feature Clerk classifying a natural-language request into a typed, capability-bound feature proposal, then show the separate local-twin mission loop narrowing controls, stopping for consent, executing within budgets, measuring, and restoring exact state on rollback |
| AWS account | The official FAQ says an AWS account is required to participate | The local proof remains independently runnable. A separate owner-run managed receipt proves one AgentCore `health` and one `plan_feed` invocation without putting AWS credentials or endpoint identifiers in the repository |
| AgentCore | Optional; a live demo and/or AgentCore deployment strengthens Technical Implementation | The checkout has a proposal-only Strands/Bedrock Runtime package plus plan-first CDK. It exposes strict `health`/`plan_feature`/`plan_feed` commands and no mutation authority. Managed evidence is kept as redacted local receipts tied to the AgentCore build used for that run, separate from current repository-source and browser claims |
| Repository | Public GitHub, GitLab, or Bitbucket repository with source, assets, setup instructions, and README | [Public GitHub repository](https://github.com/mahmoudelfeelig/feed-passport) exists; recheck visibility and final source from a signed-out browser before submission |
| License | MIT or Apache license, detectable and visible in repository metadata/About | MIT file exists; confirm host metadata still detects it before submission |
| Architecture | Diagram showing input/UI, Strands loop, tools/integrations, AWS services, and output | Mermaid diagram is in the README and distinguishes the independently runnable local path from the separately evidenced managed AgentCore path; export a PNG for Devpost if its renderer requires an upload |
| Video | Public YouTube or Vimeo; maximum five minutes; working demo plus problem, user, and importance | An approximately 94-second clean-revision silent cut and matching short voiceover script are ready; record/mix the supplied voiceover, then publish the narrated cut |
| Working access | Website, functioning demo, or test build available free and unrestricted during judging | [curate.elfeel.me](https://curate.elfeel.me/) is private-judge-sign-in-gated. After sign-in, it exposes both the social-account-free practice flow and the retained managed proposal path; recheck both immediately before submission and during judging |
| Submission identity | AWS Builder ID required | Entrant must provide it in Devpost |
| Language | English, or English translations for all judged materials | Repository documentation is English |
| Newness | Project created during the event window; ordinary tools/frameworks/templates are allowed; other pre-existing work must be disclosed | Entrants must confirm dates and disclose any pre-existing assets or code; this document does not make that claim |
| Third-party integrations | SDKs, APIs, and data require authorization and license compliance | Default Guided adapters make no calls. Candidate owner-bound transports for YouTube, X, Reddit, and Bluesky remain dormant unless external configuration and a signed authorized-dummy-account conformance receipt exist; generic migrations cannot execute them. Instagram accepts only a user-supplied Accounts Center-format following export through an opt-in loopback parser; the file is not provider-authenticated, and the path is not OAuth or provider API access |
| Sensitive data | The FAQ recommends synthetic, anonymized, or public data; entrants remain responsible for any real personal data | Keep the judged demo on deterministic fixtures and authorized public evidence |
| Team | No team-size limit in the FAQ; all members must be eligible and a team/org needs one representative | Entrants must verify eligibility and representative details |

The full jurisdiction, conflict-of-interest, IP, and prize conditions are in the [Official Rules](https://agentsforhumans.devpost.com/rules). “Open worldwide” is marketing shorthand; the rules list material exclusions.

Judges are allowed to rely only on the description, images, and video rather than running the project. The five-minute recording and its visible evidence therefore need to stand on their own.

## Prize context

The [official prize table](https://agentsforhumans.devpost.com/#prizes) gives a $40,000 cash pool: one $10,000 Grand Prize, plus Gold, Silver, and Bronze awards of $5,000, $3,000, and $2,000 in each of the three tracks. One project may win only one prize. Curate should enter Everyday Agents rather than weakening its primary-user story by chasing multiple tracks.

## Stage One gate

Stage One is pass/fail. Judges check thematic fit, baseline viability, and reasonable use of the required SDK/tools.

| Gate | Repository evidence | Remaining risk |
| --- | --- | --- |
| Theme fit | A person can turn a natural-language recommendation preference into a versioned Passport and use it for migration, temporary modes, sharing, drift, and rollback | Keep the pitch centered on real personal busywork, not on platform-growth automation |
| Strands viability | A source-bound archived browser receipt shows a request-bound `strands.Agent` using pinned Qwen3 1.7B through the official llama.cpp b8184 loopback runtime, calling exactly `inspect_selected_passport`, `inspect_selected_control_surface`, and `submit_mission_proposal`, using 3,521 tokens, passing deterministic validation, and stopping awaiting consent with zero paid or external model calls | The committed receipt is evidence for the manifest's recorded source snapshot, not automatically for later commits. Regenerate and rebind it before presenting it as final-current proof. **Local model** still does not prove AWS/AgentCore deployment or access to a social account |
| Working end-to-end agent | Feature Clerk uses genuine local inference to choose one typed feature configuration while deterministic code authors capability language and preserves every mutation gate. The separate mission planner narrows a local twin's controls; the persisted runner then handles consent, execution, measurement, receipt, and separately approved exact rollback | **Local service** and `twin:<platform>` mean deterministic local simulation, not a social-account connection or ranking replica; unprefixed external platforms remain guided and must not be shown as executed |

## Stage Two criteria-to-evidence map

The [Official Rules](https://agentsforhumans.devpost.com/rules) say the five criteria are **equally weighted**. The 20% figures below are arithmetic equivalents; Devpost does not print numeric percentages. Ties are broken in the listed order, making Technical Implementation the first tie-break criterion.

| Criterion | Weight | Evidence to show judges | Honest gap and next proof |
| --- | ---: | --- | --- |
| Technical Implementation | 20% | Two request-bound local Strands protocols; exact tool traces and token accounting; typed Feature Clerk submissions with no model-authored prose; capability-bound deterministic rendering; deterministic owner checks, proposal admission, and mission-bound one-time consent; model-facing context excludes identity, credentials, approval, execution, and rollback authority; persisted missions, budgets, evaluations, stop reasons, receipts, and durable remote-action attempts; ten restricted local control twins; exact reverse-order rollback verification; OIDC owner binding; encrypted connection/OAuth boundaries; bounded live candidates; official AT Protocol OAuth/DPoP sidecar; autonomous FastAPI runner; proposal-only AgentCore/Bedrock package with plan-first CDK; fresh final test counts are recorded only after integrated verification | Managed AgentCore and authorized dummy-account evidence is owner-run, short-lived, revision-bound, and retained only in the ignored local evidence area; the public checkout bundles no cloud credential or live-account receipt. Source/contract tests remain distinct from managed-service and account proof |
| Design | 20% | A responsive utility built around a coherent 2009–2013 passport/customs metaphor; Feature Clerk makes model classification inspectable through its exact trace, tokens, latency, server-rendered text source, bound capability, and proposal-only apply behavior; Agent mission makes bounded autonomy inspectable through hard budgets, consent, measured execution, stop reason, and rollback | A fresh Curate matrix passes all fourteen desks at four standard viewports plus three desks at effective 200% reflow with zero recorded violations. This remains a product-specific browser audit, not a full WCAG or assistive-technology conformance claim |
| Potential Impact | 20% | Specific audience: people switching accounts/platforms or temporarily changing context; concrete harms avoided: weeks of retraining, opaque drift, accidental permanent learning, and oversharing; ten account-free control twins make the safety loop demonstrable without risking personal accounts; 14 deterministic acceptance scenarios cover the established product flows, all ten twins, and the bounded mission lifecycle | No live-user outcome study or real-platform before/after measurement; present impact as a credible hypothesis backed by a working local proof |
| Creativity & Originality | 20% | Portable intent rather than impossible model copying; “visa” as a declared capability evidence level, “customs” as consent, “temporary visa” as expiring overlay, “travel companion” as a selected share slice, and “return ticket” as rollback | Threads has native temporary algorithm requests and limited preference-copying concepts; differentiate on cross-platform compilation, evidence levels, consent, measurement, and rollback rather than either feature alone |
| Presentation | 20% | The approximately 94-second cut opens on the product, demonstrates conversational and exact requests, shows YouTube and Bluesky before/after cards, a local-model plan and rollback, Copy feed, Incognito, and Blend; the custom-domain site and judge login are live | Narration, public YouTube/Vimeo upload, Devpost screenshots, and final submission copy remain entrant-owned finishing work |

## Evidence index

| Claim | Best repository evidence |
| --- | --- |
| Strands is substantively used | `services/curator/src/feed_passport/agent/curator_agent.py`, `agent/tools.py`, `agent/hooks.py`, and `tests/agent/test_consent_and_strands.py` |
| Genuine model inference is proven without a paid provider | `agent/model_provider.py`, `agent/feature_intent_planner.py`, `agent/mission_planner.py`, `scripts/run-local-feature-planner-proof.py`, `scripts/run-local-model-proof.py`, [both cold-start Feature Clerk receipts](../artifacts/evidence/evaluations/local-feature-planner-proof.json), and [the rendered two-agent browser proof](../artifacts/evidence/browser/final-official-b8184-model/report.json) |
| Feature Clerk cannot smuggle identity or authority prose | Its tool schemas accept only destination enums, bounded durations/modes, selected field categories, strategy, and numeric weight. Server templates render every displayed sentence; adversarial and capability-tampering tests plus two cold-start receipts prove zero mutation and semantic repeatability |
| The model cannot self-authorize | `agent/consent.py`, `domain/policy.py`, migration approval API routes, and policy-safety tests |
| The product handles a bounded agent mission end-to-end | `services/curator/src/feed_passport/application/mission_runner.py`, `services/curator/src/feed_passport/domain/missions.py`, `services/curator/src/feed_passport/agent/consent.py`, mission API routes, `services/curator/tests/agent/test_agent_missions.py`, and the Agent mission UI in `src/App.jsx` |
| State and receipts survive process boundaries | `infrastructure/sqlite_store.py` and SQLite restart/concurrency integration tests |
| Expiry and monitors advance autonomously | FastAPI lifespan wiring is implemented in `api/app.py`; `runtime/scheduler.py`, unit/application/SQLite tests cover the runner loop, companion/share expiry, restart-safe jobs, processing, and atomic claims; `scripts/run-live-service-proof.mjs` owns a disposable API/database lifecycle and invokes `scripts/live-service-smoke.mjs` to observe a real short-lived visa expire through the active lifespan runner |
| External capability claims are conservative | Default unprefixed adapters remain Guided; owner-bound OAuth/connection/vault ports, candidate live adapters, durable action journal/reconciliation, AT Protocol sidecar, certification verifier, and conformance gate cannot load an action without a fresh exact-revision signed dummy-account receipt. Generic migration authority still cannot execute a live adapter |
| Internal live commission stays inside certified authority | `application/live_commission.py` and `agent/live_commission_planner.py` accept only YouTube/Bluesky. The local-only model receives privacy-reduced Passport-derived demand buckets plus all certified family names/counts and must output only a complete family permutation; it receives or authors no goal, rationale, target, authorization, credential, or authority. Deterministic code applies that order to withheld exact actions under the locked budget, seals the result, and revalidates the owner connection, Passport, compiled plan, certification, and one-time owner action envelope before execution. External model calls fail closed. The Connected Agent desk and owner-authenticated API expose the lifecycle; live-account evidence remains a separate ignored exact-revision receipt |
| Guided completion is not mislabeled as execution | The owner-bound handoff state machine seals exact native steps, requires every step to be resolved as completed, skipped, or not found, and issues a receipt only after explicit finalization. Outcomes are only `GUIDED`/`SKIPPED`; the summary fixes API writes, verified recommendation outcomes, and platform verification at zero/false |
| Instagram portability is narrow and local | The opt-in loopback parser accepts only user-supplied Accounts Center-format following JSON, rejects unsafe archives and malformed relationships, retains no raw upload, stages normalized handles for 15 minutes, and merges only the user's selected subset into Passport creator intent. Provenance stores a normalized selected-subset digest, not provider authentication or a raw archive digest. It never logs into Instagram or writes a platform control |
| Local platform coverage does not overclaim ranker fidelity | `services/curator/src/feed_passport/adapters/twin/adapter.py` and `services/curator/tests/test_local_platform_twins.py` register all ten `twin:<platform>` IDs, restrict execution to declared control semantics, exclude public engagement, and label observations and losses as simulation-only/no-ranking-fidelity |
| AgentCore source and managed evidence stay distinct | `runtime/agentcore_app.py`, `runtime/agentcore_bootstrap.py`, proposal-only Bedrock model configuration, `Dockerfile.agentcore`, and `infra/agentcore` are source evidence. Local scripted-model/CDK checks do not create managed-service evidence; the redacted owner-run receipt records the separate bounded deployment and invocation proof |
| Browser implementation maps its desks to the API | `src/App.jsx`, `src/styles.css`, `src/apiClient.js`, `scripts/browser-qa.mjs`, `scripts/browser-flow-qa.mjs`, and `scripts/browser-service-qa.mjs`; shipped asset provenance is in `THIRD_PARTY_NOTICES.md`, curated captures are under `artifacts/evidence/screenshots/`, and final rendered evidence is recorded in `design-qa.md` |
| Shipped visual assets are license-traceable and not AI-generated | `THIRD_PARTY_NOTICES.md`, vendored license texts under `public/licenses/`, and shipped textures, fonts, and Lucide icons under `public/assets/licensed/` and `public/fonts/` |
| Creator fixtures resolve through the canonical directory | `application/curator.py`, `src/data.js`, and `src/apiClient.js` map Studio A, Paper Lab, and City Zine deterministically into the canonical synthetic directory. Studio A and Paper Lab use Lab lookup aliases while their cards display Bluesky; no external profile match is claimed |
| WebMCP is bounded to this site | `src/webmcp.js`, `src/webmcp.test.mjs`, and `tests/webmcp.test.mjs`; seven tools are registered, including local-mission and feed-evidence previews, while tests assert that no approval, execution, or run-mission tool exists |
| Source capture is truthful | `application.infer_passport_from_account`, `/api/passports/capture`, the Strands capture tool, client/UI capture path, and adapter tests cover Lab plus one declared dummy snapshot for each external platform; unobserved account IDs are rejected |
| Acceptance evaluation is reproducible and offline | `evaluation/suite.py`, `evaluation/cli.py`, and `tests/test_evaluation_suite.py`; `services/curator/constraints-py313-win.txt` freezes the exact verified dependency graph and was independently installed into a new environment before the full Python suite ran |
| X research provenance is not a live-feed claim | `evaluation/x_phoenix_bridge.py` preserves the legacy-layout contract; `evaluation/current_x_phoenix_bridge.py` pins the current official checkout, hashes its tracked manifest/reference sources, allowlists synthetic generators only, excludes ranker execution, and requires `live_feed_changed: false`. Its contract is tested, but no current generator execution receipt is claimed |

The committed [evidence manifest](../artifacts/evidence/manifest.json) binds each immutable curated report and screenshot to its path and SHA-256, plus a canonical digest of the tracked source snapshot excluding the self-referential evidence directory. Historical receipts remain historical even when the manifest binds the current source; the fresh Curate browser and video reports stay in the ignored local release-evidence area.

## Submission readiness

| Item | Status on 13 September | Required before submission |
| --- | --- | --- |
| Track | Chosen | Enter Everyday Agents only |
| README and setup | Ready in repository | Recheck commands from a clean clone |
| Architecture source | Ready as Mermaid | Export/upload a readable image if needed |
| MIT license | File exists | Confirm license appears in public repository metadata/About |
| Deterministic evaluation | Fresh local run reports 14 of 14 scenarios passed; archived manifest snapshot independently reports the same count | Keep the fresh local JSON with the final submission evidence |
| Social-account-free local agent mission | The final Curate capture records a real loopback-model plan, bounded local-twin run, visible six-card before/after result, and verified rollback; independent frame and media audits corroborate the cut | Keep both reports with the final video; do not describe the practice twin as a private ranking replica |
| Feature Clerk local-model proof | Archived self-owned pinned-runtime receipts cover migration, Temporary Visa, and Companion with adversarial identity, authority, and capability prose; current source tests preserve the same non-mutating contract | Keep the archived receipts labeled as historical. Regenerate only if the submission makes a current cold-start receipt claim; applying a result must still only pre-fill its desk and perform no mutation call |
| Local model runtime | The final capture report records Qwen3 1.7B on loopback, a local-model-planned mission, zero paid/external model calls, local-twin execution, and verified rollback; the independent media audit confirms the resulting cut | Keep the capture and audit reports together with the final video; do not substitute an unverified hosted provider |
| Public repository | Public `main` is updated through the final release commit; secrets and local receipts remain ignored | Recheck visibility and README rendering from a signed-out browser immediately before submission |
| Working judge access | Verified on 14 September at [curate.elfeel.me](https://curate.elfeel.me/): Cognito authorization-code + PKCE login returned to `/auth/callback`, stamped the judge session, and exposed sign-out without browser errors | Recheck the canonical URL from a signed-out browser immediately before submission and keep it available through October 8 |
| Silent demo video | Ready to regenerate from the release commit at 1440×900; the recorder binds the clean Git revision and evidence digest, checks both prompt styles, verifies three YouTube and three Bluesky cards in each feed, Copy feed, Incognito issue/revoke, Blend, and rollback, and rejects external browser requests | Add the recorded voiceover when supplied, then upload the final narrated cut to YouTube or Vimeo |
| AWS Builder ID | External action pending | Add to submission |
| Project-newness disclosure | Entrant confirmation pending | Disclose any work beyond allowed frameworks, tools, templates, and assistants |
| Live AgentCore deployment | The minimal `eu-north-1` application stack is retained for the judge flow; one managed `health` and one `plan_feed` result are preserved in ignored local receipts | Keep the fixed USD 5 alert budget, do not repeat the release-verification invocations, and keep tokens, account identifiers, judge credentials, and full Gateway receipts out of the repository. Never imply credits or Budgets guarantee a hard cap |
| External live accounts | On 13 September, owner-run local evidence temporarily subscribed an authorized dummy YouTube account and followed `bsky.app` from an authorized dummy Bluesky account, independently verified each control, restored both baselines, and retained only redacted revision-bound receipts under ignored `artifacts/local/`. A supplied Instagram Accounts Center export parsed 9 following relationships locally, retained no raw source, and changed only the Passport | Regenerate conformance only before enabling a current-revision live adapter or claiming current live certification. Keep the completed runs explicitly historical. Never imply the Instagram import was provider-authenticated or that any control proves a recommendation-ranker change; unsupported platforms remain Guided |

## Optional build-story bonus

Stage Two permits up to three public `builder.aws.com` build-story posts, worth +0.2 points each and capped at +0.6. They must be public before the submission deadline.

The rules header says the August 12 update removed the `#AgentsforHumans` requirement, while later copy and the overview still ask for “Agents for Humans” in the title. The safest reading is to include the exact words **Agents for Humans** in each title without depending on a `#` character. Recheck the submission form before publishing.

Suggested evidence-first posts:

- Why portable feed intent is different from copying a proprietary algorithm
- How deterministic consent prevents an agent from authorizing itself
- What a capability-certified adapter must prove before it may be called live

## Official sources

- [Agents for Humans overview](https://agentsforhumans.devpost.com/)
- [Official Rules](https://agentsforhumans.devpost.com/rules)
- [FAQ](https://agentsforhumans.devpost.com/details/faqs)
- [Official schedule](https://agentsforhumans.devpost.com/details/dates)
- [Official xai-org/x-algorithm repository](https://github.com/xai-org/x-algorithm)
- [Current official Phoenix tree](https://github.com/xai-org/x-algorithm/tree/main/phoenix)

## Known rule ambiguities

- A live-demo URL is described as optional, while the testing section requires working website/demo/test-build access. Provide access.
- The hashtag instruction conflicts across the August 12 update note and later copy. Use the exact phrase “Agents for Humans” and verify the final form.
- No numeric percentages, per-criterion aggregation method, or rounding method are published. Only equal weighting is official.
- “Newly created” is mandatory, but the boundary between an allowed starter and other pre-existing work is not precise. Disclose when uncertain.
- No architecture-diagram file type or resolution is specified.

These ambiguities do not change the product scope: the safest submission is reproducible, public, explicit about provenance, and conservative about live-platform access.
