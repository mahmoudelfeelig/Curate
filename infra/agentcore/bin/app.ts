import { App } from "aws-cdk-lib";

import { FeedPassportAgentCoreStack } from "../lib/feed-passport-stack";

function requiredContext(app: App, name: string): string {
  const value = app.node.tryGetContext(name);
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`missing required CDK context: ${name}`);
  }
  return value.trim();
}

function urlList(app: App, name: string): string[] {
  const raw = requiredContext(app, name);
  const values = raw.split(",").map((value) => value.trim()).filter(Boolean);
  if (values.length === 0 || values.some((value) => !/^https?:\/\//.test(value))) {
    throw new Error(`${name} must be a comma-separated list of absolute HTTP(S) URLs`);
  }
  return values;
}

const app = new App();
new FeedPassportAgentCoreStack(app, "FeedPassportAgentCore", {
  env: {
    account: requiredContext(app, "awsAccountId"),
    region: requiredContext(app, "awsRegion"),
  },
  artifactPath: requiredContext(app, "artifactPath"),
  bedrockModelId: requiredContext(app, "bedrockModelId"),
  bedrockModelArn: requiredContext(app, "bedrockModelArn"),
  callbackUrls: urlList(app, "callbackUrls"),
  logoutUrls: urlList(app, "logoutUrls"),
  cognitoDomainPrefix: requiredContext(app, "cognitoDomainPrefix"),
  description: "Feed Passport proposal-only AgentCore demo infrastructure",
});
