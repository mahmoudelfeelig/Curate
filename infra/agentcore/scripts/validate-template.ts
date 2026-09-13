import { readFileSync } from "node:fs";
import * as path from "node:path";

type JsonObject = Record<string, any>;

function fail(message: string): never {
  throw new Error(`unsafe AgentCore template: ${message}`);
}

function resourcesOf(template: JsonObject, type: string): JsonObject[] {
  return Object.values(template.Resources ?? {}).filter(
    (resource: any) => resource.Type === type,
  ) as JsonObject[];
}

function one(template: JsonObject, type: string): JsonObject {
  const resources = resourcesOf(template, type);
  if (resources.length !== 1) {
    fail(`expected exactly one ${type}, found ${resources.length}`);
  }
  return resources[0];
}

function actionsOf(statement: JsonObject): string[] {
  return Array.isArray(statement.Action) ? statement.Action : [statement.Action];
}

export function validateTemplate(template: JsonObject): void {
  for (const expensiveNetworkType of [
    "AWS::EC2::VPC",
    "AWS::EC2::NatGateway",
    "AWS::EC2::VPCEndpoint",
  ]) {
    if (resourcesOf(template, expensiveNetworkType).length !== 0) {
      fail(`${expensiveNetworkType} is forbidden in the zero-spend demo stack`);
    }
  }

  for (const unusedDataPlaneType of [
    "AWS::DynamoDB::Table",
    "AWS::SecretsManager::Secret",
    "AWS::BedrockAgentCore::TokenVault",
    "AWS::BedrockAgentCore::WorkloadIdentity",
  ]) {
    if (resourcesOf(template, unusedDataPlaneType).length !== 0) {
      fail(`${unusedDataPlaneType} is unused by the proposal-only runtime`);
    }
  }

  const client = one(template, "AWS::Cognito::UserPoolClient");
  if (client.Properties.GenerateSecret !== false) {
    fail("the browser Cognito client must be public and use no client secret");
  }
  if (!client.Properties.AllowedOAuthFlows?.includes("code")) {
    fail("Cognito must use Authorization Code flow");
  }

  const gateway = one(template, "AWS::BedrockAgentCore::Gateway");
  const gatewayJwt = gateway.Properties.AuthorizerConfiguration?.CustomJWTAuthorizer;
  if (
    gateway.Properties.AuthorizerType !== "CUSTOM_JWT" ||
    !gatewayJwt?.AllowedScopes?.includes("feed-passport/invoke")
  ) {
    fail("Gateway must require the Cognito feed-passport/invoke scope");
  }

  const runtime = one(template, "AWS::BedrockAgentCore::Runtime");
  if (runtime.Properties.AuthorizerConfiguration !== undefined) {
    fail("Runtime must use IAM ingress behind the JWT-authorized Gateway");
  }
  const allowedHeaders =
    runtime.Properties.RequestHeaderConfiguration?.RequestHeaderAllowlist;
  if (
    !Array.isArray(allowedHeaders) ||
    allowedHeaders.length !== 1 ||
    allowedHeaders[0] !== "X-Feed-Passport-Actor"
  ) {
    fail("Runtime must accept only the Gateway-injected actor header");
  }
  if (runtime.Properties.NetworkConfiguration?.NetworkMode !== "PUBLIC") {
    fail("Runtime must use managed PUBLIC mode; this stack must not create a NAT or VPC");
  }
  if (runtime.Properties.LifecycleConfiguration?.IdleRuntimeSessionTimeout !== 300) {
    fail("Runtime idle timeout must remain five minutes");
  }
  if (runtime.Properties.LifecycleConfiguration?.MaxLifetime !== 1800) {
    fail("Runtime maximum lifetime must remain thirty minutes");
  }
  const code = runtime.Properties.AgentRuntimeArtifact?.CodeConfiguration;
  if (code?.Runtime !== "PYTHON_3_13" || code?.EntryPoint?.[0] !== "agentcore_main.py") {
    fail("Runtime must use the packaged Python 3.13 proposal-only entrypoint");
  }

  const target = one(template, "AWS::BedrockAgentCore::GatewayTarget");
  const credentialTypes = (target.Properties.CredentialProviderConfigurations ?? []).map(
    (configuration: JsonObject) => configuration.CredentialProviderType,
  );
  if (credentialTypes.length !== 1 || credentialTypes[0] !== "GATEWAY_IAM_ROLE") {
    fail("Gateway Runtime target must use the scoped Gateway IAM role");
  }
  const runtimeTarget = target.Properties.TargetConfiguration?.Http?.AgentcoreRuntime;
  if (!runtimeTarget?.Arn || runtimeTarget.Qualifier !== "DEFAULT") {
    fail("Gateway target must route to the DEFAULT qualifier of the managed Runtime");
  }
  if (runtimeTarget.Schema !== undefined) {
    fail(
      "HTTP Runtime schema must remain absent until an AgentCore policy engine is configured and its schema is validated",
    );
  }

  const agentCoreRoles = resourcesOf(template, "AWS::IAM::Role").filter((role) =>
    JSON.stringify(role.Properties.AssumeRolePolicyDocument).includes(
      "bedrock-agentcore.amazonaws.com",
    ),
  );
  if (agentCoreRoles.length !== 2) {
    fail("expected one Runtime role and one Gateway role");
  }
  const sourceArns = agentCoreRoles.map(
    (role) =>
      role.Properties.AssumeRolePolicyDocument.Statement?.[0]?.Condition?.ArnLike?.[
        "aws:SourceArn"
      ],
  );
  const sourceAccounts = agentCoreRoles.map(
    (role) =>
      role.Properties.AssumeRolePolicyDocument.Statement?.[0]?.Condition?.StringEquals?.[
        "aws:SourceAccount"
      ],
  );
  if (
    sourceAccounts.some((account) => account === undefined) ||
    !sourceArns.some((arn) => JSON.stringify(arn).includes(":runtime/")) ||
    !sourceArns.some((arn) => JSON.stringify(arn).includes(":gateway/")) ||
    sourceArns.some((arn) => JSON.stringify(arn).includes(":*"))
  ) {
    fail("AgentCore role trust must be account-bound and scoped by resource type");
  }

  const interceptors = gateway.Properties.InterceptorConfigurations;
  if (
    !Array.isArray(interceptors) ||
    interceptors.length !== 1 ||
    interceptors[0].InterceptionPoints?.[0] !== "REQUEST" ||
    interceptors[0].InputConfiguration?.PassRequestHeaders !== true ||
    !interceptors[0].Interceptor?.Lambda?.Arn
  ) {
    fail("Gateway must use one request interceptor to derive the validated actor");
  }
  const resourcePolicy = one(template, "AWS::BedrockAgentCore::ResourcePolicy");
  const resourcePolicyText = JSON.stringify(resourcePolicy.Properties.Policy);
  if (
    !resourcePolicyText.includes("AllowOnlyGatewayRole") ||
    !resourcePolicyText.includes("DenyOtherPrincipals") ||
    !resourcePolicyText.includes("aws:PrincipalArn")
  ) {
    fail("Runtime resource policy must allow only the Gateway role and deny bypass");
  }

  const statements = resourcesOf(template, "AWS::IAM::Policy").flatMap(
    (policy) => policy.Properties.PolicyDocument.Statement ?? [],
  );
  const identityActions = statements.flatMap(actionsOf).filter((action) =>
    String(action).startsWith("bedrock-agentcore:GetWorkloadAccessToken"),
  );
  if (identityActions.length !== 0) {
    fail("proposal-only Runtime must not receive unused workload-token permissions");
  }
  const bedrockStatements = statements.filter((statement) =>
    actionsOf(statement).includes("bedrock:InvokeModel"),
  );
  if (bedrockStatements.length !== 1) {
    fail("expected one explicit Bedrock model invocation statement");
  }
  const modelResources = Array.isArray(bedrockStatements[0].Resource)
    ? bedrockStatements[0].Resource
    : [bedrockStatements[0].Resource];
  if (
    modelResources.length !== 1 ||
    !/^arn:aws(?:-us-gov|-cn)?:bedrock:[^:]+::foundation-model\/.+$/.test(
      String(modelResources[0]),
    )
  ) {
    fail("Bedrock invocation permission must name one exact direct foundation-model ARN");
  }
  const runtimeInvokeStatements = statements.filter((statement) =>
    actionsOf(statement).includes("bedrock-agentcore:InvokeAgentRuntime"),
  );
  if (runtimeInvokeStatements.length !== 1) {
    fail("Gateway role must have one explicit Runtime invocation statement");
  }
  const runtimeResources = Array.isArray(runtimeInvokeStatements[0].Resource)
    ? runtimeInvokeStatements[0].Resource
    : [runtimeInvokeStatements[0].Resource];
  if (
    runtimeResources.length !== 2 ||
    runtimeResources.some((resource: unknown) => resource === "*")
  ) {
    fail("Gateway may invoke only the Runtime ARN and its qualified sessions");
  }

  const mmdsOutput = template.Outputs?.MmdsV2Enforcement?.Value;
  if (typeof mmdsOutput !== "string" || !mmdsOutput.includes("UpdateAgentRuntime")) {
    fail("template must declare the mandatory post-deploy MMDSv2 update gate");
  }
}

if (require.main === module) {
  const templatePath = process.argv[2];
  if (!templatePath) {
    fail("usage: validate-template <CloudFormation template path>");
  }
  const resolved = path.resolve(templatePath);
  validateTemplate(JSON.parse(readFileSync(resolved, "utf8")) as JsonObject);
  process.stdout.write(`validated zero-spend AgentCore template: ${resolved}\n`);
}
