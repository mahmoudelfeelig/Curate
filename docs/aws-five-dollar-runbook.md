# AWS five-dollar demonstration boundary

This runbook is for one short, owner-observed AgentCore demonstration. It minimizes exposure; it cannot guarantee a hard USD 5 cap. AWS Budgets sends alerts after billing data arrives, credits may have service or expiry restrictions, and deleting a stack does not retroactively remove charges.

## Required account state

- Sign in to the AWS Management Console for the deployable AWS account. AWS Builder ID alone is not an AWS account deployment credential.
- Use the dedicated `feed-passport-demo` IAM Identity Center profile. Do not reuse another project's profile and do not create a long-lived access key for this demo.
- Select `eu-north-1`, model `amazon.nova-lite-v1:0`, and the exact direct foundation-model ARN documented in the AgentCore runbook.
- Inspect the Billing **Credits** page without copying codes or account details into the repository.
- Create `FeedPassport-Five-Dollar-Guard` before any bootstrap or deploy. Use `infra/agentcore/scripts/configure-budget.ps1`; it creates 50% actual, 80% forecast, and 100% actual email alerts for a fixed USD 5 monthly budget.

## Minimal evidence session

Run the AWS read-only preflight first. If CDK bootstrap is absent, apply it once. Deploy one `FeedPassportAgentCore` stack, create one Cognito dummy user, and set seven-day Runtime log retention immediately after the first invocation creates the group.

Invoke only these requests:

- one `health` request to prove the managed Runtime/Gateway path;
- one `PlanFeed` request using `examples/plan-feed.json` after replacing its `owner_id` with the Cognito access token's `sub`.

Save only redacted response metadata: operation, proposal-only authority, tool sequence, provider/model identifier, latency/token evidence, timestamps, and stack revision. Never save the access token, account ID, Cognito subject, email address, or full Gateway URL in a committed artifact.

The current release owner chose to retain `FeedPassportAgentCore` for judging. Do not invoke it again as part of this runbook. Keep the fixed budget alerts enabled, inspect Billing again after metering catches up, and use `inventory.ps1` to review the retained resources. The documented destroy script remains an owner-operated cleanup option after judging; it is not part of the current release procedure. Leave the shared `CDKToolkit` stack unless the AWS account owner independently decides it is unused.

## Stop conditions

Stop before deployment if the dedicated SSO profile is unavailable, the exact account/Region/model preflight fails, the USD 5 budget is missing, the budget subscriber is unconfirmed, credits cannot be inspected, or any plan contains a VPC, NAT Gateway, DynamoDB table, Secrets Manager secret, token vault, workload identity, wildcard Bedrock model permission, or a second application stack.

Stop after the first failed live invocation. Diagnose from the managed status and local tests; do not retry a potentially billable request until its failure is understood. The local scripted-model suite is the iteration environment.
