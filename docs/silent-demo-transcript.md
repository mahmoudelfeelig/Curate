# Curate silent demo and transcript

Verified runtime: **1:02.891**, silent, 1440 by 900. The timing below matches the final cut; inactive local-model wait time is removed by the recorder and disclosed in its report.

| Time | Silent screen action | Voiceover |
| --- | --- | --- |
| 0:00–0:08 | Open on the Curate passport and its portable exact mix. | “Recommendation feeds learn us over years, then make us start over on every new account. Curate turns that history into something portable.” |
| 0:08–0:22 | Show “I want less ragebait and more science-based pages,” the selected sample links, and the interpreted mix. | “I can speak naturally. I give Curate a few posts from my feed. It reads only what I chose, separates evidence from inference, and builds a mix I can inspect.” |
| 0:22–0:32 | Save the conversational result and move into the practice run. | “That works for a broad request, without forcing me to invent percentages.” |
| 0:32–0:46 | Enter the exact seven-topic, 100% request and show the local agent planning within the selected app's controls. | “It also understands precision. The request stays a sentence; the agent turns it into a bounded plan for the selected app.” |
| 0:46–0:58 | Run the practice feed, then hold on the six starting cards beside the six curated cards. | “The result is visible: outrage and unsupported claims move out, while astronomy, programming, research, drawing, anime analysis, and fragrance chemistry move in.” |
| 0:58–1:03 | Show the restored passport state and finish on the Curate cover. | “Finally, Curate restores the starting state and verifies the match. Natural language in, a measurable feed out, and a return route if I change my mind.” |

The final video must not show narration text burned into the product UI. Record the voiceover as WAV or high-bitrate MP3. Captions may be added during final editing as long as they do not cover the content cards or alter the meaning of the proof.

With the local API and verified loopback model already running, generate the capture with:

```powershell
$env:FEED_PASSPORT_DEMO_RECORDING_ACK="REVISE ONLY THE LOCAL DEMO PASSPORT"
npm run demo:record
```

The script records a raw WebM for auditability and a condensed WebM for the submission. Its JSON report lists every removed wait interval, hashes the final video, records both natural-language goals, blocks non-loopback browser traffic, and can pass only after local-model planning, practice-feed execution, verified rollback, visible starting and curated feed cards, and a sub-three-minute final cut. The local capture accesses no social account and performs no platform action. Use a disposable local database because the flow revises one local Passport.
