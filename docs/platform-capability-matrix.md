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
| Bluesky | Guided | Executable candidate | AT Protocol account controls and custom feeds are compiled, but live transport remains disabled until an authorized conformance run passes. |
| X | Guided | Executable candidate plus pinned public-code reference | Account controls are compiled from official documentation. The current Phoenix source tree is commit-bound and inspected through a restrictive generator-only bridge, but no generator, training, ranking, serving, or live-feed run is certified. |
| YouTube | Guided | Executable candidate for subscriptions | Subscription changes and native feedback steps are compiled; Home ranking and recommendation feedback are not writable through this adapter. |
| Reddit | Guided | Guided pending approval | API access depends on explicit Reddit approval; native community and mute steps remain user handoffs. |
| Instagram | Guided | Guided | Native user steps and official portability data only; no consumer recommendation write API is claimed. |
| Facebook | Guided | Guided | Native Favorites, snooze, unfollow, and feedback steps only; no News Feed mutation API is claimed. |
| Threads | Guided | Guided | Native Your Algo controls where available; no undocumented trigger behavior is claimed. |
| TikTok | Guided | Guided plus eligible import | Portability is approval- and region-gated; Manage Topics remains a native user control. |
| LinkedIn | Guided | Guided plus eligible import | Regional portability and native unfollow controls only; no consumer feed-control writes are claimed. |
| Snapchat | Guided | Guided | Native controls only; Login Kit does not grant recommendation or friend-graph authority. |

Every credential-free external adapter intentionally returns a `guided` runtime manifest even where official documentation describes a stronger candidate API. The candidate surface is available separately through `documented_capabilities()` and cannot authorize execution. The matrix is updated only from official documentation and fresh conformance receipts. Test accounts do not waive platform rules.
