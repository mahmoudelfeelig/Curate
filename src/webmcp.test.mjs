import assert from "node:assert/strict";
import test from "node:test";

import {
  MIGRATION_DESTINATION_IDS,
  TEMPORARY_VISA_DURATIONS,
  prepareWebMcpMigrationPreview,
  registerFeedPassportTools,
  validateTemporaryVisaInput,
} from "./webmcp.js";
import { webMcpTemporaryVisaForm } from "./api/clientProjections.js";


function installGlobal(name, value) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, name);
  Object.defineProperty(globalThis, name, { configurable: true, value, writable: true });
  return () => {
    if (previous) Object.defineProperty(globalThis, name, previous);
    else delete globalThis[name];
  };
}

test("document.modelContext registers, invokes, and cleans up every Feed Passport tool", async () => {
  const definitions = [];
  const cleaned = [];
  const registry = {
    registerTool(definition) {
      definitions.push(definition);
      return () => cleaned.push(definition.name);
    },
  };
  const restoreDocument = installGlobal("document", { modelContext: registry });
  const restoreFallback = installGlobal("webmcp", {
    registerTool() {
      throw new Error("document.modelContext must take precedence");
    },
  });
  const calls = [];

  try {
    const registration = registerFeedPassportTools({
      inspect: async (input) => calls.push(["inspect", input]) && { version: 3 },
      previewMigration: async (input) => calls.push(["preview", input]) && { preview: true },
      prepareTemporaryVisa: async (input) => calls.push(["visa", input]) && { prepared: true },
      openRollback: async (input) => calls.push(["rollback", input]) && { opened: true },
    });

    assert.equal(registration.supported, true);
    assert.equal(registration.registered, 4);
    assert.deepEqual(definitions.map((item) => item.name), [
      "feed_passport.inspect",
      "feed_passport.preview_migration",
      "feed_passport.issue_temporary_visa",
      "feed_passport.open_rollback",
    ]);
    const preview = definitions.find((item) => item.name === "feed_passport.preview_migration");
    assert.deepEqual(preview.inputSchema.properties.source.enum, MIGRATION_DESTINATION_IDS);
    assert.deepEqual(preview.inputSchema.properties.destination.enum, MIGRATION_DESTINATION_IDS);
    assert.deepEqual(await preview.execute({ source: "x", destination: "youtube" }), { preview: true });
    assert.deepEqual(calls, [["preview", { source: "x", destination: "youtube" }]]);

    registration.cleanup();
    assert.deepEqual(cleaned, definitions.map((item) => item.name));
  } finally {
    restoreFallback();
    restoreDocument();
  }
});

test("migration preview binds a validated route and exercises preview authority only", async () => {
  const calls = [];
  const api = {
    async previewMigration(route) {
      calls.push(["preview", route]);
      return {
        source: "fixture",
        data: {
          previewId: "PRV-WEBMCP-7",
          actions: [{ action: "Tune topic weights", count: 2, mode: "Fixture" }],
          losses: [{ severity: "Partial", title: "Home ranking", detail: "No direct control." }],
        },
      };
    },
    async applyMigration() {
      calls.push(["apply"]);
    },
    async grantConsent() {
      calls.push(["consent"]);
    },
  };

  const prepared = await prepareWebMcpMigrationPreview({
    api,
    input: { source: " X ", destination: "YouTube" },
    sourcePassport: { id: "FP-74128", version: 3, source: "x" },
  });

  assert.deepEqual(calls, [["preview", { source: "x", destination: "youtube" }]]);
  assert.deepEqual(prepared.preview.route, { source: "x", destination: "youtube" });
  assert.deepEqual(prepared.preview.sourcePassport, { id: "FP-74128", version: 3, source: "x" });
  assert.deepEqual(prepared.response.preview, {
    previewId: "PRV-WEBMCP-7",
    actions: [{ action: "Tune topic weights", count: 2, mode: "Fixture" }],
    losses: [{ severity: "Partial", title: "Home ranking", detail: "No direct control." }],
  });
  assert.equal(prepared.response.approvalRequired, true);
  assert.equal(prepared.response.consentGranted, false);
  assert.equal(prepared.response.executionPerformed, false);
  assert.equal(prepared.response.mutationPerformed, false);
});

test("temporary visa WebMCP input cannot retain stale exact minutes or invent durations", () => {
  assert.deepEqual(validateTemporaryVisaInput({ purpose: " Field notes ", duration: "48 hours" }), {
    purpose: "Field notes",
    duration: "48 hours",
  });
  assert.throws(
    () => validateTemporaryVisaInput({ purpose: "Field notes", duration: "forever" }),
    /Unsupported temporary visa duration/,
  );
  assert.deepEqual(
    webMcpTemporaryVisaForm(
      { purpose: "Clerk", duration: "555 minutes", durationMinutes: 555, mode: "Isolated Lab" },
      { purpose: "WebMCP", duration: "48 hours" },
    ),
    { purpose: "WebMCP", duration: "48 hours", durationMinutes: null, mode: "Isolated Lab" },
  );
  assert.deepEqual(TEMPORARY_VISA_DURATIONS, ["6 hours", "48 hours", "7 days"]);
});

test("migration preview rejects unsupported or self-routes before calling the API", async () => {
  let previewCalls = 0;
  const api = {
    async previewMigration() {
      previewCalls += 1;
      throw new Error("should not be reached");
    },
  };

  await assert.rejects(
    prepareWebMcpMigrationPreview({ api, input: { source: "unknown", destination: "youtube" } }),
    /Unsupported migration source/,
  );
  await assert.rejects(
    prepareWebMcpMigrationPreview({ api, input: { source: "x", destination: "x" } }),
    /must be different/,
  );
  await assert.rejects(
    prepareWebMcpMigrationPreview({
      api,
      input: { source: "x", destination: "youtube" },
      sourcePassport: { id: "FP-LAB", version: 2, source: "lab" },
    }),
    /is not bound to active Passport FP-LAB/,
  );
  assert.equal(previewCalls, 0);
});

test("unsupported hosts degrade without registering tools", () => {
  const restoreDocument = installGlobal("document", {});
  const restoreWebmcp = installGlobal("webmcp", undefined);
  try {
    const registration = registerFeedPassportTools({ inspect: () => ({}) });
    assert.deepEqual(
      { supported: registration.supported, registered: registration.registered },
      { supported: false, registered: 0 },
    );
    registration.cleanup();
  } finally {
    restoreWebmcp();
    restoreDocument();
  }
});
