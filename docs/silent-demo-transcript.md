# Curate silent demo and transcript

Reference runtime: **about 3:06**, silent, 1440 by 900. The source-bound capture report records the exact final duration; inactive local-model wait time is removed by the recorder and disclosed there.

| Time | Silent screen action | Voiceover |
| --- | --- | --- |
| 0:00–0:25 | Pan through the archived YouTube and Bluesky dummy-account feeds before curation, with small labels attached to visible content. | Establish the real starting point and identify the recommendation to change. |
| 0:25–0:56 | Enter the broad request, paste bare links, then show the exact seven-topic mix. | Teach that descriptions are optional and both conversational and precise requests work. |
| 0:56–1:14 | Show Curate working, then the separate redacted AWS deployment check. | Explain the agent loop and distinguish the retained AgentCore/Bedrock proof from the active local demo run. |
| 1:14–1:40 | Run and measure the practice-feed plan, then restore its starting state. | Explain the content-level result and the tested way back. |
| 1:40–2:02 | Pan through both archived after feeds, then show direct before/after comparisons. | Point to the exact visible changes without claiming the captures prove the full percentage mix. |
| 2:02–2:21 | Choose the Copy Feed route and show its prepared result. | Teach what transfers and what needs a different route. |
| 2:21–2:42 | Issue, show, and revoke Incognito. | Teach purpose, duration, expiry, and immediate closure. |
| 2:42–3:05 | Create a Blend invitation, make the second selection, activate it, and stop it. | Teach the two-person flow and separate-account result. |
| 3:05–3:06 | Finish on the Curate cover. | Close with the product promise and return route. |

The final video must not show narration text burned into the product UI. Record the voiceover as WAV or high-bitrate MP3. Captions may be added during final editing as long as they do not cover the content cards or alter the meaning of the proof.

With the local API and verified loopback model already running, generate the capture with:

```powershell
$env:FEED_PASSPORT_DEMO_RECORDING_ACK="REVISE ONLY THE LOCAL DEMO PASSPORT"
$env:FEED_PASSPORT_PLATFORM_CAPTURE_MANIFEST="artifacts/local/platform-captures/manifest.json"
npm run demo:record
```

The script records a raw WebM for auditability and a condensed WebM for the submission. Its JSON report lists every removed wait interval, hashes the final video, records both natural-language goals, blocks non-loopback browser traffic, and can pass only after local-model planning, practice-feed execution, verified rollback, four reviewed scrolling platform captures, two direct comparisons, the separate managed AWS proof, complete Copy Feed/Incognito/Blend tutorials, and a two-to-four-minute final cut. The recorder itself accesses no social account, invokes no AWS service, and performs no platform action; it reads separately archived, reviewed evidence. Use a disposable local database because the flow revises one local Passport.
