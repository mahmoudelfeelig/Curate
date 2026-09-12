import assert from "node:assert/strict";
import test from "node:test";

import {
  commissionPresentation,
  commissionStatusView,
  connectedCommissionForMode,
  connectionOptionLabel,
  eligibleLiveConnections,
  exactCommissionPlan,
} from "./connectedAgentDesk.js";


function commission(overrides = {}) {
  return {
    id: "live-commission-1",
    status: "awaiting_approval",
    priority_mode: "balanced",
    selection_summary: "The local priority planner ordered the certified action families.",
    planner_evidence: {
      provider: "llamacpp",
      model_id: "local-test-model",
      deterministic_validation: "passed",
    },
    plan: {
      actions: [{ id: "fallback-must-not-render", action_type: "post", target: "wrong" }],
    },
    approval_scope: {
      max_total_actions: 2,
      allowed_action_types: ["subscribe_creator", "unsubscribe_creator"],
      certification: {
        execute: ["subscribe_creator", "unsubscribe_creator"],
        environment: "authorized_live",
        result: "passed",
      },
      executable_plan: {
        actions: [
          {
            id: "action-exact-1",
            action_type: "subscribe_creator",
            target: "channel-one",
            reason: "Keep this selected creator.",
            reversible: true,
          },
          {
            id: "action-exact-2",
            action_type: "unsubscribe_creator",
            target: "channel-two",
            reason: "Remove this selected creator.",
            reversible: true,
          },
        ],
        losses: [
          {
            field: "topic_targets",
            severity: "high",
            reason: "YouTube does not expose recommendation topic weights.",
          },
        ],
      },
    },
    ...overrides,
  };
}


test("uses only the immutable approval-scope action sequence", () => {
  const value = commission();
  const exact = exactCommissionPlan(value);
  const view = commissionPresentation(value);

  assert.equal(exact.actions[0].id, "action-exact-1");
  assert.deepEqual(view.actions.map((action) => action.id), ["action-exact-1", "action-exact-2"]);
  assert.equal(view.exactPlanValid, true);
  assert.equal(view.certificationValid, true);
  assert.equal(JSON.stringify(view).includes("fallback-must-not-render"), false);
  assert.equal(JSON.stringify(view).includes("fingerprint"), false);
});


test("never exposes a fixture commission as a connected service result", () => {
  const value = commission();

  assert.equal(connectedCommissionForMode("fixture", value), null);
  assert.equal(connectedCommissionForMode("service", value), value);
});


test("fails closed when any exact action falls outside the certified subset", () => {
  const value = commission();
  value.approval_scope.executable_plan.actions.push({
    id: "action-not-certified",
    action_type: "mute_keyword",
    target: "ragebait",
    reversible: true,
  });

  const view = commissionPresentation(value);

  assert.equal(view.allActionsCertified, false);
  assert.equal(view.actions.at(-1).certified, false);
  assert.deepEqual(view.certifiedSubset, ["subscribe_creator", "unsubscribe_creator"]);
});


test("normalizes translation losses without inventing recommendation verification", () => {
  const view = commissionPresentation(commission());

  assert.equal(view.limitations.length, 1);
  assert.equal(view.limitations[0].title, "Topic Targets");
  assert.match(view.limitations[0].detail, /does not expose/);
  assert.equal("after" in view, false);
  assert.equal("evaluation" in view, false);
});


test("accepts only active YouTube and Bluesky connection choices", () => {
  const values = eligibleLiveConnections([
    { id: "youtube-active", platform: "youtube", status: "active" },
    { id: "bluesky-active", platform: "bluesky", active: true },
    { id: "youtube-stale", platform: "youtube", status: "reauth_required" },
    { id: "instagram-active", platform: "instagram", status: "active" },
  ]);

  assert.deepEqual(values.map((connection) => connection.id), ["youtube-active", "bluesky-active"]);
});


test("prints the exact connected subject without claiming it is a dummy account", () => {
  const label = connectionOptionLabel({
    id: "connection-one",
    platform: "youtube",
    external_subject: "UC_exact_owner_subject",
  });

  assert.equal(label, "Youtube account · UC_exact_owner_subject");
  assert.doesNotMatch(label, /dummy/i);
});


test("defines an explicit presentation for every live commission stop state", () => {
  const expected = {
    awaiting_approval: "approve",
    reconciliation_required: "reconcile",
    failed_recoverable: "reconcile",
    issued: "terminal",
    issued_with_drift: "terminal",
    issued_partial: "terminal",
    stale: "repreview",
    needs_human: "terminal",
    planning: "pending",
    planning_failed: "repreview",
    cancelled: "terminal",
    rolled_back: "terminal",
    rollback_partial: "terminal",
  };

  for (const [status, action] of Object.entries(expected)) {
    assert.equal(commissionStatusView(status).action, action, status);
  }
});
