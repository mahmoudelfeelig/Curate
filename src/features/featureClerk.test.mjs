import assert from "node:assert/strict";
import test from "node:test";

import {
  expectedFeatureTools,
  formatExactDuration,
  mapFeatureProposalToDesk,
  resolveMigrationCapability,
} from "./featureClerk.js";

test("migration proposals map the server Lab id without performing work", () => {
  const mapped = mapFeatureProposalToDesk({
    kind: "migration",
    destination: "feed_passport_lab",
    goal: "Copy my calm research intent into the Lab.",
    rationale: "The Lab is the only requested destination.",
  });

  assert.deepEqual(mapped, {
    section: "migration",
    values: {
      destination: "lab",
      goal: "Copy my calm research intent into the Lab.",
      rationale: "The Lab is the only requested destination.",
    },
  });
  assert.deepEqual(expectedFeatureTools("migration"), [
    "inspect_selected_passport",
    "inspect_safe_feature_catalog",
    "submit_migration_proposal",
  ]);
});

test("temporary proposals preserve exact minutes and honest local modes", () => {
  const mapped = mapFeatureProposalToDesk({
    kind: "temporary_visa",
    purpose: "Focus on human-centered agent research.",
    duration_minutes: 555,
    mode: "reversible_live",
    rationale: "A time-boxed reversible experiment fits the request.",
  });

  assert.equal(mapped.section, "temporary");
  assert.equal(mapped.values.purpose, "Focus on human-centered agent research.");
  assert.equal(mapped.values.durationMinutes, 555);
  assert.equal(mapped.values.duration, "9 hours 15 minutes · exactly 555 minutes");
  assert.equal(mapped.values.mode, "Reversible Lab");
  assert.equal(formatExactDuration(10080), "7 days · exactly 10080 minutes");
});

test("companion proposals map every selected category but no partner identity", () => {
  const mapped = mapFeatureProposalToDesk({
    kind: "companion_sync",
    field_categories: ["topics", "creators", "formats", "exclusions", "serendipity"],
    strategy: "taste_swap",
    companion_input_percent: 33,
    duration_minutes: 4321,
    rationale: "A selective perspective exchange matches the requested fields.",
  });

  assert.equal(mapped.section, "companion");
  assert.deepEqual(mapped.values.share, {
    topics: true,
    creators: true,
    serendipity: true,
    exclusions: true,
    formats: true,
  });
  assert.deepEqual(mapped.values.blend, {
    mode: "Taste Swap",
    weight: 33,
    duration: "3 days 1 minute · exactly 4321 minutes",
    durationMinutes: 4321,
  });
  assert.equal("partnerCode" in mapped.values, false);
  assert.equal("partnerShare" in mapped.values, false);
  assert.equal("consent" in mapped.values, false);
});

test("migration capability resolution never calls a guided handoff executable", () => {
  const capability = resolveMigrationCapability(
    { kind: "migration", destination: "youtube" },
    [{
      platform: "youtube",
      manifest: {
        evidence_level: "guided",
        operations: { execute: "guided" },
        limitations: ["No direct Home ranking control."],
        conformance: { result: "not_run", environment: "not_run" },
      },
    }],
  );

  assert.equal(capability.boundary, "Guided external handoff only");
  assert.equal(capability.executeMode, "guided");
  assert.equal(capability.conformance, "not_run");
  assert.doesNotMatch(capability.boundary, /executable/i);
});

test("migration capability resolution fails closed for absent or mismatched bindings", () => {
  const missing = resolveMigrationCapability(
    { kind: "migration", destination: "youtube" },
    [],
  );
  assert.equal(missing.found, false);
  assert.equal(missing.executeMode, "unavailable");
  assert.equal(missing.boundary, "No execution claim");

  const mismatched = resolveMigrationCapability(
    {
      kind: "migration",
      destination: "youtube",
      capability: {
        destination_id: "x",
        evidence_level: "executable",
        execute_mode: "authorized",
      },
    },
    [],
  );
  assert.equal(mismatched.found, false);
  assert.equal(mismatched.executeMode, "unavailable");
  assert.equal(mismatched.source, "server_bound_mismatch");
});
