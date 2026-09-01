# Design QA

## Status

**PASSED for the evidence manifest's recorded local prototype snapshot.** Those committed receipts do not automatically certify later commits. A final-current claim requires rerunning the matrix after the final commit and rebinding the evidence manifest. This is not a claim of full WCAG conformance or live social-platform behavior.

## Visual source and implementation contract

- Visual direction: a tactile 2009–2013 passport and customs-desk utility.
- Public visual source of truth: the live DOM/CSS implementation and curated browser captures.
- Curated final comparison captures: `artifacts/evidence/screenshots/overview-1440x1024.png`, `artifacts/evidence/screenshots/feature-clerk-proposal.png`, and `artifacts/evidence/screenshots/agent-390x844.png`.
- The implementation retains the navy cloth field, open cream passport, paper grain, ink stamps, dense early-2010s travel-document typography, colored desk tabs, customs consent ticket, and coral action language, while every label and control is driven by live application state.
- Only the licensed and provenance-tracked imagery listed in `THIRD_PARTY_NOTICES.md` is stored, served from `public/`, or emitted in the build.
- Shipped textures, fonts, and icons are provenance-tracked in `THIRD_PARTY_NOTICES.md`: CC0 Poly Haven and ambientCG textures, OFL 1.1 fonts, and Lucide icons under ISC/MIT.

## Final browser environment

| Field | Value |
| --- | --- |
| Browser | Local Google Chrome `151.0.7922.175` |
| Host | `win32-x64`; Node `v24.11.0` |
| Preview | Source-bound fixture matrix on isolated loopback port `http://127.0.0.1:15174`; the deterministic and model-backed service runs used their own loopback ports |
| Network policy | Only local origins permitted; external requests blocked and recorded |
| Density | DPR 1; the `195 x 422` run represents effective 200% reflow from a `390 x 844` mobile viewport |
| Manifest-bound passive report | `artifacts/evidence/browser/final-passive-v2/report.json` |
| Report SHA-256 | `22E07A9D50AA5FA3C77E3FEAC24E754FC7F059F32D83457B64041BC0E766D739` |

## Render and responsive matrix

For the manifest's recorded source snapshot, all twelve desks, including Feature Clerk, were captured at `1440 x 1024`, `1024 x 768`, `768 x 1024`, and `390 x 844`. Overview and Agent mission were additionally captured at `195 x 422` effective 200% reflow. That run produced 50 screenshots; the committed report retains every screenshot path and all validation results, with representative captures curated beside it.

The final passive run recorded:

- zero console warnings/errors, page errors, failed requests, or external network attempts;
- zero root horizontal overflow, horizontally clipped nodes, clipped text, rollback-tab intersections, duplicate IDs, missing image alternatives, unlabeled controls, or sub-24px effective hit regions;
- 175 inspected keyboard stops; the skip link was first and targeted `#workspace` in every viewport;
- browser Back restored Constitution from Agent mission at all four normal viewports;
- 61 reduced-motion scroll calls, all using `behavior: auto` and none using smooth scrolling;
- both licensed fonts loaded at every inspected state.

## Interaction evidence

`artifacts/evidence/browser/final-fixture-flow-v2/report.json` passed ten fixture scenarios and retained twelve screenshot paths. It covers:

- Passport issuance, invalid/valid Constitution totals, save, and destination selection;
- migration capture, non-mutating preview, translation-loss disclosure, and simulated apply;
- temporary mode issue/revoke and companion blend issue/revoke;
- drift check, correction, alert-only monitor, emergency stop, creator preservation, and template loading;
- checkpoint create/restore plus strict export/import;
- Agent mission preview, disabled execution before consent, one-time approval, bounded run, iteration ledger, receipts, desktop/mobile rollback, cancellation, and verified fixture restoration;
- real keyboard traversal into visually hidden radio and file inputs with visible parent focus styling;
- all six WebMCP tools at the highest-priority registry seam, with no approval, execution, run-mission, account, or network authority.

`artifacts/evidence/browser/final-deterministic-service/report.json` independently passed eleven assertions against a disposable SQLite database with model inference explicitly disabled. It remained in Local service mode, showed Feature Clerk as disabled instead of fabricating AI, required two deliberate local principals in isolated browser contexts, refreshed the active Companion when a source Passport changed, revoked it cleanly, persisted one local YouTube control-twin pass and receipt, then completed a separately approved rollback with `state_restored: true` using `local_twin_control_state_sha256`. It emitted no console, page, request, or external-network failures.

`artifacts/evidence/browser/final-official-b8184-model/report.json` exercised both rendered agent protocols with genuine local planning. Feature Clerk called `inspect_selected_passport`, `inspect_safe_feature_catalog`, and `submit_temporary_visa_proposal`, used 5,491 tokens, displayed only deterministic server-rendered prose, left stable service state unchanged, and made zero API requests when Apply merely pre-filled the Temporary desk. The separate mission planner called exactly `inspect_selected_passport`, `inspect_selected_control_surface`, and `submit_mission_proposal`; its plan used 3,521 tokens. Server-side validation reduced the action families to the allowed subset, execution stayed disabled until one-time consent, and the separately approved rollback restored the exact pre-run local control-state fingerprint. The report records zero paid or external model calls, console errors, page errors, failed requests, blocked requests, or external network attempts.

Compact copies of these reports live under `artifacts/evidence/browser/`. The committed [evidence manifest](artifacts/evidence/manifest.json) binds every curated file and screenshot to one exact tracked source snapshot using canonical Git-blob bytes, independent of checkout line endings. They are archived reference receipts until regenerated and rebound after the final commit.

## Comparison and fix history

- Pass 01 exposed narrow-root overflow, rollback overlay collisions, hidden-input focus gaps, and an accessibility-visible decorative watermark.
- Pass 02 confirmed the layout fixes and exposed loopback CORS failures when the service-backed preview used `127.0.0.1`.
- Passes 03-05 confirmed the CORS fix, skip link, live status, history routing, reduced-motion behavior, input labels, and effective 200% reflow.
- The fixture interaction pass then exposed a mobile Agent ledger defect that passive overflow checks could not see: an overly broad direct-span selector put phase copy in the 22px icon column. The selector was separated by role, and the final flow asserts at least 60% usable content width for every mobile phase row.
- The 1024px Companion ticket now uses shrinkable columns and wraps long synthetic Passport IDs. Operational labels and status marks were enlarged, labeled input hit regions are measured through their owning labels, and the 195px activity/archive layout wraps without clipping.
- The final integrated passive run first exposed two stale QA assumptions after modularization: Vite's development connection made `networkidle` inappropriate, and the new Companion title/Feature Clerk desk were absent from the expected section map. The runner now waits for the actual workbench and covers all twelve desks.
- The first final model browser run also proved that repeated platform reads change only `health.checked_at`; the mutation fingerprint now excludes exactly that observation timestamp while comparing every stable field. A direct before/after diff confirmed no Feature Clerk write.
- For their manifest-bound source snapshot, the passive, fixture, deterministic-service, and genuine-local-model reports contain no remaining P0, P1, or P2 rendered defect found by that audit.

## Limits

The browser checks combine rendered inspection, keyboard traversal, accessible-name heuristics, ARIA snapshots, a targeted CSS-token contrast assertion, network/console capture, and visual comparison. They do not replace a complete assistive-technology matrix, manual screen-reader study, or formal WCAG audit. External social accounts, OAuth, and live-platform recommender behavior were deliberately not exercised.

Manifest-snapshot browser-design verdict: **PASSED for the account-free demo and its declared evidence boundaries at that exact source snapshot. Re-run before making a final-current verdict.**
