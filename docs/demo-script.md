# Curate demo script

Target runtime: about **two minutes**, with the silent screen capture kept below three minutes. The recording is an edited product walkthrough: it removes inactive local-model wait time but does not synthesize a result or hide any user decision.

## The story

Open on the Curate passport. The first screen should communicate the product in one glance: a person describes the feed they want, Curate turns it into portable preferences, and the agent translates those preferences into the controls a destination supports.

Move directly to **Tune my feed**. Enter the conversational request:

> I want less ragebait and more science-based pages.

Paste the six owner-written sample links from the recording script and capture the starting feed. Keep the visible content cards centered long enough to read their titles. The mix and metrics are supporting evidence; the content itself is the main before-state.

Save the proposed change to the Passport, then open the agent mission. Replace the goal with the precise request:

> Make it 50% astronomy, 15% coding, 12% drawing, 3% anime, 10% Naruto, 5% One Piece, and 5% perfumes.

Show the entire sentence, the interpreted mix, and the compact local-planning state. The final video keeps roughly one and a half seconds of that state, removes the rest of the inactive model wait, and resumes on the returned proposal. This is a presentation edit only; the receipt still comes from the verified loopback model.

Run the proposal on the **Practice feed**. Center the starting-versus-curated content cards and their headline metrics. The audience should be able to name what changed without reading an implementation receipt. Restore the starting state and pause on the successful restoration mark.

Return to **Tune my feed**, replace the input sample with the six after-links, and compare. End with both content columns visible: the starting feed contains outrage and rumor-heavy examples; the curated feed contains astronomy, coding, source-backed science, drawing, anime analysis, and perfume chemistry. Then return to the passport cover.

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
| 0:00–0:12 | Open on Curate, then choose **Tune my feed**. | State the problem and the portable-passport idea. |
| 0:12–0:35 | Enter the vague request, capture the six-link sample, and center the starting content cards. | Explain that someone can begin conversationally. |
| 0:35–0:48 | Show the inferred mix and save it to the Passport. | Explain that Curate turns the request into usable preferences. |
| 0:48–1:08 | Enter the exact seven-topic request. Show the compact wait briefly, then cut to the returned local proposal. | Explain that precise percentages work too. |
| 1:08–1:28 | Run the **Practice feed** and center the visible starting and curated cards. | Describe the content shift before mentioning metrics. |
| 1:28–1:38 | Restore the starting state and pause on the verified mark. | Emphasize reversibility. |
| 1:38–1:58 | Compare the after-sample and hold on both feed columns. | Explain the measurable comparison. |
| 1:58–2:08 | End on the Curate passport cover. | Close with the product promise. |

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
