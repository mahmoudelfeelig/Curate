# Platform capability matrix

Capability labels are evidence levels, not marketing tiers.

- **Live closed loop:** authorized observe, execute, sample, and rollback pass live conformance against the named external service.
- **Local control loop:** observe, execute, sample, and rollback pass deterministic conformance only inside the explicitly labeled Lab or platform twin.
- **Executable:** authorized mutations work, but the recommendation surface cannot be verified completely.
- **Guided:** the agent compiles declared native steps and verifies only what an official interface exposes.
- **Lab:** deterministic simulation or public ranking code only.
- **Unavailable:** no authorized path for the requested behavior.

| Platform | Certified runtime level | Documented candidate surface | Defensible role |
| --- | --- | --- | --- |
| Feed Passport Lab | Lab | Full deterministic surface | End-to-end migration, overlay, blend, drift, expiry, evaluation, and exact rollback reference implementation. |
| Bluesky | Guided | Implemented live candidate for bounded account controls | The official OAuth/DPoP sidecar and opaque Python bridge cover follows, actor mutes, and muted words, but activation still requires durable HTTPS deployment, an authorized dummy account, and a fresh signed conformance receipt. Custom-feed/ranking state is not copied. |
| X | Guided | Implemented live candidate plus pinned public-code reference | Owner-bound follows and mutes are implemented, but X API use is pay-per-use and therefore disabled under zero spend. The Phoenix tree remains an offline research reference; no generator, training, ranking, serving, or live-feed run is certified. |
| YouTube | Guided | Implemented live candidate for subscriptions | Owner-bound subscribe/unsubscribe transport is implemented, but no dummy account or signed live receipt exists. Home ranking and native recommendation feedback are not writable through this adapter. |
| Reddit | Guided | Implemented live candidate pending provider approval | Owner-bound subscribe/unsubscribe transport is implemented, but explicit Reddit approval, a dummy account, scopes, approval reference, and signed conformance receipt remain mandatory. |
| Instagram | Guided | Guided | Native user steps and official portability data only; no consumer recommendation write API is claimed. |
| Facebook | Guided | Guided | Native Favorites, snooze, unfollow, and feedback steps only; no News Feed mutation API is claimed. |
| Threads | Guided | Guided | Native Your Algo controls where available; no undocumented trigger behavior is claimed. |
| TikTok | Guided | Guided plus eligible import | Portability is approval- and region-gated; Manage Topics remains a native user control. |
| LinkedIn | Guided | Guided plus eligible import | Regional portability and native unfollow controls only; no consumer feed-control writes are claimed. |
| Snapchat | Guided | Guided | Native controls only; Login Kit does not grant recommendation or friend-graph authority. |

Every unconfigured external adapter intentionally returns a `guided` runtime manifest even where official documentation or a dormant transport describes a stronger candidate surface. `documented_capabilities()`, transport source, OAuth registration, and a selected UI card cannot authorize execution. A runtime may promote only the exact action subset in a fresh signature-, expiry-, revision-, owner-, dummy-account-, and provider-approval-validated conformance receipt. Test accounts do not waive platform rules.
