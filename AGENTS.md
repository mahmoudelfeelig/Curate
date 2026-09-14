# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the current interface no longer matches the goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.

## Durable Feed Passport decisions

- The public product name is **Curate**. Keep `feed_passport` only for established protocol, package, schema, and infrastructure identifiers where renaming would break compatibility.
- Use the owner-supplied red elephant artwork as the Curate brand mark, favicon, masthead icon, and passport-page watermark. Do not replace it with generated or approximate artwork.
- Make the visible before/after feed the main proof in the agent journey. Keep implementation evidence available in optional receipt details rather than leading with it.
- Prefer plain-language labels such as "practice feed", "starting feed", and "tune my feed" over internal terms such as "control twin", "seeded scenario", or "agent ledger" in the primary UI.
- During model work, show one compact waiting state; do not expose internal tool stages while the user has no action to take.
- Keep the demo baseline deliberately short. Give the transformed feeds more screen time, select frames that make the requested shift visually unmistakable, and attach compact labels only to content they accurately describe.

- The public visual source of truth is the live DOM/CSS implementation and curated browser captures; retain no unprovenanced visual references.
- Ship no AI-generated or unprovenanced images. Every raster, font, and icon must have a compatible license and an entry in `THIRD_PARTY_NOTICES.md`; the UI should use live DOM and CSS for tickets, stamps, status, charts, and stateful controls.
- Preserve the 2009-2013 travel-document desktop utility: navy cloth work surface, open paper passport, destination stamps, customs declaration, perforated tickets, coral action accents, muted green, customs yellow, and typewriter details.
- Avoid contemporary AI-dashboard conventions, glass cards, purple gradients, chat-first interaction, oversized rounded corners, emoji, and decorative sci-fi agent imagery.
- The visual metaphor maps to real domain objects: constitution = portable preference policy, visa = platform application, stamp = certified capability/result, customs declaration = consent, travel companion = explicit shared slice, temporary visa = expiring overlay, return ticket = rollback, and translation loss = unsupported intent.
- Topic composition totals 100%; serendipity and outrage are cross-cutting constraints and are never included in that sum.
- Do not call an adapter `Direct` unless its conformance tests prove observe, authorized execute, sample, and rollback. Use `Executable`, `Guided`, `Lab`, or `Unavailable` honestly.
- Do not automate likes, comments, posts, reposts, direct messages, or other public engagement to train a feed. Credentials never enter model context.
- The domain layer has no Strands, AWS, HTTP, database, or platform imports. Deterministic policy owns authorization, action budgets, idempotency, expiry, consent, and rollback.
- One Strands coordinator may propose bounded actions through tools. Deterministic guards validate every tool request, and an independent evaluator decides whether to stop, adapt, or request human judgment.
- The Agent Desk is a structured mission console, not a regex chat router. A mission must expose observe, evaluate, plan, consent, execute, re-observe, adapt-or-stop, receipts, and rollback.
- Local platform control twins use `twin:<platform>` IDs and prove orchestration against simulated control surfaces only. They never claim private ranking fidelity or real account access.
- Every feature operates on a versioned `FeedPassport`: migration, temporary overlays, partner blends, templates, checkpoints, drift correction, creator continuity, and platform translation.
- The current interface is desktop-first and must remain legible on narrow screens by stacking sections rather than shrinking the entire canvas.
- Keep all existing Product Design hosting/runtime files intact. The Python service lives under `services/curator`; shared JSON contracts live under `contracts`.
