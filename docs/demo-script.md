# Curate demo script

Reference runtime: **about 3:37**. The source-bound capture report is authoritative. The recording is an edited product tutorial: it removes inactive local-model wait time but does not synthesize a result or hide any user decision.

## The story

Open on the Curate passport, then immediately establish the current feed. Every chapter follows the same tutorial pattern: show what exists, show what the person chooses, show Curate working, then show what changed.

Pan through the archived YouTube and Bluesky **before** captures edge to edge. Keep the small labels attached to the content as the frame moves, then move directly to **Tune my feed** and enter the conversational request:

> I want less ragebait and more science-based pages.

Paste the six sample links from the recording script and capture the starting feed. Links work on their own; this curated tutorial also includes short owner-authored notes so the offline classifier has explicit, reviewable semantics. Keep the visible content cards centered long enough to read their titles. The mix and metrics are supporting evidence; the content itself is the main before-state.

Save the proposed change to the Passport, then open the agent mission. Replace the goal with the precise request:

> Make it 50% astronomy, 15% coding, 12% drawing, 3% anime, 10% Naruto, 5% One Piece, and 5% perfumes.

Show the entire sentence, the interpreted mix, and the compact local-planning state. Keep the animated working state, remove only the remaining inactive model wait, and resume on the returned proposal. Show the retained AWS deployment check as a separate interstitial: it proves one earlier AgentCore health result and one Bedrock feed plan, while the active proposal in this recording still comes from the verified loopback model.

Run the proposal on the **Practice feed**. Scroll 930 pixels through six starting cards and six curated cards extracted from the rendered page. The curated cards must visibly cover astronomy, research, coding, drawing, anime, Naruto, One Piece, and perfumes. Center the headline metrics after the scroll so the audience can name what changed without reading an implementation receipt. Restore the starting state and pause on the successful restoration mark.

Return to **Tune my feed**, replace the input sample with the six after-links, and compare. Keep the three YouTube and three Bluesky cards visible in both columns. Pan through the archived YouTube and Bluesky **after** captures from the same dummy accounts, then place each platform's before and after frames next to each other. Continue through a prepared Copy Feed result, an issued and revoked Incognito feed, and a complete invitation/activation/stop Blend flow before returning to the passport cover.

## Preflight

- Use a disposable local database.
- Start the pinned loopback model, local Curator API, and Vite client.
- Confirm `/health` reports a healthy API and active scheduler.
- Confirm `/api/agent/model/status` reports `ready`, `loopback_only`, and no paid or external model calls.
- Confirm the capture manifest contains exactly one reviewed dummy-account home-feed screenshot for each YouTube/Bluesky before/after state, with a matching SHA-256 for every image. Every capture must be at least 800 by 450 pixels, and each platform's before/after files must be distinct.
- Run the final integrated Node and Python checks.
- Open the browser at 1440 by 900 and confirm there is no horizontal clipping.

## Timed recording

| Time | Screen and operator action | Narration |
| --- | --- | --- |
| 0:00–0:23 | Pan 930 pixels through the real YouTube and Bluesky dummy-account home feeds marked **BEFORE**. | Identify visible conflict-heavy content and the useful content to keep. |
| 0:23–1:04 | Enter the vague request, optional link notes, and exact seven-topic request. | Teach natural and percentage-based input. |
| 1:04–1:27 | Show the local agent state and the full nine-event retained AWS execution trace. | Explain planning and the managed AgentCore/Bedrock proof without conflating the two runs. |
| 1:27–2:09 | Show the returned plan, scroll through the six-card starting and curated practice feeds, measure the change, and roll back. | Make every requested subject and the 100-point calm-content shift visible. |
| 2:09–2:41 | Pan through both **AFTER** feeds and show direct platform comparisons. | Point out exactly which visible recommendations changed. |
| 2:41–2:53 | Prepare **Copy Feed** from YouTube to Bluesky. | Teach the route, preview, and result. |
| 2:53–3:07 | Issue and revoke **Incognito**. | Teach purpose, duration, expiry, and immediate closure. |
| 3:07–3:31 | Create, activate, and stop **Blend** with two local test principals. | Teach both selections and the separate-account result. |
| 3:31–3:37 | End on the Curate passport. | Close with the product promise. |

## Recording boundary

The active product flow is local and account-free. It uses a real loopback model proposal, deterministic policy checks, a local practice feed, measurement, and rollback. The recorder does not log into or change YouTube, Bluesky, or Instagram, and it does not invoke AWS. It presents owner-captured screenshots from previously reviewed dummy-account home feeds plus redacted receipts from a previously completed managed run, and binds every image to the recording report by SHA-256.

Only owner-authored text, live DOM/CSS, the supplied Curate elephant mark, and the four reviewed dummy-account feed captures may appear. Do not add generated artwork, stock screenshots, personal-account captures, credentials, OAuth callbacks, AWS identifiers, private browser history, or private feed content. Review each capture for email addresses, account IDs, notification contents, browser chrome, and unrelated history before setting `public_demo_reviewed` to `true`.

Store the four images and a manifest in an ignored local directory. The manifest format is:

```json
{
  "schema": "curate/platform-feed-capture-manifest/v1",
  "account_class": "dummy",
  "public_demo_reviewed": true,
  "captures": [
    {
      "platform": "youtube",
      "phase": "before",
      "captured_at": "2026-09-14T10:00:00.000Z",
      "file": "youtube-before.png",
      "sha256": "<lowercase SHA-256>",
      "page_kind": "home_feed",
      "source_origin": "https://www.youtube.com"
    }
  ]
}
```

Include the analogous `bluesky:before`, `youtube:after`, and `bluesky:after` entries. Each after timestamp must be later than its platform's before timestamp. Image paths must be relative to the manifest and stay inside its directory. The recorder rejects captures below 800 by 450 pixels and rejects a platform pair whose before and after hashes are identical.

Generate the capture with:

```powershell
$env:FEED_PASSPORT_DEMO_RECORDING_ACK="REVISE ONLY THE LOCAL DEMO PASSPORT"
$env:FEED_PASSPORT_PLATFORM_CAPTURE_MANIFEST="D:\path\to\reviewed-dummy-feed-captures\manifest.json"
npm run demo:record
```

The script records a raw WebM for auditability and a condensed WebM for the submission. Its report lists every removed wait interval, hashes the final video, verifies the 930-pixel feed tours, and requires the nine-event AWS replay and all seven requested topics.

## Final review

Watch the condensed file at full size and on a laptop-sized player. Confirm the actual YouTube and Bluesky pages are legible and labeled with the correct before/after state, both Curate feed columns are legible, the precise percentages total 100%, the conversational request remains unforced, the cut around model planning feels intentional, and there is no dead air longer than a few seconds. Re-time narration and captions against the new cut before finalizing. The submitted video must remain below the event limit and be publicly accessible during judging.
