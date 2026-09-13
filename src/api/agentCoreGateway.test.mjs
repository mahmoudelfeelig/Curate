import assert from "node:assert/strict";
import test from "node:test";

import {
  AgentCoreGatewayError,
  agentCoreConfigFromEnv,
  createAgentCoreGatewayClient,
} from "./agentCoreGateway.js";

const GATEWAY = "https://demo-123.gateway.bedrock-agentcore.eu-north-1.amazonaws.com";

function jwt(overrides = {}) {
  const encode = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
  return `${encode({ alg: "RS256" })}.${encode({
    token_use: "access",
    scope: "feed-passport/invoke",
    sub: "judge-cognito-sub",
    exp: 1_900_000_000,
    ...overrides,
  })}.${Buffer.from("signature").toString("base64url")}`;
}

const PASSPORT = {
  id: "passport-demo-v3",
  owner_id: "untrusted-browser-owner",
  name: "My useful internet",
  version: 3,
  intent: "Prefer calm, useful material.",
  topic_targets: { research: 0.6, design: 0.4 },
  creator_preferences: {},
  format_preferences: { long_form: 0.8 },
  languages: ["en"],
  hard_exclusions: ["ragebait"],
  serendipity: 0.2,
  max_outrage: 0.05,
  max_source_share: 0.3,
};

const EVIDENCE = [{
  platform: "youtube",
  metadata_source: "youtube_data_api_v3",
  metadata_verified: true,
  title: "A calm astronomy explainer",
  description: "A measured tour of a newly imaged nebula.",
  inferred_topics: ["astronomy", "science"],
  ragebait_signal: false,
  confidence: 0.92,
}];

test("AgentCore configuration is opt-in and restricted to the eu-north-1 Gateway origin", () => {
  assert.deepEqual(agentCoreConfigFromEnv({}), { configured: false });
  assert.deepEqual(
    agentCoreConfigFromEnv({ VITE_CURATE_AGENTCORE_GATEWAY_URL: `${GATEWAY}/` }),
    { configured: true, endpoint: `${GATEWAY}/curator-runtime/invocations` },
  );
  assert.throws(
    () => agentCoreConfigFromEnv({ VITE_CURATE_AGENTCORE_GATEWAY_URL: "https://example.com" }),
    /eu-north-1 Gateway origin/,
  );
  assert.throws(
    () => agentCoreConfigFromEnv({ VITE_CURATE_AGENTCORE_GATEWAY_URL: `${GATEWAY}/health` }),
    /cannot contain a path/,
  );
});

test("authenticated PlanFeed is the only browser operation and binds owner to token subject", async () => {
  const calls = [];
  const client = createAgentCoreGatewayClient({
    environment: { VITE_CURATE_AGENTCORE_GATEWAY_URL: GATEWAY },
    getAccessToken: async () => jwt(),
    now: () => 1_800_000_000_000,
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return {
        ok: true,
        status: 200,
        headers: { get: () => "application/json" },
        json: async () => ({
          kind: "feed_goal_proposal",
          proposal: { target_topic_weights: { astronomy: 1 }, hard_exclusions: [], rationale: "Calmer science." },
          evidence: { authority: "proposal_only" },
          consent_created: false,
          approved: false,
          executed: false,
        }),
      };
    },
  });
  const result = await client.planFeed({
    passport: PASSPORT,
    request: "Show me more astronomy and less ragebait.",
    evidence: EVIDENCE,
  });
  assert.equal(result.kind, "feed_goal_proposal");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, `${GATEWAY}/curator-runtime/invocations`);
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.credentials, "omit");
  assert.equal(calls[0].options.redirect, "error");
  assert.match(calls[0].options.headers.Authorization, /^Bearer [^.]+\.[^.]+\.[^.]+$/);
  const body = JSON.parse(calls[0].options.body);
  assert.equal(body.kind, "plan_feed");
  assert.equal(body.passport.owner_id, "judge-cognito-sub");
  assert.equal(body.passport.owner_id === PASSPORT.owner_id, false);
  assert.deepEqual(body.evidence, EVIDENCE);
  assert.deepEqual(Object.keys(client).sort(), ["planFeed", "status"]);
});

test("AgentCore browser planner rejects missing scope before network access", async () => {
  let called = false;
  const client = createAgentCoreGatewayClient({
    environment: { VITE_CURATE_AGENTCORE_GATEWAY_URL: GATEWAY },
    getAccessToken: async () => jwt({ scope: "openid" }),
    now: () => 1_800_000_000_000,
    fetchImpl: async () => { called = true; },
  });
  await assert.rejects(
    client.planFeed({ passport: PASSPORT, request: "More science", evidence: EVIDENCE }),
    (error) => error instanceof AgentCoreGatewayError && error.status === 401,
  );
  assert.equal(called, false);
});

test("AgentCore browser planner rejects responses that claim execution", async () => {
  const client = createAgentCoreGatewayClient({
    environment: { VITE_CURATE_AGENTCORE_GATEWAY_URL: GATEWAY },
    getAccessToken: async () => jwt(),
    now: () => 1_800_000_000_000,
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      json: async () => ({
        kind: "feed_goal_proposal",
        proposal: {},
        evidence: {},
        consent_created: false,
        approved: false,
        executed: true,
      }),
    }),
  });
  await assert.rejects(
    client.planFeed({ passport: PASSPORT, request: "More science", evidence: EVIDENCE }),
    (error) => error instanceof AgentCoreGatewayError && error.status === 502,
  );
});

test("AgentCore errors never reflect a bearer token", async () => {
  const accessToken = jwt();
  const client = createAgentCoreGatewayClient({
    environment: { VITE_CURATE_AGENTCORE_GATEWAY_URL: GATEWAY },
    getAccessToken: async () => accessToken,
    now: () => 1_800_000_000_000,
    fetchImpl: async () => ({
      ok: false,
      status: 403,
      headers: { get: () => "application/json" },
      json: async () => ({ message: "Access denied" }),
    }),
  });
  await assert.rejects(
    client.planFeed({ passport: PASSPORT, request: "More science", evidence: EVIDENCE }),
    (error) => {
      assert.equal(error.status, 403);
      assert.doesNotMatch(error.message, new RegExp(accessToken.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
      return true;
    },
  );
});

