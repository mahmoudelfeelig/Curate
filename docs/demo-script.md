# Curate demo script

Reference runtime: **about 1:34**. The source-bound capture report is authoritative. The recording is an edited product walkthrough: it removes inactive local-model wait time but does not synthesize a result or hide any user decision.

## The story

Open on the Curate passport. The first screen should communicate the product in one glance: a person describes the feed they want, Curate turns it into portable preferences, and the agent translates those preferences into the controls a destination supports.

Move directly to **Tune my feed**. Enter the conversational request:

> I want less ragebait and more science-based pages.

Paste the six owner-written sample links from the recording script and capture the starting feed. Keep the visible content cards centered long enough to read their titles. The mix and metrics are supporting evidence; the content itself is the main before-state.

Save the proposed change to the Passport, then open the agent mission. Replace the goal with the precise request:

> Make it 50% astronomy, 15% coding, 12% drawing, 3% anime, 10% Naruto, 5% One Piece, and 5% perfumes.

Show the entire sentence, the interpreted mix, and the compact local-planning state. The final video keeps roughly one and a half seconds of that state, removes the rest of the inactive model wait, and resumes on the returned proposal. This is a presentation edit only; the receipt still comes from the verified loopback model.

Run the proposal on the **Practice feed**. Center the starting-versus-curated content cards and their headline metrics. The audience should be able to name what changed without reading an implementation receipt. Restore the starting state and pause on the successful restoration mark.

Return to **Tune my feed**, replace the input sample with the six after-links, and compare. Keep the three YouTube and three Bluesky cards visible in both columns. Continue through **Copy feed**, **Incognito**, and **Blend**, then return to the passport cover.

## Preflight

- Use a disposable local database.
- Start the pinned loopback model, local Curator API, and Vite client.
- Confirm `/health` reports a healthy API and active scheduler.
- Confirm `/api/agent/model/status` reports `ready`, `loopback_only`, and no paid or external model calls.
- Run the final integrated Node and Python checks.
- Open the browser at 1440 by 900 and confirm there is no horizontal clipping.

## Timed recording

| Time | Screen and operator action | Narration |
| --- | --- | --- |
| 0:00–0:14 | Open on Curate and its portable exact mix. | State the problem, audience, and portable-passport idea. |
| 0:14–0:28 | Enter the vague request, then the exact seven-topic request. | Explain that conversational and percentage-based intent both work. |
| 0:28–0:45 | Show the six-link YouTube and Bluesky sample, then both feed columns. | Describe the selected sample and visible content shift. |
| 0:45–0:58 | Show the local Strands plan, bounded practice run, measurement, and rollback. | Explain the agent loop and return route. |
| 0:58–1:11 | Preview **Copy feed** from YouTube to Bluesky and show translation loss. | Explain what carries over and what needs another route. |
| 1:11–1:22 | Issue and revoke **Incognito**. | Explain temporary context and expiry. |
| 1:22–1:28 | Show **Blend**. | Explain selected shared tastes without merging accounts. |
| 1:28–1:34 | End on the Curate passport. | Close with the product promise. |

## Recording boundary

The primary flow is local and account-free. It uses a real loopback model proposal, deterministic policy checks, a local practice feed, measurement, and rollback. It does not log into or change YouTube, Bluesky, or Instagram. Provider-specific live evidence belongs in a separately redacted receipt, not in this browser recording.

Only owner-authored text, live DOM/CSS, and the supplied Curate elephant mark may appear. Do not add generated artwork, stock screenshots, account identifiers, credentials, OAuth callbacks, AWS identifiers, private browser history, or private feed content.

Generate the capture with:

```powershell
$env:FEED_PASSPORT_DEMO_RECORDING_ACK="REVISE ONLY THE LOCAL DEMO PASSPORT"
npm run demo:record
```

The script records a raw WebM for auditability and a condensed WebM for the submission. Its report lists every removed wait interval and hashes the final video.

## Final review

Watch the condensed file at full size and on a laptop-sized player. Confirm that both feed columns are legible, the precise percentages total 100%, the conversational request remains unforced, the cut around model planning feels intentional, and there is no dead air longer than a few seconds. The submitted video must remain below the event limit and be publicly accessible during judging.
