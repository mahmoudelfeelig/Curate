# Curate silent demo and transcript

Reference runtime: **about 1:48**, silent, 1440 by 900. The source-bound capture report records the exact final duration; inactive local-model wait time is removed by the recorder and disclosed there.

| Time | Silent screen action | Voiceover |
| --- | --- | --- |
| 0:00–0:08 | Show the archived YouTube and Bluesky dummy-account feeds before curation. | Establish the real starting point on both platforms. |
| 0:08–0:22 | Open the Curate passport. | Introduce the problem, the people switching accounts or platforms, and the portable Passport. |
| 0:22–0:36 | Show the broad request and exact seven-topic mix. | Explain that Curate accepts natural language at either level of precision. |
| 0:36–0:52 | Paste bare YouTube and Bluesky links, then show the local plan and bounded practice run. | Explain that descriptions are optional and the agent translates the request into measurable changes. |
| 0:52–1:08 | Show the Curate comparison and the archived YouTube and Bluesky feeds after curation. | Point to the visibly different platform feeds and the measurable drop in unwanted content. |
| 1:08–1:22 | Preview Copy feed from YouTube to Bluesky and restore the practice feed. | Explain what carries over, what needs a different route, and the way back. |
| 1:22–1:36 | Issue, show, and revoke Incognito. | Explain temporary context, expiry, and immediate closure. |
| 1:36–1:42 | Show Blend. | Explain selected shared tastes without merging accounts. |
| 1:42–1:48 | Finish on the Curate cover. | Close with the product promise and return route. |

The final video must not show narration text burned into the product UI. Record the voiceover as WAV or high-bitrate MP3. Captions may be added during final editing as long as they do not cover the content cards or alter the meaning of the proof.

With the local API and verified loopback model already running, generate the capture with:

```powershell
$env:FEED_PASSPORT_DEMO_RECORDING_ACK="REVISE ONLY THE LOCAL DEMO PASSPORT"
$env:FEED_PASSPORT_PLATFORM_CAPTURE_MANIFEST="artifacts/local/platform-captures/manifest.json"
npm run demo:record
```

The script records a raw WebM for auditability and a condensed WebM for the submission. Its JSON report lists every removed wait interval, hashes the final video, records both natural-language goals, blocks non-loopback browser traffic, and can pass only after local-model planning, practice-feed execution, verified rollback, four reviewed real-platform captures, visible starting and curated feed cards, and a sub-three-minute final cut. The recorder itself accesses no social account and performs no platform action; it reads the separately archived dummy-account captures from the manifest. Use a disposable local database because the flow revises one local Passport.
