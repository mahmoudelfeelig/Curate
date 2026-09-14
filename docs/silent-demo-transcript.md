# Curate silent demo and transcript

Reference runtime: **about 1:34**, silent, 1440 by 900. The source-bound capture report records the exact final duration; inactive local-model wait time is removed by the recorder and disclosed there.

| Time | Silent screen action | Voiceover |
| --- | --- | --- |
| 0:00–0:14 | Open on the Curate passport. | Introduce the problem, the people switching accounts or platforms, and the portable Passport. |
| 0:14–0:28 | Show the broad request and exact seven-topic mix. | Explain that Curate accepts natural language at either level of precision. |
| 0:28–0:45 | Show three YouTube and three Bluesky cards before and after. | Explain that descriptions are optional and the selected sample becomes visibly calmer and more varied. |
| 0:45–0:58 | Show the local Strands plan, practice run, measurement, and restored state. | Explain the bounded agent loop and rollback. |
| 0:58–1:11 | Preview Copy feed from YouTube to Bluesky. | Explain what carries over and what requires another route. |
| 1:11–1:22 | Issue, show, and revoke Incognito. | Explain temporary context, expiry, and immediate closure. |
| 1:22–1:28 | Show Blend. | Explain selected shared tastes without merging accounts. |
| 1:28–1:34 | Finish on the Curate cover. | Close with the product promise and return route. |

The final video must not show narration text burned into the product UI. Record the voiceover as WAV or high-bitrate MP3. Captions may be added during final editing as long as they do not cover the content cards or alter the meaning of the proof.

With the local API and verified loopback model already running, generate the capture with:

```powershell
$env:FEED_PASSPORT_DEMO_RECORDING_ACK="REVISE ONLY THE LOCAL DEMO PASSPORT"
npm run demo:record
```

The script records a raw WebM for auditability and a condensed WebM for the submission. Its JSON report lists every removed wait interval, hashes the final video, records both natural-language goals, blocks non-loopback browser traffic, and can pass only after local-model planning, practice-feed execution, verified rollback, visible starting and curated feed cards, and a sub-three-minute final cut. The local capture accesses no social account and performs no platform action. Use a disposable local database because the flow revises one local Passport.
