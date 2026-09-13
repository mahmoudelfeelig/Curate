# Silent demo and voiceover transcript

Target: about 2 minutes 30 seconds. Record the browser without audio at 1440×900. Keep pointer movement slow, pause on evidence labels, and never expose emails, account handles, OAuth tokens, AWS account IDs, or private feed content.

| Time | Silent screen action | Voiceover |
| --- | --- | --- |
| 0:00–0:15 | Open the Passport overview, then the Constitution. | “Recommendation feeds take years to tune, then disappear when we switch accounts or platforms. Feed Passport turns what I want into a portable, inspectable policy.” |
| 0:15–0:48 | Open Feed evidence. Show three owner-selected links and the goal: “60% pet science, 20% cute drawing, keep the rest exploratory, and reduce ragebait.” Capture before and linger on each provenance badge. | “I select a few posts I actually saw. Feed Passport never claims to read the platform’s private algorithm. It keeps provider metadata, my notes, and deterministic inference separate. A request-bound Strands agent sees a sanitized version, calls exactly three proposal tools, and turns my language into measurable targets and guardrails.” |
| 0:48–1:03 | Show the target mix and YouTube, Bluesky, and Instagram translation cards. Approve and apply the Passport revision. | “Each platform gets only the controls it really supports, and missing capabilities stay visible. This consent revises my Passport only; it does not authorize a social action.” |
| 1:03–1:43 | Open Agent mission. Run the local model planner, show its three-tool trace and fixed action budget, approve the local twin run, inspect the measured after-state, then roll it back and show State verified. | “A second Strands planner proposes the control strategy. Deterministic code owns identity, budgets, approval, execution, measurement, and rollback. The local twin performs the complete loop, then proves the original control-state fingerprint was restored. It tests real agent behavior without impersonating a private recommender.” |
| 1:43–2:03 | Open Connected Agent, Temporary Visa, and Companion. | “Live YouTube or Bluesky actions need owner OAuth, a fresh dummy-account certification, and separate consent to exact reversible targets. Temporary and shared feeds reuse the same expiring, revocable policy boundary.” |
| 2:03–2:20 | Return to Feed evidence, replace the links with the after-sample, and click Compare after. | “After a change, I sample again. Feed Passport measures the association, reports uncertainty, and never calls a small sample proof of ranking causation.” |
| 2:20–2:30 | End on the Passport overview. | “One portable intent, honest capability translation, human consent, evidence, and rollback. Your feed, your rules, anywhere platforms permit.” |

The silent capture should include no narration text burned into the video beyond the product UI. Record the voiceover cleanly against this timing and send the audio as WAV or high-bitrate MP3; final editing can trim pauses and add captions without changing the underlying proof claims.

With the local API and verified loopback model already running, generate the source-free capture with:

```powershell
$env:FEED_PASSPORT_DEMO_RECORDING_ACK="REVISE ONLY THE LOCAL DEMO PASSPORT"
npm run demo:record
```

The capture blocks non-loopback browser requests, refuses an external or paid model, and writes its WebM video and JSON report under the operating-system temporary directory by default. Set `FEED_PASSPORT_DEMO_OUTPUT_DIR` to another non-repository output directory when needed. The report records that no social account was accessed and requires a local-model mission, local-twin execution, and verified rollback before it can pass. The flow does revise the selected local demo Passport once, so use a disposable local database for the final recording.
