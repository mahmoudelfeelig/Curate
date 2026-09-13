# Curate silent demo and transcript

Target: **1:45 to 2:15**, silent, 1440 by 900. The exact timing follows the UI response, while inactive local-model wait time is removed by the recorder and disclosed in its report.

| Time | Silent screen action | Voiceover |
| --- | --- | --- |
| 0:00–0:12 | Open on the Curate passport, then choose **Tune my feed**. | “Recommendation feeds learn us over years, but that work disappears when we open a new account or move platforms. Curate gives that history a portable shape.” |
| 0:12–0:35 | Type “I want less ragebait and more science-based pages.” Show the six starting-feed links, capture, and center the starting content cards. | “I can start loosely. These are a few examples from my starting feed. Curate separates what I supplied from what it inferred, then turns the request into a feed I can actually compare.” |
| 0:35–0:48 | Show the inferred preference mix, then save it to the Passport. | “A conversational request becomes useful preferences without forcing me to invent percentages.” |
| 0:48–1:08 | Open the agent mission and type the exact seven-topic, 100% request. Briefly show the planning animation; cut directly to the returned local-model proposal. | “It also understands precision. This complete one-hundred-percent mix still feels like a sentence, not a settings form. The local agent plans within the controls available for this destination.” |
| 1:08–1:28 | Run on the **Practice feed**. Center the visible starting and curated content cards, then their summary metrics. | “The difference is visible, not just a score. Outrage, drama, and unsupported claims give way to astronomy, programming, source-backed science, drawing, anime analysis, and fragrance chemistry.” |
| 1:28–1:38 | Restore the starting state and pause on the verified restoration mark. | “Every practice-feed change has a return route. One click restores the starting state and verifies the match.” |
| 1:38–1:58 | Return to **Tune my feed**, paste the after-sample, compare, and hold on both feed columns. | “Afterward, Curate compares the content itself and keeps the measurements and receipts available for inspection.” |
| 1:58–2:08 | End on the Curate passport cover. | “Curate is a passport for your taste: natural language in, a measurable feed out, and a clear translation for every platform that supports it.” |

The final video must not show narration text burned into the product UI. Record the voiceover as WAV or high-bitrate MP3. Captions may be added during final editing as long as they do not cover the content cards or alter the meaning of the proof.

With the local API and verified loopback model already running, generate the capture with:

```powershell
$env:FEED_PASSPORT_DEMO_RECORDING_ACK="REVISE ONLY THE LOCAL DEMO PASSPORT"
npm run demo:record
```

The script records a raw WebM for auditability and a condensed WebM for the submission. Its JSON report lists every removed wait interval, hashes the final video, records both natural-language goals, blocks non-loopback browser traffic, and can pass only after local-model planning, practice-feed execution, verified rollback, visible starting and curated feed cards, and a sub-three-minute final cut. The local capture accesses no social account and performs no platform action. Use a disposable local database because the flow revises one local Passport.
