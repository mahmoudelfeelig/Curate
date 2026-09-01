# Feed Passport AgentCore deployment

This directory packages the real proposal-only Strands feature planner for Amazon Bedrock AgentCore and defines its supporting AWS resources with CDK. It is deliberately plan-first: the local commands synthesize, validate, package, and test without contacting AWS. Every command that can change AWS defaults to `Plan` or `Validate` and requires an exact acknowledgement before its apply path runs.

There is no honest way to guarantee a zero-dollar AWS deployment. AgentCore Runtime/Gateway, Bedrock, Cognito, S3 assets, and CloudWatch are consumption-based services. Promotional credits and AWS Budgets are not hard caps. The repository therefore never deploys, bootstraps, invokes, or tears down AWS resources as part of a local test.

## Cloud boundary

The public cloud boundary exposes exactly two discriminated operations:

```json
{"kind":"health"}
```

```json
{"kind":"plan_feature","passport":{"...":"bounded versioned snapshot"},"request":"one bounded request"}
```

`plan_feature` derives its actor from the `sub` claim of the bearer token after AgentCore Runtime's custom JWT authorizer has validated it. The caller cannot supply an actor ID. The Passport owner must match that subject. The app exposes no `execute`, `approve`, `rollback`, browser-control, credential, or live-platform mutation tool.

The deployment path is:

```text
Cognito Authorization Code + PKCE access token
                  |
                  v
AgentCore Gateway custom JWT authorizer
                  |
             JWT passthrough
                  v
AgentCore Runtime custom JWT authorizer + gateway-only workload restriction
                  |
          async proposal-only Strands planner
                  |
        one explicitly configured Bedrock model
```

The stack deliberately omits AgentCore workload identity/token-vault resources, DynamoDB, and Secrets Manager because the current proposal-only Runtime does not consume them. The live-platform layer remains local and owner-bound until a later runtime actually integrates those services; unused future permissions and billable resources are not deployed speculatively.

No VPC or NAT Gateway is created. Runtime uses AgentCore-managed `PUBLIC` networking, a five-minute idle timeout, and a thirty-minute maximum lifetime. `resource-inventory.json` records each synthesized resource category plus service-created or bootstrap dependencies, their billing triggers, local substitutes, and teardown behavior.

## Runtime configuration contract

Cloud planning fails closed unless CDK sets all of these values:

| Variable | Required | Meaning |
| --- | --- | --- |
| `FEED_PASSPORT_BEDROCK_MODEL_ID` | yes | Exact direct foundation-model identifier |
| `FEED_PASSPORT_BEDROCK_REGION` | yes | Explicit AWS Region; there is no SDK/default-region fallback |
| `FEED_PASSPORT_JWT_ISSUER` | yes | Exact Cognito issuer accepted when reading the already validated JWT context |
| `FEED_PASSPORT_BEDROCK_TIMEOUT_SECONDS` | no | Planner timeout, default `60`, valid range `1..300` |

The model execution evidence always labels Bedrock as external and potentially billable. Local tests inject a scripted Strands `Model` and label it `scripted_no_network`; the existing llama.cpp path remains loopback-only. There is no silent provider fallback in either direction.

The current stack accepts only a direct foundation-model ARN. It deliberately rejects inference profiles because invoking a profile can require additional permissions for underlying destination models; the narrow one-ARN IAM interface cannot represent that safely.

The direct-code artifact requires Python 3.13 for Linux ARM64. Its entrypoint is `agentcore_main.py`. Packaging includes the curator project plus `bedrock-agentcore`, `strands-agents`, Pydantic, boto3, and their resolved dependencies. Application dependency declarations should keep boto3 explicit even though AgentCore/Strands currently pull it transitively.

## What is proven locally

The local suite executes the actual async Strands tool loop with a scripted, no-network model; validates JWT-subject ownership; rejects free text, spoofed actor IDs, and mutation-shaped commands; proves health performs no model construction; asserts the proposal-only IAM boundary and gateway-only ingress in the synthesized template; and rejects VPCs, NAT gateways, unused state/identity services, wildcard Bedrock model permissions, public client secrets, and workload-token permissions.

It cannot prove AWS account permissions, regional AgentCore availability, Cognito token validation by the managed service, Bedrock entitlement, Gateway-to-Runtime routing, credit coverage, or billing. Those require an AWS account and some checks require metered invocations.

Run the network-free source and CDK checks from this directory:

```powershell
./scripts/local-dry-run.ps1
```

The cross-platform deterministic template gate uses placeholder account/model identifiers and the checked-in entrypoint only; it does not read AWS credentials or call AWS:

```powershell
npm run validate:template
```

Build the deployable Linux ARM64 zip as an additional local check. Packaging fails unless every tracked and untracked file is committed or removed, and the embedded manifest is bound to the exact clean `HEAD`. Docker may download the mutable `python:3.13-slim` image and the version-ranged Python dependencies, so the dependency bytes are not reproducible until those inputs are digest- and lock-pinned; only the archive serialization is deterministic. This still makes no AWS call:

```powershell
./scripts/local-dry-run.ps1 -IncludeLinuxArm64Package
```

Create a full local CDK plan with explicit placeholders matching the intended account and model. Planning also fails on a dirty tree and validates an existing `-SkipPackage` artifact against the exact clean `HEAD`. It runs tests, packaging, synth, and the deterministic template validator:

```powershell
./scripts/plan.ps1 `
  -AwsAccountId 111122223333 `
  -AwsRegion eu-central-1 `
  -BedrockModelId amazon.nova-lite-v1:0 `
  -BedrockModelArn arn:aws:bedrock:eu-central-1::foundation-model/amazon.nova-lite-v1:0 `
  -CognitoDomainPrefix feed-passport-choose-a-unique-prefix
```

`compose.local.yml` can start the container for its unauthenticated health boundary. Cloud-mode planning is intentionally unavailable in that container because a locally invented JWT is not equivalent to Runtime validation.

## AWS access needed later

Use a dedicated least-privilege AWS CLI profile and never add access keys to `.env` or the repository. A conventional AWS Builder ID is a hackathon/community identity, not an IAM deployment credential. AWS documents the distinction in [AWS Builder ID and other AWS credentials](https://docs.aws.amazon.com/signin/latest/userguide/differences-builder-id.html). If the account is part of AWS's limited new experience, follow the credentials offered by that experience; otherwise use an AWS account role through IAM Identity Center or another account-approved CLI mechanism.

Before any write, the read-only preflight confirms the exact caller account, AgentCore control-plane visibility, and model metadata without invoking Runtime or Bedrock:

```powershell
./scripts/preflight.ps1 `
  -Mode AwsReadOnly `
  -AwsAccountId 111122223333 `
  -AwsRegion eu-central-1 `
  -AwsProfile feed-passport-demo `
  -BedrockModelId amazon.nova-lite-v1:0 `
  -BedrockModelArn arn:aws:bedrock:eu-central-1::foundation-model/amazon.nova-lite-v1:0
```

If `CDKToolkit` is absent, inspect the bootstrap command first:

```powershell
./scripts/bootstrap.ps1 `
  -AwsAccountId 111122223333 `
  -AwsRegion eu-central-1 `
  -AwsProfile feed-passport-demo
```

Applying bootstrap creates shared AWS resources and is therefore separately gated:

```powershell
./scripts/bootstrap.ps1 `
  -Mode Apply `
  -AwsAccountId 111122223333 `
  -AwsRegion eu-central-1 `
  -AwsProfile feed-passport-demo `
  -ApplyAcknowledgement "BOOTSTRAP FEED PASSPORT AWS" `
  -BillingAcknowledgement "AWS CREDITS ARE NOT A HARD SPEND CAP"
```

Deployment also defaults to a purely local plan. Its apply form uses the same plan first, verifies the account, requires an existing CDK bootstrap, deploys the stack, then immediately performs and verifies the mandatory `metadataConfiguration.requireMMDSV2=true` update. It does not invoke the Runtime or Bedrock.

```powershell
./scripts/deploy.ps1 `
  -Mode Apply `
  -AwsAccountId 111122223333 `
  -AwsRegion eu-central-1 `
  -AwsProfile feed-passport-demo `
  -BedrockModelId amazon.nova-lite-v1:0 `
  -BedrockModelArn arn:aws:bedrock:eu-central-1::foundation-model/amazon.nova-lite-v1:0 `
  -CognitoDomainPrefix feed-passport-choose-a-unique-prefix `
  -ApplyAcknowledgement "DEPLOY FEED PASSPORT AGENTCORE" `
  -BillingAcknowledgement "AWS CREDITS ARE NOT A HARD SPEND CAP"
```

MMDSv2 is a post-deploy gate because the current CDK/CloudFormation `AWS::BedrockAgentCore::Runtime` surface does not expose `MetadataConfiguration`, while AgentCore requires it for invocation. The apply script preserves the live Runtime configuration when sending `UpdateAgentRuntime`, waits for `READY`, then reads it back and fails unless `requireMMDSV2` is true. See AWS's [AgentCore Runtime security guidance](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-security-best-practices.html).

## Cognito dummy user and metered proof

The stack disables self-sign-up and creates a public Authorization Code + PKCE app client. Creating a dummy user defaults to a no-op plan, suppresses email delivery when applied, prompts for the password without echoing it, and removes a half-created user if password setup fails:

```powershell
./scripts/create-demo-user.ps1 `
  -Mode Apply `
  -AwsAccountId 111122223333 `
  -AwsRegion eu-central-1 `
  -AwsProfile feed-passport-demo `
  -Username demo-curator `
  -ApplyAcknowledgement "CREATE FEED PASSPORT DUMMY USER"
```

Use the stack's hosted-UI base URL, public client ID, and callback URL with Authorization Code + PKCE to obtain a short-lived access token containing the `feed-passport/invoke` scope. Do not paste the token into a file, command argument, issue, or chat transcript.

`smoke.ps1` validates its payload by default without a network call. Invoke mode prompts for the access token with hidden input. Even health starts an AgentCore Runtime and can incur charges. `PlanFeature` additionally calls Bedrock and needs its own acknowledgement:

```powershell
./scripts/smoke.ps1 `
  -Mode Invoke `
  -Operation Health `
  -GatewayUrl https://replace.gateway.bedrock-agentcore.eu-central-1.amazonaws.com `
  -InvokeAcknowledgement "INVOKE AGENTCORE MAY INCUR AWS CHARGES"
```

```powershell
./scripts/smoke.ps1 `
  -Mode Invoke `
  -Operation PlanFeature `
  -GatewayUrl https://replace.gateway.bedrock-agentcore.eu-central-1.amazonaws.com `
  -PayloadPath ./examples/plan-feature.json `
  -InvokeAcknowledgement "INVOKE AGENTCORE MAY INCUR AWS CHARGES" `
  -BedrockAcknowledgement "INVOKE BEDROCK MAY INCUR AWS CHARGES"
```

The example Passport's `owner_id` must be replaced with the Cognito access token's `sub`. A successful response is still only a proposal. `consent_created`, `approved`, and `executed` remain false.

After the first explicitly approved invocation creates the Runtime log group, set short retention through the separately gated script:

```powershell
./scripts/set-retention.ps1 `
  -Mode Apply `
  -RuntimeId replace-with-stack-output `
  -AwsRegion eu-central-1 `
  -AwsProfile feed-passport-demo `
  -ApplyAcknowledgement "SET AGENTCORE LOG RETENTION"
```

## Inventory and teardown

The default inventory is local and labels itself as a plan:

```powershell
./scripts/inventory.ps1
```

The AWS read-only mode verifies the caller account and lists only the CloudFormation stack's resources and outputs. It does not invoke Runtime or Bedrock:

```powershell
./scripts/inventory.ps1 `
  -Mode AwsReadOnly `
  -AwsAccountId 111122223333 `
  -AwsRegion eu-central-1 `
  -AwsProfile feed-passport-demo
```

Teardown defaults to a no-op plan and targets only `FeedPassportAgentCore`. Apply permanently deletes the stack, its dummy Cognito users, Runtime, Gateway, and other stack-owned resources. The shared `CDKToolkit` stack is intentionally preserved. Runtime logs and the exact S3 deployment object are outside application-stack ownership and have separate acknowledgements:

```powershell
./scripts/destroy.ps1 `
  -Mode Apply `
  -AwsAccountId 111122223333 `
  -AwsRegion eu-central-1 `
  -AwsProfile feed-passport-demo `
  -DeleteRuntimeLogGroup `
  -DeleteRuntimeAsset `
  -ApplyAcknowledgement "DESTROY FEED PASSPORT AGENTCORE" `
  -LogDeletionAcknowledgement "DELETE FEED PASSPORT RUNTIME LOGS" `
  -AssetDeletionAcknowledgement "DELETE FEED PASSPORT RUNTIME ASSET"
```

If the account is used by other CDK applications, never delete `CDKToolkit` just to clean up this demo. Review Billing and Cost Explorer after teardown; this repository cannot verify credit settlement or guarantee that an external billing system has reached zero.
