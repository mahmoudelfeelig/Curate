import assert from "node:assert/strict";
import test from "node:test";

import { prepareWebMcpMigrationPreview, registerFeedPassportTools } from "../src/webmcp.js";

test("WebMCP exposes preview and inspect boundaries but no self-approval or execution tool", async () => {
  const previous = globalThis.webmcp;
  const registered = [];
  globalThis.webmcp = {
    registerTool(definition) {
      registered.push(definition);
      return { unregister() {} };
    },
  };

  try {
    const registration = registerFeedPassportTools({
      inspect: async () => ({ ok: true }),
      previewMigration: async () => ({ opened: "migration" }),
      prepareTemporaryVisa: async () => ({ opened: "temporary", approvalRequired: true }),
      openRollback: async () => ({ opened: "history", rollbackPerformed: false }),
      previewAgentMission: async ({ goal, platform }) => ({
        opened: "agent",
        goal,
        platform,
        approvalGranted: false,
        accountAccessed: false,
      }),
      inspectAgentMission: async () => ({ mission: null, approvalGranted: false }),
      previewFeedEvidence: async ({ goal, links }) => ({
        opened: "evidence",
        goal,
        links,
        approvalGranted: false,
        passportChanged: false,
        accountAccessed: false,
      }),
    });

    assert.equal(registration.supported, true);
    assert.equal(registration.registered, 7);
    const names = registered.map((tool) => tool.name);
    assert.ok(names.includes("feed_passport.preview_local_agent_mission"));
    assert.ok(names.includes("feed_passport.inspect_local_agent_mission"));
    assert.ok(names.includes("feed_passport.preview_feed_evidence"));
    assert.equal(names.some((name) => /approve|execute|run_mission/.test(name)), false);

    const previewTool = registered.find((tool) => tool.name === "feed_passport.preview_local_agent_mission");
    const result = await previewTool.execute({ goal: "Curate my local twin", platform: "youtube" });
    assert.equal(result.approvalGranted, false);
    assert.equal(result.accountAccessed, false);
    const evidenceTool = registered.find((tool) => tool.name === "feed_passport.preview_feed_evidence");
    const linkOnlyEvidence = [{ url: "https://www.instagram.com/reel/example/" }];
    const evidenceResult = await evidenceTool.execute({
      goal: "Reduce ragebait",
      links: linkOnlyEvidence,
    });
    assert.equal(evidenceResult.opened, "evidence");
    assert.equal(evidenceResult.approvalGranted, false);
    assert.equal(evidenceResult.passportChanged, false);
    assert.equal(evidenceResult.accountAccessed, false);
    assert.deepEqual(evidenceResult.links, linkOnlyEvidence);
  } finally {
    if (previous === undefined) delete globalThis.webmcp;
    else globalThis.webmcp = previous;
  }
});

test("WebMCP migration preparation returns a renderable preview without consent or execution", async () => {
  const operations = [];
  const prepared = await prepareWebMcpMigrationPreview({
    api: {
      async previewMigration(route) {
        operations.push({ operation: "preview", route });
        return {
          source: "service",
          data: {
            previewId: "MIG-WEBMCP-42",
            actions: [{ action: "Compile subscriptions", count: 4, mode: "Guided" }],
            losses: [{ severity: "Informational", title: "No translation loss", detail: "All intent was represented." }],
          },
        };
      },
      async applyMigration() {
        operations.push({ operation: "apply" });
      },
      async grantConsent() {
        operations.push({ operation: "consent" });
      },
    },
    input: { source: "lab", destination: "youtube" },
    sourcePassport: { id: "FP-LOCAL", version: 8, source: "lab" },
  });

  assert.deepEqual(operations, [{ operation: "preview", route: { source: "lab", destination: "youtube" } }]);
  assert.equal(prepared.response.opened, "migration");
  assert.equal(prepared.response.preview.previewId, "MIG-WEBMCP-42");
  assert.deepEqual(prepared.preview.actions, prepared.response.preview.actions);
  assert.deepEqual(prepared.preview.losses, prepared.response.preview.losses);
  assert.deepEqual(prepared.response.sourcePassport, { id: "FP-LOCAL", version: 8, source: "lab" });
  assert.deepEqual(
    {
      approvalRequired: prepared.response.approvalRequired,
      consentGranted: prepared.response.consentGranted,
      executionPerformed: prepared.response.executionPerformed,
      mutationPerformed: prepared.response.mutationPerformed,
    },
    { approvalRequired: true, consentGranted: false, executionPerformed: false, mutationPerformed: false },
  );
});
