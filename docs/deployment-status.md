# Curate deployment status

Curate is published on Cloudflare Pages at [curate.elfeel.me](https://curate.elfeel.me/). The production frontend is configured for the retained Amazon Cognito judge sign-in and the proposal-only Amazon Bedrock AgentCore Gateway in `eu-north-1`.

The owner-authenticated Curator API is published at `curate-api.elfeel.me`, and the AT Protocol metadata and callback boundary is published at `curate-oauth.elfeel.me`. Both use first-level hostnames covered by Cloudflare Universal SSL. The API and OAuth sidecar run as health-checked Docker services with `unless-stopped` restart policies; the retained named Cloudflare Tunnel uses the same restart policy. The OAuth hostname routes only health, client metadata, JWKS, and callback paths. Private sidecar routes terminate at the tunnel with `404`.

The final managed deployment completed successfully and the stack resources are intentionally retained. One managed health invocation returned a healthy, proposal-only runtime with mutation tools disabled. One `PlanFeed` invocation returned a valid feed-goal proposal, preserved the complete seven-topic percentage mix, and reported both `approved=false` and `executed=false`.

Those were the only final managed invocations authorized for release verification. They are not repeated by the normal test suite. Redacted summaries belong in submission material; full receipts remain in the ignored local evidence directory.

After the required private judge sign-in, the public workspace supports two evidence paths:

- The Curate Lab path needs no social-media account. It compares visible feed cards and metrics before and after a vague or percentage-based request, then rolls the practice change back.
- The retained managed path lets a signed-in judge request a proposal. The managed agent can interpret and explain a goal but cannot approve, execute, or roll back platform actions.
- The Authorization Desk can initiate YouTube and Bluesky OAuth through the public API. A successful connection remains owner-bound and does not bypass the exact-revision certification and one-run action boundaries.

The current release boundary remains deliberate: local twins prove the orchestration and measurement loop, while live YouTube and Bluesky writes require owner-bound credentials plus an exact-revision conformance certificate. Instagram supports the supplied export intake and guided native controls; it does not claim an unsupported consumer-feed write API.
