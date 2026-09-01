import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import * as path from "node:path";
import { after, before, describe, it } from "node:test";

import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";

import {
  FeedPassportAgentCoreStack,
  validateBedrockModelBinding,
} from "../lib/feed-passport-stack";
import { validateTemplate } from "../scripts/validate-template";

let fixtureDirectory: string;
let template: Template;
const modelArn =
  "arn:aws:bedrock:eu-central-1::foundation-model/amazon.nova-lite-v1:0";

before(() => {
  fixtureDirectory = mkdtempSync(path.join(tmpdir(), "feed-passport-agentcore-cdk-"));
  const artifactPath = path.join(fixtureDirectory, "feed-passport-agentcore.zip");
  writeFileSync(artifactPath, "deterministic synth fixture", "utf8");
  const app = new App();
  const stack = new FeedPassportAgentCoreStack(app, "TestStack", {
    env: { account: "111122223333", region: "eu-central-1" },
    artifactPath,
    bedrockModelId: "amazon.nova-lite-v1:0",
    bedrockModelArn: modelArn,
    callbackUrls: ["http://127.0.0.1:5173/auth/callback"],
    logoutUrls: ["http://127.0.0.1:5173/"],
    cognitoDomainPrefix: "feed-passport-test-111122223333",
  });
  template = Template.fromStack(stack);
});

after(() => {
  rmSync(fixtureDirectory, { recursive: true, force: true });
});

describe("Feed Passport AgentCore stack", () => {
  it("binds the runtime model ID to the exact authorized ARN and deployment region", () => {
    assert.doesNotThrow(() =>
      validateBedrockModelBinding(
        "amazon.nova-lite-v1:0",
        modelArn,
        "eu-central-1",
      ),
    );
    assert.throws(
      () => validateBedrockModelBinding("other-model", modelArn, "eu-central-1"),
      /exactly match/,
    );
    assert.throws(
      () =>
        validateBedrockModelBinding(
          "amazon.nova-lite-v1:0",
          modelArn,
          "us-east-1",
        ),
      /region/,
    );
    assert.throws(
      () =>
        validateBedrockModelBinding(
          "amazon.nova-lite-v1:0",
          "arn:aws:bedrock:eu-central-1:111122223333:custom-model/amazon.nova-lite-v1:0",
          "eu-central-1",
        ),
      /direct foundation model/,
    );
    assert.throws(
      () =>
        validateBedrockModelBinding(
          "eu.amazon.nova-lite-v1:0",
          "arn:aws:bedrock:eu-central-1:111122223333:inference-profile/eu.amazon.nova-lite-v1:0",
          "eu-central-1",
        ),
      /direct foundation model/,
    );
  });

  it("has no VPC, NAT gateway, unused data plane, or public client secret", () => {
    template.resourceCountIs("AWS::EC2::VPC", 0);
    template.resourceCountIs("AWS::EC2::NatGateway", 0);
    template.resourceCountIs("AWS::DynamoDB::Table", 0);
    template.resourceCountIs("AWS::SecretsManager::Secret", 0);
    template.resourceCountIs("AWS::BedrockAgentCore::TokenVault", 0);
    template.resourceCountIs("AWS::BedrockAgentCore::WorkloadIdentity", 0);
    template.hasResourceProperties("AWS::Cognito::UserPoolClient", {
      GenerateSecret: false,
      AllowedOAuthFlows: ["code"],
      AllowedOAuthScopes: Match.arrayWith([
        "openid",
        Match.objectLike({ "Fn::Join": Match.anyValue() }),
      ]),
    });
  });

  it("does not grant proposal-only runtime access to unused identity or state services", () => {
    const policies = template.findResources("AWS::IAM::Policy");
    const actions = Object.values(policies).flatMap((resource: any) =>
      resource.Properties.PolicyDocument.Statement.flatMap((statement: any) =>
        Array.isArray(statement.Action) ? statement.Action : [statement.Action],
      ),
    );
    assert.equal(actions.some((action) => String(action).startsWith("dynamodb:")), false);
    assert.equal(actions.some((action) => String(action).startsWith("secretsmanager:")), false);
    assert.equal(
      actions.some((action) => String(action).startsWith("bedrock-agentcore:GetWorkload")),
      false,
    );
  });

  it("locks both gateway and runtime to Cognito JWT scope and gateway-only ingress", () => {
    template.hasResourceProperties("AWS::BedrockAgentCore::Gateway", {
      AuthorizerType: "CUSTOM_JWT",
      AuthorizerConfiguration: {
        CustomJWTAuthorizer: {
          AllowedScopes: ["feed-passport/invoke"],
          DiscoveryUrl: Match.objectLike({ "Fn::Join": Match.anyValue() }),
        },
      },
    });
    template.hasResourceProperties("AWS::BedrockAgentCore::Runtime", {
      NetworkConfiguration: { NetworkMode: "PUBLIC" },
      ProtocolConfiguration: "HTTP",
      LifecycleConfiguration: {
        IdleRuntimeSessionTimeout: 300,
        MaxLifetime: 1800,
      },
      AuthorizerConfiguration: {
        CustomJWTAuthorizer: {
          AllowedScopes: ["feed-passport/invoke"],
          AllowedWorkloadConfiguration: {
            HostingEnvironments: Match.arrayWith([
              { Arn: Match.objectLike({ "Fn::GetAtt": Match.anyValue() }) },
            ]),
          },
        },
      },
      EnvironmentVariables: Match.objectLike({
        FEED_PASSPORT_BEDROCK_MODEL_ID: "amazon.nova-lite-v1:0",
        FEED_PASSPORT_BEDROCK_REGION: { Ref: "AWS::Region" },
      }),
    });
  });

  it("uses a direct Python 3.13 artifact and JWT passthrough runtime target", () => {
    template.hasResourceProperties("AWS::BedrockAgentCore::Runtime", {
      AgentRuntimeArtifact: {
        CodeConfiguration: {
          EntryPoint: ["agentcore_main.py"],
          Runtime: "PYTHON_3_13",
        },
      },
    });
    template.hasResourceProperties("AWS::BedrockAgentCore::GatewayTarget", {
      Name: "curator-runtime",
      CredentialProviderConfigurations: [{ CredentialProviderType: "JWT_PASSTHROUGH" }],
      TargetConfiguration: {
        Http: {
          AgentcoreRuntime: Match.objectLike({ Qualifier: "DEFAULT" }),
        },
      },
    });
  });

  it("grants only the selected Bedrock model and never enables ForUserId identity", () => {
    const policies = template.findResources("AWS::IAM::Policy");
    const statements = Object.values(policies).flatMap((resource: any) =>
      resource.Properties.PolicyDocument.Statement,
    );
    const actions = statements.flatMap((statement: any) =>
      Array.isArray(statement.Action) ? statement.Action : [statement.Action],
    );
    assert.equal(actions.includes("bedrock-agentcore:GetWorkloadAccessTokenForUserId"), false);
    const bedrockStatement = statements.find((statement: any) =>
      (Array.isArray(statement.Action) ? statement.Action : [statement.Action]).includes(
        "bedrock:InvokeModel",
      ),
    );
    assert.ok(bedrockStatement);
    assert.deepEqual(bedrockStatement.Resource, modelArn);
    const gatewayRuntimeStatement = statements.find((statement: any) =>
      (Array.isArray(statement.Action) ? statement.Action : [statement.Action]).includes(
        "bedrock-agentcore:InvokeAgentRuntime",
      ),
    );
    assert.ok(gatewayRuntimeStatement);
    assert.equal(gatewayRuntimeStatement.Resource.length, 2);
    assert.equal(gatewayRuntimeStatement.Resource.includes("*"), false);
  });

  it("declares the mandatory MMDSv2 apply gate", () => {
    const rendered = template.toJSON();
    assert.match(
      rendered.Outputs.MmdsV2Enforcement.Value,
      /post-deploy UpdateAgentRuntime/,
    );
  });

  it("passes the deterministic local safety validator", () => {
    assert.doesNotThrow(() => validateTemplate(template.toJSON()));
  });
});
