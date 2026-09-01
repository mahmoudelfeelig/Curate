import * as path from "node:path";

import {
  Aws,
  CfnOutput,
  Duration,
  RemovalPolicy,
  Stack,
  StackProps,
  Tags,
} from "aws-cdk-lib";
import * as agentcore from "aws-cdk-lib/aws-bedrockagentcore";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3assets from "aws-cdk-lib/aws-s3-assets";
import { Construct } from "constructs";

export interface FeedPassportAgentCoreStackProps extends StackProps {
  readonly artifactPath: string;
  readonly bedrockModelId: string;
  readonly bedrockModelArn: string;
  readonly callbackUrls: string[];
  readonly logoutUrls: string[];
  readonly cognitoDomainPrefix: string;
}

const RUNTIME_NAME = "feed_passport_curator";
const GATEWAY_NAME = "feed-passport-gateway";
const REQUIRED_SCOPE = "feed-passport/invoke";

export function validateBedrockModelBinding(
  modelId: string,
  modelArn: string,
  expectedRegion: string,
): void {
  const normalizedId = modelId.trim();
  if (!normalizedId) {
    throw new Error("bedrockModelId must be explicit");
  }
  const match = /^arn:(aws(?:-us-gov|-cn)?):bedrock:([^:]+)::foundation-model\/(.+)$/.exec(
    modelArn,
  );
  if (!match) {
    throw new Error("bedrockModelArn must identify a direct foundation model");
  }
  const [, , arnRegion, arnModelId] = match;
  if (arnRegion !== expectedRegion) {
    throw new Error("bedrockModelArn region must match the deployment region");
  }
  if (arnModelId !== normalizedId) {
    throw new Error("bedrockModelArn resource must exactly match bedrockModelId");
  }
}

function runtimeSchema(): string {
  return JSON.stringify({
    openapi: "3.0.3",
    info: {
      title: "Feed Passport proposal-only AgentCore Runtime",
      version: "1.0.0",
    },
    paths: {
      "/invocations": {
        post: {
          operationId: "invokeFeedPassportPlanner",
          requestBody: {
            required: true,
            content: {
              "application/json": {
                schema: {
                  oneOf: [
                    {
                      type: "object",
                      additionalProperties: false,
                      required: ["kind"],
                      properties: { kind: { const: "health" } },
                    },
                    {
                      type: "object",
                      additionalProperties: false,
                      required: ["kind", "passport", "request"],
                      properties: {
                        kind: { const: "plan_feature" },
                        passport: { type: "object" },
                        request: { type: "string", minLength: 1, maxLength: 1200 },
                      },
                    },
                  ],
                },
              },
            },
          },
          responses: { "200": { description: "Health or bounded feature proposal" } },
        },
      },
    },
  });
}

export class FeedPassportAgentCoreStack extends Stack {
  constructor(scope: Construct, id: string, props: FeedPassportAgentCoreStackProps) {
    super(scope, id, props);

    validateBedrockModelBinding(props.bedrockModelId, props.bedrockModelArn, this.region);
    if (props.callbackUrls.length === 0 || props.logoutUrls.length === 0) {
      throw new Error("at least one Cognito callback and logout URL is required");
    }

    Tags.of(this).add("Project", "feed-passport");
    Tags.of(this).add("ManagedBy", "aws-cdk");
    Tags.of(this).add("CostProfile", "credits-only-delete-after-demo");

    const userPool = new cognito.UserPool(this, "UserPool", {
      userPoolName: "feed-passport-users",
      selfSignUpEnabled: false,
      signInAliases: { username: true, email: true },
      autoVerify: { email: true },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const invokeScope = new cognito.ResourceServerScope({
      scopeName: "invoke",
      scopeDescription: "Invoke the Feed Passport proposal-only agent",
    });
    const resourceServer = userPool.addResourceServer("ResourceServer", {
      identifier: "feed-passport",
      scopes: [invokeScope],
    });
    const userPoolClient = userPool.addClient("PublicSpaClient", {
      userPoolClientName: "feed-passport-spa",
      generateSecret: false,
      preventUserExistenceErrors: true,
      authFlows: { userSrp: true },
      enableTokenRevocation: true,
      accessTokenValidity: Duration.minutes(60),
      idTokenValidity: Duration.minutes(60),
      oAuth: {
        flows: { authorizationCodeGrant: true },
        callbackUrls: props.callbackUrls,
        logoutUrls: props.logoutUrls,
        scopes: [
          cognito.OAuthScope.OPENID,
          cognito.OAuthScope.resourceServer(resourceServer, invokeScope),
        ],
      },
      supportedIdentityProviders: [cognito.UserPoolClientIdentityProvider.COGNITO],
    });
    const userPoolDomain = userPool.addDomain("HostedDomain", {
      cognitoDomain: { domainPrefix: props.cognitoDomainPrefix },
    });
    const jwtIssuer = `https://cognito-idp.${Aws.REGION}.${Aws.URL_SUFFIX}/${userPool.userPoolId}`;
    const discoveryUrl = `${jwtIssuer}/.well-known/openid-configuration`;

    const runtimeRole = new iam.Role(this, "RuntimeRole", {
      assumedBy: new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com", {
        conditions: {
          StringEquals: { "aws:SourceAccount": Aws.ACCOUNT_ID },
          ArnLike: {
            "aws:SourceArn": `arn:${Aws.PARTITION}:bedrock-agentcore:${Aws.REGION}:${Aws.ACCOUNT_ID}:*`,
          },
        },
      }),
      description: "Proposal-only Feed Passport Runtime role for model invocation and telemetry",
    });
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "InvokeOnlyConfiguredBedrockModel",
        actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        resources: [props.bedrockModelArn],
      }),
    );
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "AgentCoreRuntimeLogs",
        actions: [
          "logs:CreateLogGroup",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:PutResourcePolicy",
        ],
        resources: [
          `arn:${Aws.PARTITION}:logs:${Aws.REGION}:${Aws.ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*`,
          `arn:${Aws.PARTITION}:logs:${Aws.REGION}:${Aws.ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*`,
        ],
      }),
    );
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "AgentCoreTelemetry",
        actions: [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords",
          "xray:GetSamplingRules",
          "xray:GetSamplingTargets",
        ],
        resources: ["*"],
      }),
    );
    runtimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "AgentCoreMetrics",
        actions: ["cloudwatch:PutMetricData"],
        resources: ["*"],
        conditions: { StringEquals: { "cloudwatch:namespace": "bedrock-agentcore" } },
      }),
    );
    const codeAsset = new s3assets.Asset(this, "RuntimeCode", {
      path: path.resolve(props.artifactPath),
    });
    codeAsset.grantRead(runtimeRole);

    const gatewayRole = new iam.Role(this, "GatewayRole", {
      assumedBy: new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
      description: "AgentCore Gateway service role; JWT passthrough adds no secret access",
    });
    const gateway = new agentcore.CfnGateway(this, "Gateway", {
      name: GATEWAY_NAME,
      description: "JWT-governed entry point for the Feed Passport proposal-only runtime",
      authorizerType: "CUSTOM_JWT",
      authorizerConfiguration: {
        customJwtAuthorizer: {
          discoveryUrl,
          allowedClients: [userPoolClient.userPoolClientId],
          allowedScopes: [REQUIRED_SCOPE],
        },
      },
      roleArn: gatewayRole.roleArn,
      tags: { Project: "feed-passport", Authority: "proposal-only" },
    });

    const runtime = new agentcore.CfnRuntime(this, "Runtime", {
      agentRuntimeName: RUNTIME_NAME,
      description: "Async proposal-only Strands feature planner; no mutation tools are exposed",
      agentRuntimeArtifact: {
        codeConfiguration: {
          code: {
            s3: {
              bucket: codeAsset.s3BucketName,
              prefix: codeAsset.s3ObjectKey,
            },
          },
          entryPoint: ["agentcore_main.py"],
          runtime: "PYTHON_3_13",
        },
      },
      roleArn: runtimeRole.roleArn,
      networkConfiguration: { networkMode: "PUBLIC" },
      protocolConfiguration: "HTTP",
      lifecycleConfiguration: {
        idleRuntimeSessionTimeout: 300,
        maxLifetime: 1800,
      },
      requestHeaderConfiguration: {
        requestHeaderAllowlist: ["Authorization"],
      },
      authorizerConfiguration: {
        customJwtAuthorizer: {
          discoveryUrl,
          allowedClients: [userPoolClient.userPoolClientId],
          allowedScopes: [REQUIRED_SCOPE],
          allowedWorkloadConfiguration: {
            hostingEnvironments: [{ arn: gateway.attrGatewayArn }],
          },
        },
      },
      environmentVariables: {
        FEED_PASSPORT_BEDROCK_MODEL_ID: props.bedrockModelId,
        FEED_PASSPORT_BEDROCK_REGION: Aws.REGION,
        FEED_PASSPORT_BEDROCK_TIMEOUT_SECONDS: "60",
        FEED_PASSPORT_JWT_ISSUER: jwtIssuer,
        UNIFIED_TRACES_DESTINATION_ENABLED: "true",
      },
      tags: {
        Project: "feed-passport",
        Authority: "proposal-only",
        SecurityPostDeploy: "require-mmdsv2",
      },
    });
    gatewayRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "InvokeOnlyFeedPassportRuntime",
        actions: ["bedrock-agentcore:InvokeAgentRuntime"],
        resources: [runtime.attrAgentRuntimeArn, `${runtime.attrAgentRuntimeArn}/*`],
      }),
    );

    const gatewayTarget = new agentcore.CfnGatewayTarget(this, "RuntimeGatewayTarget", {
      gatewayIdentifier: gateway.attrGatewayIdentifier,
      name: "curator-runtime",
      description: "Forwards the already validated user JWT to the proposal-only Runtime",
      credentialProviderConfigurations: [{ credentialProviderType: "JWT_PASSTHROUGH" }],
      targetConfiguration: {
        http: {
          agentcoreRuntime: {
            arn: runtime.attrAgentRuntimeArn,
            qualifier: "DEFAULT",
            schema: { source: { inlinePayload: runtimeSchema() } },
          },
        },
      },
    });
    gatewayTarget.addResourceDependency(gateway);
    gatewayTarget.addResourceDependency(runtime);

    new CfnOutput(this, "GatewayUrl", { value: gateway.attrGatewayUrl });
    new CfnOutput(this, "GatewayTargetName", { value: "curator-runtime" });
    new CfnOutput(this, "RuntimeId", { value: runtime.attrAgentRuntimeId });
    new CfnOutput(this, "RuntimeArn", { value: runtime.attrAgentRuntimeArn });
    new CfnOutput(this, "UserPoolId", { value: userPool.userPoolId });
    new CfnOutput(this, "UserPoolClientId", { value: userPoolClient.userPoolClientId });
    new CfnOutput(this, "CognitoIssuer", { value: jwtIssuer });
    new CfnOutput(this, "CognitoHostedUiBaseUrl", { value: userPoolDomain.baseUrl() });
    new CfnOutput(this, "MmdsV2Enforcement", {
      value: "deploy.ps1 performs and verifies the required post-deploy UpdateAgentRuntime",
      description:
        "CloudFormation Runtime currently omits metadataConfiguration; apply orchestration must finish it",
    });
  }
}
