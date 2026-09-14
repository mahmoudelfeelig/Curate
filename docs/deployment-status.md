# Curate deployment status

Curate is published on Cloudflare Pages at [curate.elfeel.me](https://curate.elfeel.me/). The production frontend is configured for the retained Amazon Cognito judge sign-in and the proposal-only Amazon Bedrock AgentCore Gateway in `eu-north-1`.

The final managed deployment completed successfully and the stack resources are intentionally retained. One managed health invocation returned a healthy, proposal-only runtime with mutation tools disabled. One `PlanFeed` invocation returned a valid feed-goal proposal, preserved the complete seven-topic percentage mix, and reported both `approved=false` and `executed=false`.

Those were the only final managed invocations authorized for release verification. They are not repeated by the normal test suite. Redacted summaries belong in submission material; full receipts remain in the ignored local evidence directory.

After the required private judge sign-in, the public workspace supports two evidence paths:

- The Curate Lab path needs no social-media account. It compares visible feed cards and metrics before and after a vague or percentage-based request, then rolls the practice change back.
- The retained managed path lets a signed-in judge request a proposal. The managed agent can interpret and explain a goal but cannot approve, execute, or roll back platform actions.

The current release boundary remains deliberate: local twins prove the orchestration and measurement loop, while live YouTube and Bluesky writes require owner-bound credentials plus an exact-revision conformance certificate. Instagram supports the supplied export intake and guided native controls; it does not claim an unsupported consumer-feed write API.
