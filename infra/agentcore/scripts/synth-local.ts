import * as path from "node:path";

import { App } from "aws-cdk-lib";

import { FeedPassportAgentCoreStack } from "../lib/feed-passport-stack";

const infraRoot = path.resolve(__dirname, "../..");
const app = new App({ outdir: path.join(infraRoot, "cdk.out") });

new FeedPassportAgentCoreStack(app, "FeedPassportAgentCore", {
  env: { account: "111122223333", region: "eu-central-1" },
  artifactPath: path.join(infraRoot, "packaging", "agentcore_main.py"),
  bedrockModelId: "amazon.nova-lite-v1:0",
  bedrockModelArn:
    "arn:aws:bedrock:eu-central-1::foundation-model/amazon.nova-lite-v1:0",
  callbackUrls: ["http://127.0.0.1:5173/auth/callback"],
  logoutUrls: ["http://127.0.0.1:5173/"],
  cognitoDomainPrefix: "feed-passport-local-plan-111122223333",
  description: "Feed Passport deterministic no-network AgentCore plan",
});

app.synth();
process.stdout.write("Synthesized deterministic local template without AWS API calls.\n");
