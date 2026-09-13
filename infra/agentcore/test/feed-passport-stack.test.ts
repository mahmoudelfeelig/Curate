import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
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
  "arn:aws:bedrock:eu-north-1::foundation-model/amazon.nova-lite-v1:0";

before(() => {
  fixtureDirectory = mkdtempSync(path.join(tmpdir(), "feed-passport-agentcore-cdk-"));
  const artifactPath = path.join(fixtureDirectory, "feed-passport-agentcore.zip");
  writeFileSync(artifactPath, "deterministic synth fixture", "utf8");
  const app = new App();
  const stack = new FeedPassportAgentCoreStack(app, "TestStack", {
    env: { account: "111122223333", region: "eu-north-1" },
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
        "eu-north-1",
      ),
    );
    assert.throws(
      () => validateBedrockModelBinding("other-model", modelArn, "eu-north-1"),
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
          "arn:aws:bedrock:eu-north-1:111122223333:custom-model/amazon.nova-lite-v1:0",
          "eu-north-1",
        ),
      /direct foundation model/,
    );
    assert.throws(
      () =>
        validateBedrockModelBinding(
          "eu.amazon.nova-lite-v1:0",
          "arn:aws:bedrock:eu-north-1:111122223333:inference-profile/eu.amazon.nova-lite-v1:0",
          "eu-north-1",
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

  it("constrains AgentCore service roles to this account, Region, and resource type", () => {
    const roles = template.findResources("AWS::IAM::Role");
    const agentCoreRoles = Object.values(roles).filter((resource: any) =>
      JSON.stringify(resource.Properties.AssumeRolePolicyDocument).includes(
        "bedrock-agentcore.amazonaws.com",
      ),
    );
    assert.equal(agentCoreRoles.length, 2);
    const sourceArnPatterns: string[] = [];
    for (const resource of agentCoreRoles as any[]) {
      const statement = resource.Properties.AssumeRolePolicyDocument.Statement[0];
      assert.deepEqual(statement.Condition.StringEquals, {
        "aws:SourceAccount": { Ref: "AWS::AccountId" },
      });
      assert.ok(statement.Condition.ArnLike["aws:SourceArn"]);
      assert.match(
        JSON.stringify(statement.Condition.ArnLike["aws:SourceArn"]),
        /bedrock-agentcore/,
      );
      sourceArnPatterns.push(JSON.stringify(statement.Condition.ArnLike["aws:SourceArn"]));
    }
    assert.equal(sourceArnPatterns.some((pattern) => /:runtime\//.test(pattern)), true);
    assert.equal(sourceArnPatterns.some((pattern) => /:gateway\//.test(pattern)), true);
    assert.equal(sourceArnPatterns.some((pattern) => /:\*"/.test(pattern)), false);
  });

  it("binds Cognito JWT identity to gateway-only IAM runtime ingress", () => {
    template.hasResourceProperties("AWS::BedrockAgentCore::Gateway", {
      AuthorizerType: "CUSTOM_JWT",
      AuthorizerConfiguration: {
        CustomJWTAuthorizer: {
          AllowedScopes: ["feed-passport/invoke"],
          DiscoveryUrl: Match.objectLike({ "Fn::Join": Match.anyValue() }),
        },
      },
      InterceptorConfigurations: [
        {
          InterceptionPoints: ["REQUEST"],
          InputConfiguration: { PassRequestHeaders: true },
          Interceptor: { Lambda: { Arn: Match.anyValue() } },
        },
      ],
    });
    template.hasResourceProperties("AWS::BedrockAgentCore::Runtime", {
      NetworkConfiguration: { NetworkMode: "PUBLIC" },
      ProtocolConfiguration: "HTTP",
      LifecycleConfiguration: {
        IdleRuntimeSessionTimeout: 300,
        MaxLifetime: 1800,
      },
      RequestHeaderConfiguration: {
        RequestHeaderAllowlist: ["X-Feed-Passport-Actor"],
      },
      EnvironmentVariables: Match.objectLike({
        FEED_PASSPORT_BEDROCK_MODEL_ID: "amazon.nova-lite-v1:0",
        FEED_PASSPORT_BEDROCK_REGION: { Ref: "AWS::Region" },
      }),
    });
    const runtimes = template.findResources("AWS::BedrockAgentCore::Runtime");
    assert.equal(Object.values(runtimes)[0].Properties.AuthorizerConfiguration, undefined);
    template.resourceCountIs("AWS::BedrockAgentCore::ResourcePolicy", 1);
    const policies = template.findResources("AWS::BedrockAgentCore::ResourcePolicy");
    const policyText = JSON.stringify(Object.values(policies)[0].Properties.Policy);
    assert.match(policyText, /AllowOnlyGatewayRole/);
    assert.match(policyText, /DenyOtherPrincipals/);
    assert.match(policyText, /aws:PrincipalArn/);
    const functions = template.findResources("AWS::Lambda::Function");
    const interceptor = Object.values(functions).find((resource: any) =>
      String(resource.Properties.Description).includes("Gateway-validated Cognito JWT"),
    ) as any;
    assert.ok(interceptor);
    const code = String(interceptor.Properties.Code.ZipFile);
    assert.match(code, /EXPECTED_ISSUER/);
    assert.match(code, /EXPECTED_CLIENT_ID/);
    assert.match(code, /REQUIRED_SCOPE/);
    assert.match(code, /X-Feed-Passport-Actor/);
    assert.doesNotMatch(code, /console\.(?:log|info|debug|warn|error)/);
  });

  it("uses a direct Python 3.13 artifact and gateway-role runtime target", () => {
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
      CredentialProviderConfigurations: [{ CredentialProviderType: "GATEWAY_IAM_ROLE" }],
      TargetConfiguration: {
        Http: {
          AgentcoreRuntime: Match.objectLike({ Qualifier: "DEFAULT" }),
        },
      },
    });
    const targets = template.findResources("AWS::BedrockAgentCore::GatewayTarget");
    const runtimeTarget = Object.values(targets)[0].Properties.TargetConfiguration.Http
      .AgentcoreRuntime;
    assert.equal(runtimeTarget.Qualifier, "DEFAULT");
    assert.equal(runtimeTarget.Schema, undefined);
  });

  it("rejects an unreviewed HTTP Runtime schema without a policy engine", () => {
    const rendered = structuredClone(template.toJSON());
    const target = Object.values(
      rendered.Resources,
    ).find((resource: any) => resource.Type === "AWS::BedrockAgentCore::GatewayTarget") as any;
    target.Properties.TargetConfiguration.Http.AgentcoreRuntime.Schema = {
      Source: { InlinePayload: "{}" },
    };
    assert.throws(
      () => validateTemplate(rendered),
      /schema must remain absent until an AgentCore policy engine is configured/,
    );
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

  it("forces every apply to rebuild its artifact from the clean checkout", () => {
    const deployScript = readFileSync(
      path.join(__dirname, "..", "..", "scripts", "deploy.ps1"),
      "utf8",
    );
    const rejection = deployScript.indexOf('if ($Mode -eq "Apply" -and $SkipPackage)');
    const planning = deployScript.indexOf('& (Join-Path $PSScriptRoot "plan.ps1")');
    assert.ok(rejection >= 0);
    assert.ok(planning > rejection);
    assert.match(deployScript, /deployment must rebuild the artifact from this clean checkout/);
  });

  it("does not print the AWS account identifier during preflight", () => {
    const preflightScript = readFileSync(
      path.join(__dirname, "..", "..", "scripts", "preflight.ps1"),
      "utf8",
    );
    assert.doesNotMatch(preflightScript, /Write-Host[^\r\n]*identity\.Account/);
    assert.match(preflightScript, /Verified the expected AWS caller account/);
  });

  it("passes the deterministic local safety validator", () => {
    assert.doesNotThrow(() => validateTemplate(template.toJSON()));
  });
});
