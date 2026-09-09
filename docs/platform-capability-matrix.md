# Platform capability matrix

Capability labels are evidence levels, not marketing tiers.

- **Live closed loop:** authorized observe, execute, sample, and rollback pass live conformance against the named external service.
- **Local control loop:** observe, execute, sample, and rollback pass deterministic conformance only inside the explicitly labeled Lab or platform twin.
- **Executable:** authorized mutations work, but the recommendation surface cannot be verified completely.
- **Guided:** the agent compiles declared native steps; a handoff receipt can record only whether the user says each step was completed, skipped, or unavailable. It is not API or recommendation-outcome verification.
- **Lab:** deterministic simulation or public ranking code only.
- **Unavailable:** no authorized path for the requested behavior.

| Platform | Certified runtime level | Documented candidate surface | Defensible role |
| --- | --- | --- | --- |
| Feed Passport Lab | Lab | Full deterministic surface | End-to-end migration, overlay, blend, drift, expiry, evaluation, and exact rollback reference implementation. |
| Bluesky | Guided | Implemented live candidate for bounded account controls | The official OAuth/DPoP sidecar and opaque Python bridge cover follow/unfollow, actor mute/unmute, and muted-word add/remove. An internal local-only agent can prioritize all certified action families before deterministic budgeted target selection, but it has no owner-authenticated public API route and generic migrations cannot execute the live adapter. Loading still requires durable HTTPS deployment, an authorized dummy account, and a fresh signed conformance receipt. Custom feeds and ranking state are not copied. |
| X | Guided | Implemented live candidate plus pinned public-code reference | Owner-bound follows and mutes are implemented, but X API use is pay-per-use and therefore disabled under zero spend. The Phoenix tree remains an offline research reference; no generator, training, ranking, serving, or live-feed run is certified. |
| YouTube | Guided | Implemented live candidate for subscriptions | Owner-bound subscription list/insert/delete maps to subscribe/unsubscribe. An internal local-only agent can prioritize all certified action families before deterministic budgeted target selection, but it has no owner-authenticated public API route and generic migrations cannot execute the live adapter. No dummy account or signed live receipt exists; Home ranking, watch history, and native recommendation feedback are neither read nor written through this adapter. |
| Reddit | Guided | Implemented live candidate pending provider approval | Owner-bound subscribe/unsubscribe transport is implemented, but explicit Reddit approval, a dummy account, scopes, approval reference, and signed conformance receipt remain mandatory. |
| Instagram | Guided | Local following import plus Guided handoff | The opt-in loopback importer reads only user-supplied Accounts Center-format `following.json`, retains no raw upload, and adds only explicitly selected handles to Passport creator intent. It stores a normalized selected-subset digest and does not authenticate the file with Meta. Exact native follow/unfollow, mute/unmute, and hidden-word steps can be user-resolved, but their receipt always reports zero API writes and zero verified recommendation outcomes. No consumer recommendation write API is claimed. |
| Facebook | Guided | Guided | Native Favorites, snooze, unfollow, and feedback steps only; no News Feed mutation API is claimed. |
| Threads | Guided | Guided | Native Your Algo controls where available; no undocumented trigger behavior is claimed. |
| TikTok | Guided | Guided plus eligible import | Portability is approval- and region-gated; Manage Topics remains a native user control. |
| LinkedIn | Guided | Guided plus eligible import | Regional portability and native unfollow controls only; no consumer feed-control writes are claimed. |
| Snapchat | Guided | Guided | Native controls only; Login Kit does not grant recommendation or friend-graph authority. |

Every unconfigured external adapter intentionally returns a `guided` runtime manifest even where official documentation or a dormant transport describes a stronger candidate surface. `documented_capabilities()`, transport source, OAuth registration, and a selected UI card cannot authorize execution. A runtime may load only the exact action subset in a fresh signature-, expiry-, revision-, owner-, dummy-account-, and provider-approval-validated conformance receipt. Generic migration authority still rejects live-adapter execution; only the internal owner-authenticated commission boundary is implemented for that path, and it has no public routes. Test accounts do not waive platform rules.

These boundaries were rechecked on 9 September 2026 against the official [YouTube subscriptions resource](https://developers.google.com/youtube/v3/docs/subscriptions), [AT Protocol OAuth specification](https://atproto.com/specs/oauth), [Bluesky API reference](https://docs.bsky.app/docs/api/app-bsky-graph-get-follows), and [Meta-published Instagram API collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api). The local Instagram parser consumes a user-supplied portability export; it is not an Instagram API integration or OAuth session.
