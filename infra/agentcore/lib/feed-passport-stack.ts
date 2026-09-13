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
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
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
const ACTOR_HEADER = "X-Feed-Passport-Actor";

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
            "aws:SourceArn": `arn:${Aws.PARTITION}:bedrock-agentcore:${Aws.REGION}:${Aws.ACCOUNT_ID}:runtime/*`,
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
      assumedBy: new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com", {
        conditions: {
          StringEquals: { "aws:SourceAccount": Aws.ACCOUNT_ID },
          ArnLike: {
            "aws:SourceArn": `arn:${Aws.PARTITION}:bedrock-agentcore:${Aws.REGION}:${Aws.ACCOUNT_ID}:gateway/*`,
          },
        },
      }),
      description: "AgentCore Gateway service role; may invoke only the curated Runtime",
    });
    const interceptorLogGroup = new logs.LogGroup(this, "IdentityInterceptorLogs", {
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const identityInterceptor = new lambda.Function(this, "IdentityInterceptor", {
      description: "Derives a bounded subject from the Gateway-validated Cognito JWT",
      runtime: lambda.Runtime.NODEJS_22_X,
      handler: "index.handler",
      timeout: Duration.seconds(5),
      memorySize: 128,
      logGroup: interceptorLogGroup,
      environment: {
        EXPECTED_ISSUER: jwtIssuer,
        EXPECTED_CLIENT_ID: userPoolClient.userPoolClientId,
        REQUIRED_SCOPE,
      },
      code: lambda.Code.fromInline(`
"use strict";
exports.handler = async (event) => {
  const request = event && event.http && event.http.gatewayRequest;
  if (!request || event.interceptorInputVersion !== "1.0") {
    throw new Error("unsupported interceptor request");
  }
  const headers = request.headers || {};
  const authorizationEntry = Object.entries(headers).find(
    ([name]) => String(name).toLowerCase() === "authorization",
  );
  const authorization = authorizationEntry ? String(authorizationEntry[1]) : "";
  const match = /^Bearer ([A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+)$/.exec(authorization);
  if (!match) throw new Error("validated bearer token is missing");
  let claims;
  try {
    claims = JSON.parse(Buffer.from(match[1].split(".")[1], "base64url").toString("utf8"));
  } catch {
    throw new Error("validated bearer claims are malformed");
  }
  const scopes = typeof claims.scope === "string" ? claims.scope.split(/\\s+/) : [];
  if (
    claims.iss !== process.env.EXPECTED_ISSUER ||
    claims.client_id !== process.env.EXPECTED_CLIENT_ID ||
    claims.token_use !== "access" ||
    !scopes.includes(process.env.REQUIRED_SCOPE)
  ) {
    throw new Error("validated bearer claims do not match this deployment");
  }
  const subject = typeof claims.sub === "string" ? claims.sub.trim() : "";
  if (!subject || subject.length > 160 || subject.includes("\\0")) {
    throw new Error("validated bearer subject is unusable");
  }
  return {
    interceptorOutputVersion: "1.0",
    http: {
      transformedGatewayRequest: {
        headers: { "Content-Type": "application/json", "${ACTOR_HEADER}": subject },
        body: request.body,
      },
    },
  };
};
`),
    });
    identityInterceptor.grantInvoke(gatewayRole);
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
      interceptorConfigurations: [
        {
          interceptor: { lambda: { arn: identityInterceptor.functionArn } },
          interceptionPoints: ["REQUEST"],
          inputConfiguration: { passRequestHeaders: true },
        },
      ],
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
        requestHeaderAllowlist: [ACTOR_HEADER],
      },
      environmentVariables: {
        FEED_PASSPORT_BEDROCK_MODEL_ID: props.bedrockModelId,
        FEED_PASSPORT_BEDROCK_REGION: Aws.REGION,
        FEED_PASSPORT_BEDROCK_TIMEOUT_SECONDS: "60",
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
    const runtimeIngressPolicy = new agentcore.CfnResourcePolicy(
      this,
      "RuntimeIngressPolicy",
      {
        resourceArn: runtime.attrAgentRuntimeArn,
        policy: this.toJsonString({
          Version: "2012-10-17",
          Statement: [
            {
              Sid: "AllowOnlyGatewayRole",
              Effect: "Allow",
              Principal: { AWS: gatewayRole.roleArn },
              Action: "bedrock-agentcore:InvokeAgentRuntime",
              Resource: runtime.attrAgentRuntimeArn,
            },
            {
              Sid: "DenyOtherPrincipals",
              Effect: "Deny",
              Principal: { AWS: "*" },
              Action: "bedrock-agentcore:InvokeAgentRuntime",
              Resource: runtime.attrAgentRuntimeArn,
              Condition: {
                ArnNotEquals: { "aws:PrincipalArn": gatewayRole.roleArn },
              },
            },
          ],
        }),
      },
    );
    runtimeIngressPolicy.addResourceDependency(runtime);

    const gatewayTarget = new agentcore.CfnGatewayTarget(this, "RuntimeGatewayTarget", {
      gatewayIdentifier: gateway.attrGatewayIdentifier,
      name: "curator-runtime",
      description: "Signs the Gateway-validated, subject-bound request to the Runtime",
      credentialProviderConfigurations: [{ credentialProviderType: "GATEWAY_IAM_ROLE" }],
      targetConfiguration: {
        http: {
          agentcoreRuntime: {
            arn: runtime.attrAgentRuntimeArn,
            qualifier: "DEFAULT",
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
