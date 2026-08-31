import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { migrationPreviewForUi } from "./api/clientProjections.js";

const demoPassport = {
  id: "passport-demo",
  owner_id: "demo-owner",
  version: 1,
  name: "Demo Passport",
  intent: "A calm, portable test feed.",
  topic_targets: { indie: 0.25, design: 0.25, local: 0.2, research: 0.3 },
  creator_preferences: {},
  format_preferences: {},
  languages: ["en"],
  hard_exclusions: [],
  serendipity: 0.2,
  max_outrage: 0.05,
  max_source_share: 0.4,
  created_at: "2026-08-29T10:00:00Z",
  updated_at: "2026-08-29T10:00:00Z",
};

const PLATFORM_PROFILE_FILES = [
  "bluesky",
  "facebook",
  "instagram",
  "linkedin",
  "reddit",
  "snapchat",
  "threads",
  "tiktok",
  "x",
  "youtube",
];

test("migration preview labels unknown external delivery as unavailable", () => {
  const preview = migrationPreviewForUi({
    platform: "youtube",
    plan: {
      actions: [{ action_type: "set_topic_preference", parameters: {} }],
      losses: [],
    },
  });
  assert.deepEqual(preview.actions, [
    { action: "Set Topic Preference", count: 1, mode: "Unavailable" },
  ]);

  const authorized = migrationPreviewForUi({
    platform: "authorized_example",
    plan: {
      actions: [{
        action_type: "set_topic_preference",
        parameters: { delivery: "official_api", certification: "authorized_live" },
      }],
      losses: [],
    },
  });
  assert.equal(authorized.actions[0].mode, "Executable");
});

function declaredProfileActions(source) {
  return new Set(
    [...source.matchAll(/ActionType\.([A-Z_]+)/g)].map((match) => match[1].toLowerCase()),
  );
}

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

test("Curator client maps UI state to the real API contract", async () => {
  const previousBase = globalThis.__CURATOR_API_URL__;
  const previousFetch = globalThis.fetch;
  const requests = [];

  globalThis.__CURATOR_API_URL__ = "http://curator.test/api";
  globalThis.fetch = async (input, options = {}) => {
    const url = new URL(String(input));
    const body = options.body ? JSON.parse(options.body) : null;
    requests.push({ method: options.method || "GET", path: url.pathname, body });

    if (url.pathname === "/api/demo") {
      return jsonResponse({
        passports: [demoPassport],
        migrations: [],
        overlays: [],
        companions: [],
        receipts: [],
      });
    }
    if (url.pathname === "/health") {
      return jsonResponse({ status: "healthy", scheduler: "active" });
    }
    if (url.pathname === "/api/passports/passport-demo" && options.method === "PATCH") {
      return jsonResponse({ ...demoPassport, version: 2, name: body.changes.name, updated_at: "2026-08-29T10:05:00Z" });
    }
    if (url.pathname === "/api/passports/capture" && options.method === "POST") {
      return jsonResponse({
        ...demoPassport,
        id: "passport-captured-x",
        name: body.name,
        intent: body.intent,
        created_at: "2026-08-29T10:09:00Z",
        updated_at: "2026-08-29T10:09:00Z",
      }, 201);
    }
    if (url.pathname === "/api/passports/passport-demo/checkpoints") {
      return jsonResponse({
        id: "checkpoint-1",
        passport_id: "passport-demo",
        passport_version: 2,
        label: body.label,
        created_at: "2026-08-29T10:06:00Z",
      }, 201);
    }
    if (url.pathname === "/api/migrations/preview") {
      return jsonResponse({
        id: "migration-1",
        passport_id: "passport-demo",
        passport_version: 2,
        platform: body.platform,
        destination_account_id: body.destination_account_id,
        created_at: "2026-08-29T10:07:00Z",
        plan: {
          actions: [
            { action_type: "follow_creator", reversible: true, parameters: {} },
            { action_type: "set_topic", reversible: true, parameters: {} },
          ],
          losses: [],
        },
      }, 201);
    }
    if (url.pathname === "/api/migrations/migration-1/approval") {
      return jsonResponse({ token: "approval-token-at-least-twenty-characters" });
    }
    if (url.pathname === "/api/migrations/migration-1/execute") {
      return jsonResponse({
        receipt_id: "receipt-1",
        completed_at: "2026-08-29T10:08:00Z",
        decisions: [],
      });
    }
    return jsonResponse({ detail: `Unexpected request: ${options.method || "GET"} ${url.pathname}` }, 500);
  };

  try {
    const { classifyAgentCommand, feedPassportApi } = await import(`./apiClient.js?contract=${Date.now()}`);
    const loaded = await feedPassportApi.loadPassport();
    assert.equal(loaded.source, "service");
    assert.equal(loaded.data.ownerId, "demo-owner");

    const constitution = {
      ...loaded.data.passport,
      title: "Revised Passport",
      topics: [
        { id: "independent_games", percent: 35 },
        { id: "design", percent: 30 },
        { id: "local_culture", percent: 20 },
        { id: "research", percent: 15 },
      ],
      languages: ["English", "German"],
      serendipity: 20,
      outrageCeiling: 5,
      creatorCeiling: 15,
    };
    await feedPassportApi.saveConstitution(constitution);
    await feedPassportApi.issuePassport({ destinations: ["lab"], expiry: "7 days" });
    const preview = await feedPassportApi.previewMigration({ source: "lab", destination: "lab" });
    await feedPassportApi.applyMigration({
      previewId: preview.data.previewId,
      sourceName: "Proof Lab",
      destinationName: "Proof Lab",
    });
    const captured = await feedPassportApi.capturePassport({
      source: "x",
      name: "Captured X demo snapshot",
      intent: "Capture declared dummy-account evidence without claiming live access.",
    });
    assert.equal(captured.source, "service");
    assert.equal(captured.data.passportId, "passport-captured-x");

    const revision = requests.find((item) => item.path === "/api/passports/passport-demo" && item.method === "PATCH");
    assert.deepEqual(revision.body.changes.topic_targets, {
      indie: 0.35,
      design: 0.3,
      local: 0.2,
      research: 0.15,
    });
    assert.deepEqual(revision.body.changes.languages, ["en", "de"]);

    const migration = requests.find((item) => item.path === "/api/migrations/preview");
    assert.equal(migration.body.platform, "feed_passport_lab");
    assert.equal(migration.body.destination_account_id, "destination-new");

    const approval = requests.find((item) => item.path.endsWith("/approval"));
    assert.equal(approval.body.max_total_actions, 2);
    const execution = requests.find((item) => item.path.endsWith("/execute"));
    assert.equal(execution.body.approval_token, "approval-token-at-least-twenty-characters");
    const capture = requests.find((item) => item.path === "/api/passports/capture");
    assert.equal(capture.body.platform, "x");
    assert.equal(capture.body.account_id, "x-demo-account");

    assert.equal(classifyAgentCommand("Copy my feed to Bluesky"), "preview_migration");
    assert.equal(classifyAgentCommand("Audit feed drift"), "watch_drift");
    assert.equal(classifyAgentCommand("Show platform limits"), "list_platforms");
    assert.equal(classifyAgentCommand("Open conference mode temporarily"), "list_templates");
  } finally {
    globalThis.fetch = previousFetch;
    if (previousBase === undefined) delete globalThis.__CURATOR_API_URL__;
    else globalThis.__CURATOR_API_URL__ = previousBase;
  }
});

test("continuous companion invitation survives service refresh and still requires a second local principal", async () => {
  const previousBase = globalThis.__CURATOR_API_URL__;
  const previousFetch = globalThis.fetch;
  const requests = [];
  const shares = [];
  const companions = [];
  const passports = [demoPassport];
  globalThis.__CURATOR_API_URL__ = "http://curator.test/api";

  globalThis.fetch = async (input, options = {}) => {
    const url = new URL(String(input));
    const body = options.body ? JSON.parse(options.body) : null;
    requests.push({ method: options.method || "GET", path: url.pathname, body });
    if (url.pathname === "/health") return jsonResponse({ status: "healthy", scheduler: "active" });
    if (url.pathname === "/api/demo") {
      return jsonResponse({ passports, shares, companions, migrations: [], overlays: [], receipts: [] });
    }
    if (url.pathname === "/api/passports" && options.method === "POST") {
      const partner = {
        ...demoPassport,
        id: "passport-partner",
        owner_id: body.owner_id,
        name: body.name,
        topic_targets: body.topic_targets,
        format_preferences: body.format_preferences,
        hard_exclusions: body.hard_exclusions,
      };
      passports.push(partner);
      return jsonResponse(partner, 201);
    }
    if (url.pathname === "/api/shares" && options.method === "POST") {
      assert.ok(String(body.pair_id || "").trim(), "continuous consent must include a pair_id");
      assert.ok(String(body.counterparty_owner_id || "").trim(), "continuous consent must include a counterparty_owner_id");
      assert.notEqual(body.actor_id, body.counterparty_owner_id);
      const participantOwnerIds = [body.actor_id, body.counterparty_owner_id].sort();
      if (shares.length) {
        const firstSlice = shares[0];
        assert.equal(body.pair_id, firstSlice.pair_id);
        assert.equal(body.actor_id, firstSlice.counterparty_owner_id);
        assert.equal(body.counterparty_owner_id, firstSlice.owner_id);
        assert.deepEqual(participantOwnerIds, firstSlice.participant_owner_ids);
      }
      const slice = {
        id: `slice-${shares.length + 1}`,
        consent_id: `consent-${shares.length + 1}`,
        owner_id: body.actor_id,
        passport_id: body.passport_id,
        passport_version: 1,
        selected_fields: {
          topic_names: body.topic_names,
          creator_ids: body.creator_ids,
          include_serendipity: body.include_serendipity,
          include_formats: body.include_formats,
          include_exclusions: body.include_exclusions,
        },
        status: "active",
        scope: body.scope,
        refresh_on_revision: body.refresh_on_revision,
        target_passport_ids: body.target_passport_ids,
        pair_id: body.pair_id,
        counterparty_owner_id: body.counterparty_owner_id,
        participant_owner_ids: participantOwnerIds,
        created_at: "2026-08-31T10:00:00Z",
        expires_at: body.expires_at,
      };
      shares.push(slice);
      return jsonResponse(slice, 201);
    }
    if (url.pathname === "/api/companions" && options.method === "POST") {
      const pairSlices = body.slice_ids.map((sliceId) => shares.find((item) => item.id === sliceId));
      assert.equal(pairSlices.length, 2);
      assert.ok(pairSlices.every(Boolean));
      assert.equal(pairSlices[0].pair_id, pairSlices[1].pair_id);
      assert.equal(pairSlices[0].counterparty_owner_id, pairSlices[1].owner_id);
      assert.equal(pairSlices[1].counterparty_owner_id, pairSlices[0].owner_id);
      assert.deepEqual(pairSlices[0].participant_owner_ids, pairSlices[1].participant_owner_ids);
      const partner = passports.find((item) => item.id === "passport-partner");
      const blend = {
        id: "companion-continuous-1",
        name: body.name,
        status: "active",
        participant_ids: [demoPassport.owner_id, partner.owner_id],
        slice_ids: body.slice_ids,
        consent_ids: shares.map((item) => item.consent_id),
        requested_weights: body.weights,
        strategy: body.strategy,
        scope: body.scope,
        refresh_on_revision: true,
        pair_id: pairSlices[0].pair_id,
        participant_owner_ids: pairSlices[0].participant_owner_ids,
        sync_revision: 1,
        source_passport_ids: [demoPassport.id, partner.id],
        target_passport_ids: [demoPassport.id, partner.id],
        participant_selected_fields: Object.fromEntries(shares.map((item) => [item.passport_id, item.selected_fields])),
        created_at: "2026-08-31T10:01:00Z",
        last_synced_at: "2026-08-31T10:01:00Z",
        expires_at: body.expires_at,
      };
      companions.push(blend);
      return jsonResponse(blend, 201);
    }
    return jsonResponse({ detail: `Unexpected request: ${options.method || "GET"} ${url.pathname}` }, 500);
  };

  try {
    const { feedPassportApi } = await import(`./apiClient.js?two-person=${Date.now()}`);
    await feedPassportApi.loadPassport();
    const first = await feedPassportApi.createCompanionInvitation({
      share: { topics: true, creators: false, serendipity: true, exclusions: true, formats: false },
      partnerCode: "HARBOR-1936",
      mode: "Weighted Mix",
      weight: 35,
      duration: "7 days",
    });

    assert.equal(first.data.activationPerformed, false);
    assert.equal(companions.length, 0);
    assert.equal(requests.filter((item) => item.path === "/api/shares" && item.method === "POST").length, 1);
    assert.equal(requests.some((item) => item.path === "/api/passports" && item.method === "POST"), false);
    const { feedPassportApi: reloadedApi } = await import(`./apiClient.js?two-person-reload=${Date.now()}`);
    await reloadedApi.loadPassport();
    const refreshed = await reloadedApi.listState();
    assert.equal(refreshed.source, "service");
    assert.equal(refreshed.data.activeCompanion, null);
    assert.equal(refreshed.data.pendingCompanionConsent.id, first.data.invitation.id);
    assert.match(refreshed.data.pendingCompanionConsent.code, /^RESTORED-/);

    const activated = await reloadedApi.acceptCompanionInvitation({
      invitationId: refreshed.data.pendingCompanionConsent.id,
      share: { topics: false, creators: false, serendipity: false, exclusions: false, formats: true },
    });
    const consentCalls = requests.filter((item) => item.path === "/api/shares" && item.method === "POST");
    assert.equal(consentCalls.length, 2);
    assert.notEqual(consentCalls[0].body.actor_id, consentCalls[1].body.actor_id);
    assert.ok(consentCalls[0].body.pair_id);
    assert.equal(consentCalls[0].body.pair_id, consentCalls[1].body.pair_id);
    assert.equal(consentCalls[0].body.counterparty_owner_id, consentCalls[1].body.actor_id);
    assert.equal(consentCalls[1].body.counterparty_owner_id, consentCalls[0].body.actor_id);
    const expectedParticipantOwnerIds = [consentCalls[0].body.actor_id, consentCalls[1].body.actor_id].sort();
    assert.deepEqual(shares[0].participant_owner_ids, expectedParticipantOwnerIds);
    assert.deepEqual(shares[1].participant_owner_ids, expectedParticipantOwnerIds);
    const partnerPassportCall = requests.find((item) => item.path === "/api/passports" && item.method === "POST");
    assert.equal(partnerPassportCall.body.owner_id, consentCalls[0].body.counterparty_owner_id);
    assert.deepEqual(consentCalls[0].body.target_passport_ids, [demoPassport.id]);
    assert.deepEqual(consentCalls[1].body.target_passport_ids, ["passport-partner"]);
    for (const call of consentCalls) {
      assert.equal(call.body.scope, "continuous");
      assert.equal(call.body.refresh_on_revision, true);
      assert.deepEqual(call.body.target_passport_ids, [call.body.passport_id]);
    }
    assert.equal(consentCalls[0].body.include_serendipity, true);
    assert.equal(consentCalls[1].body.include_serendipity, false);
    const blendCall = requests.find((item) => item.path === "/api/companions" && item.method === "POST");
    assert.equal(blendCall.body.scope, "continuous");
    assert.deepEqual(blendCall.body.slice_ids, ["slice-1", "slice-2"]);
    assert.equal(activated.data.companion.syncRevision, 1);
    assert.equal(activated.data.companion.scope, "continuous");
    assert.notEqual(activated.data.companion.ownerPrincipalId, activated.data.companion.partnerPrincipalId);
  } finally {
    globalThis.fetch = previousFetch;
    if (previousBase === undefined) delete globalThis.__CURATOR_API_URL__;
    else globalThis.__CURATOR_API_URL__ = previousBase;
  }
});

test("failed continuous activation revokes only the second principal's partial consent", async () => {
  const previousBase = globalThis.__CURATOR_API_URL__;
  const previousFetch = globalThis.fetch;
  const requests = [];
  let shareSequence = 0;
  globalThis.__CURATOR_API_URL__ = "http://curator.test/api";
  globalThis.fetch = async (input, options = {}) => {
    const url = new URL(String(input));
    const body = options.body ? JSON.parse(options.body) : null;
    requests.push({ method: options.method || "GET", path: url.pathname, body });
    if (url.pathname === "/health") return jsonResponse({ status: "healthy", scheduler: "active" });
    if (url.pathname === "/api/demo") {
      return jsonResponse({ passports: [demoPassport], shares: [], companions: [], migrations: [], overlays: [], receipts: [] });
    }
    if (url.pathname === "/api/passports" && options.method === "POST") {
      return jsonResponse({
        ...demoPassport,
        id: "passport-partner-failure",
        owner_id: body.owner_id,
        topic_targets: body.topic_targets,
      }, 201);
    }
    if (url.pathname === "/api/shares" && options.method === "POST") {
      assert.ok(String(body.pair_id || "").trim(), "continuous consent must include a pair_id");
      assert.ok(String(body.counterparty_owner_id || "").trim(), "continuous consent must include a counterparty_owner_id");
      assert.notEqual(body.actor_id, body.counterparty_owner_id);
      const participantOwnerIds = [body.actor_id, body.counterparty_owner_id].sort();
      shareSequence += 1;
      return jsonResponse({
        id: `failure-slice-${shareSequence}`,
        consent_id: `failure-consent-${shareSequence}`,
        owner_id: body.actor_id,
        passport_id: body.passport_id,
        passport_version: 1,
        selected_fields: {
          topic_names: body.topic_names,
          creator_ids: body.creator_ids,
          include_serendipity: body.include_serendipity,
          include_formats: body.include_formats,
          include_exclusions: body.include_exclusions,
        },
        status: "active",
        scope: body.scope,
        refresh_on_revision: body.refresh_on_revision,
        target_passport_ids: body.target_passport_ids,
        pair_id: body.pair_id,
        counterparty_owner_id: body.counterparty_owner_id,
        participant_owner_ids: participantOwnerIds,
        created_at: "2026-08-31T10:00:00Z",
        expires_at: body.expires_at,
      }, 201);
    }
    if (url.pathname === "/api/companions" && options.method === "POST") {
      return jsonResponse({ detail: "Deliberate blend rejection" }, 409);
    }
    if (url.pathname === "/api/shares/failure-slice-2/revoke" && options.method === "POST") {
      return jsonResponse({ id: "failure-slice-2", status: "revoked", revoked_at: "2026-08-31T10:02:00Z" });
    }
    return jsonResponse({ detail: `Unexpected request: ${options.method || "GET"} ${url.pathname}` }, 500);
  };

  try {
    const { feedPassportApi } = await import(`./apiClient.js?two-person-failure=${Date.now()}`);
    await feedPassportApi.loadPassport();
    const first = await feedPassportApi.createCompanionInvitation({
      share: { topics: true, creators: false, serendipity: true, exclusions: false, formats: false },
      partnerCode: "HARBOR-FAIL",
      mode: "Bridge View",
      weight: 30,
      duration: "48 hours",
    });
    await assert.rejects(
      feedPassportApi.acceptCompanionInvitation({
        invitationId: first.data.invitation.id,
        share: { topics: false, creators: false, serendipity: false, exclusions: true, formats: true },
      }),
      /Deliberate blend rejection/,
    );
    const revokeCalls = requests.filter((item) => item.path.endsWith("/revoke"));
    assert.equal(revokeCalls.length, 1);
    assert.equal(revokeCalls[0].path, "/api/shares/failure-slice-2/revoke");
    const consentCalls = requests.filter((item) => item.path === "/api/shares" && item.method === "POST");
    assert.equal(revokeCalls[0].body.actor_id, consentCalls[1].body.actor_id);
    assert.notEqual(revokeCalls[0].body.actor_id, consentCalls[0].body.actor_id);
    assert.equal((await feedPassportApi.listState()).data.activeCompanion, null);
  } finally {
    globalThis.fetch = previousFetch;
    if (previousBase === undefined) delete globalThis.__CURATOR_API_URL__;
    else globalThis.__CURATOR_API_URL__ = previousBase;
  }
});

test("incomplete service rollback remains visible and retryable", async () => {
  const previousBase = globalThis.__CURATOR_API_URL__;
  const previousFetch = globalThis.fetch;
  globalThis.__CURATOR_API_URL__ = "http://curator.test/api";

  try {
    for (const rollbackStatus of ["rollback_failed", "rollback_partial"]) {
      const receiptId = `receipt-${rollbackStatus}`;
      const requests = [];
      const rawReceipt = {
        id: receiptId,
        passport_id: demoPassport.id,
        passport_version: demoPassport.version,
        destination_id: "destination-new",
        platform: "feed_passport_lab",
        previous_checkpoint_id: "checkpoint-before-migration",
        issued_at: "2026-08-31T09:00:00Z",
        status: "issued",
        outcomes: [{
          status: "executed",
          action: { id: "action-retryable", reversible: true },
        }],
      };
      globalThis.fetch = async (input, options = {}) => {
        const url = new URL(String(input));
        const body = options.body ? JSON.parse(options.body) : null;
        requests.push({ method: options.method || "GET", path: url.pathname, body });
        if (url.pathname === "/health") return jsonResponse({ status: "healthy", scheduler: "active" });
        if (url.pathname === "/api/demo") {
          return jsonResponse({
            passports: [demoPassport],
            shares: [],
            companions: [],
            migrations: [{ id: `migration-${rollbackStatus}`, receipt_id: receiptId, platform: "feed_passport_lab" }],
            overlays: [],
            receipts: [rawReceipt],
          });
        }
        if (url.pathname === `/api/receipts/${receiptId}/rollback-approval`) {
          assert.equal(body.actor_id, demoPassport.owner_id);
          assert.equal(body.platform, "feed_passport_lab");
          return jsonResponse({ token: `approval-${rollbackStatus}-at-least-twenty-characters` });
        }
        if (url.pathname === `/api/receipts/${receiptId}/rollback`) {
          assert.equal(body.actor_id, demoPassport.owner_id);
          assert.equal(body.platform, "feed_passport_lab");
          assert.equal(body.approval_token, `approval-${rollbackStatus}-at-least-twenty-characters`);
          return jsonResponse({
            ...rawReceipt,
            status: rollbackStatus,
            rollback: {
              restored_actions: rollbackStatus === "rollback_partial" ? ["action-restored"] : [],
              failed_actions: ["action-retryable"],
              completed_at: "2026-08-31T09:05:00Z",
            },
          });
        }
        return jsonResponse({ detail: `Unexpected request: ${options.method || "GET"} ${url.pathname}` }, 500);
      };

      const { feedPassportApi } = await import(`./apiClient.js?rollback-incomplete=${rollbackStatus}-${Date.now()}`);
      const loaded = await feedPassportApi.loadPassport();
      const result = await feedPassportApi.rollback(loaded.data.receipts[0]);

      assert.equal(result.source, "service");
      assert.equal(result.data.rollbackComplete, false);
      assert.equal(result.data.receipt.id, receiptId);
      assert.equal(result.data.receipt.type, "Migration rollback incomplete");
      assert.equal(result.data.receipt.status, "Needs attention");
      assert.equal(result.data.receipt.reversible, true);
      assert.equal(result.data.receipt._platform, "feed_passport_lab");
      assert.doesNotMatch(result.data.receipt.detail, /completed|restored through/i);
      assert.equal(requests.filter((item) => item.path.endsWith("/rollback-approval")).length, 1);
      assert.equal(requests.filter((item) => item.path.endsWith("/rollback")).length, 1);
    }
  } finally {
    globalThis.fetch = previousFetch;
    if (previousBase === undefined) delete globalThis.__CURATOR_API_URL__;
    else globalThis.__CURATOR_API_URL__ = previousBase;
  }
});

test("model-backed mission preview uses the dedicated service route without fixture fallback", async () => {
  const previousBase = globalThis.__CURATOR_API_URL__;
  const previousFetch = globalThis.fetch;
  const requests = [];
  globalThis.__CURATOR_API_URL__ = "http://curator.test/api";
  globalThis.fetch = async (input, options = {}) => {
    const url = new URL(String(input));
    const body = options.body ? JSON.parse(options.body) : null;
    requests.push({ method: options.method || "GET", path: url.pathname, body });
    if (url.pathname === "/api/demo") {
      return jsonResponse({
        passports: [demoPassport],
        migrations: [],
        overlays: [],
        companions: [],
        receipts: [],
      });
    }
    if (url.pathname === "/health") {
      return jsonResponse({ status: "healthy", scheduler: "active" });
    }
    if (url.pathname === "/api/agent/model/status") {
      return jsonResponse({
        configured: true,
        online: true,
        readiness: "ready",
        provider: "llamacpp",
        model_id: "feed-passport-local-qwen3-1.7b",
        endpoint_scope: "loopback_only",
        mode: "local_only",
        external_model_calls: false,
        paid_model_calls: false,
      });
    }
    if (url.pathname === "/api/agent/missions/plan") {
      return jsonResponse({
        id: "mission-model-1",
        owner_id: body.actor_id,
        passport_id: body.passport_id,
        platform: body.platform,
        account_id: body.account_id,
        goal: body.goal,
        status: "awaiting_approval",
        planner_evidence: {
          runtime: "strands",
          provider: "llamacpp",
          authority: "proposal_only",
        },
      }, 201);
    }
    return jsonResponse({ detail: `Unexpected request: ${options.method || "GET"} ${url.pathname}` }, 500);
  };

  try {
    const { feedPassportApi } = await import(`./apiClient.js?model=${Date.now()}`);
    await feedPassportApi.loadPassport();
    const status = await feedPassportApi.getAgentModelStatus();
    assert.equal(status.source, "service");
    assert.equal(status.data.readiness, "ready");

    const preview = await feedPassportApi.previewAgentMissionWithModel({
      goal: "Use the local model to narrow this mission safely.",
      platform: "youtube",
      accountId: "destination-new",
      maxIterations: 2,
      maxTotalActions: 4,
      maxActionsPerIteration: 2,
      maxTopicDistance: 0.16,
    });
    assert.equal(preview.source, "service");
    assert.equal(preview.data.planner_evidence.authority, "proposal_only");
    const request = requests.find((item) => item.path === "/api/agent/missions/plan");
    assert.equal(request.body.actor_id, "demo-owner");
    assert.equal(request.body.passport_id, "passport-demo");
    assert.equal(request.body.platform, "twin:youtube");
    assert.equal(request.body.max_total_actions, 4);
    assert.equal("approval_token" in request.body, false);
  } finally {
    globalThis.fetch = previousFetch;
    if (previousBase === undefined) delete globalThis.__CURATOR_API_URL__;
    else globalThis.__CURATOR_API_URL__ = previousBase;
  }
});

test("fixture mode refuses to impersonate a local language model", async () => {
  const previousBase = globalThis.__CURATOR_API_URL__;
  const previousLegacyBase = globalThis.__FEED_PASSPORT_API_BASE__;
  delete globalThis.__CURATOR_API_URL__;
  delete globalThis.__FEED_PASSPORT_API_BASE__;
  try {
    const { feedPassportApi } = await import(`./apiClient.js?no-model=${Date.now()}`);
    await feedPassportApi.loadPassport();
    await assert.rejects(
      feedPassportApi.previewAgentMissionWithModel({
        goal: "Do not fabricate a model response.",
        platform: "youtube",
      }),
      /fixture mode will not impersonate AI/i,
    );
  } finally {
    if (previousBase === undefined) delete globalThis.__CURATOR_API_URL__;
    else globalThis.__CURATOR_API_URL__ = previousBase;
    if (previousLegacyBase === undefined) delete globalThis.__FEED_PASSPORT_API_BASE__;
    else globalThis.__FEED_PASSPORT_API_BASE__ = previousLegacyBase;
  }
});

test("Feature Clerk uses only the service proposal route and exact bound request", async () => {
  const previousBase = globalThis.__CURATOR_API_URL__;
  const previousFetch = globalThis.fetch;
  const requests = [];
  globalThis.__CURATOR_API_URL__ = "http://curator.test/api";
  globalThis.fetch = async (input, options = {}) => {
    const url = new URL(String(input));
    const body = options.body ? JSON.parse(options.body) : null;
    requests.push({ method: options.method || "GET", path: url.pathname, body });
    if (url.pathname === "/api/demo") {
      return jsonResponse({
        passports: [demoPassport],
        migrations: [],
        overlays: [],
        companions: [],
        receipts: [],
      });
    }
    if (url.pathname === "/health") return jsonResponse({ status: "healthy", scheduler: "active" });
    if (url.pathname === "/api/platforms") {
      return jsonResponse([{
        platform: "youtube",
        accounts: ["youtube-demo-account"],
        manifest: {
          evidence_level: "guided",
          operations: { observe: "guided", execute: "guided", sample: "unavailable", rollback: "unavailable", verify: "unavailable" },
          limitations: ["No direct Home ranking control."],
          conformance: { result: "not_run", environment: "not_run" },
        },
      }]);
    }
    if (url.pathname === "/api/agent/features/plan") {
      return jsonResponse({
        proposal: {
          kind: "migration",
          destination: "youtube",
          goal: "Carry my research intent to YouTube.",
          rationale: "A guided migration proposal matches the request.",
        },
        evidence: {
          runtime: "strands",
          provider: "llamacpp",
          model_id: "feed-passport-local-qwen3-1.7b",
          endpoint_scope: "loopback_only",
          external_model_calls: false,
          paid_model_calls: false,
          authority: "proposal_only",
          mutation_tools_exposed: false,
          stop_reason: "typed_proposal_submitted",
          duration_ms: 14,
          cycles: 1,
          usage: { input_tokens: 40, output_tokens: 25, total_tokens: 65 },
          tools: [
            { name: "inspect_selected_passport", status: "completed" },
            { name: "inspect_safe_feature_catalog", status: "completed" },
            { name: "submit_migration_proposal", status: "accepted" },
          ],
          proposal_kind: "migration",
          proposal_text_digests: [],
          catalog_sha256: "a".repeat(64),
          locked_by_server: ["actor", "passport_id_and_version", "consent", "approval", "execution", "rollback"],
        },
      });
    }
    return jsonResponse({ detail: `Unexpected request: ${options.method || "GET"} ${url.pathname}` }, 500);
  };

  try {
    const { feedPassportApi } = await import(`./apiClient.js?feature-clerk=${Date.now()}`);
    await feedPassportApi.loadPassport();
    const result = await feedPassportApi.planFeatureIntent("  Carry my research intent to YouTube.  ");

    assert.equal(result.source, "service");
    assert.equal(result.data.proposal.kind, "migration");
    assert.equal(result.platforms[0].manifest.operations.execute, "guided");
    const planRequest = requests.find((item) => item.path === "/api/agent/features/plan");
    assert.deepEqual(planRequest.body, {
      actor_id: "demo-owner",
      passport_id: "passport-demo",
      request: "Carry my research intent to YouTube.",
    });
    assert.deepEqual(
      requests.filter((item) => item.method !== "GET" && item.path !== "/api/agent/features/plan"),
      [],
    );
    assert.equal(requests.some((item) => /approval|execute|consent|shares|visas|companions|migrations/.test(item.path)), false);
  } finally {
    globalThis.fetch = previousFetch;
    if (previousBase === undefined) delete globalThis.__CURATOR_API_URL__;
    else globalThis.__CURATOR_API_URL__ = previousBase;
  }
});

test("Feature Clerk refuses fixture-mode AI impersonation with 503", async () => {
  const previousBase = globalThis.__CURATOR_API_URL__;
  const previousLegacyBase = globalThis.__FEED_PASSPORT_API_BASE__;
  delete globalThis.__CURATOR_API_URL__;
  delete globalThis.__FEED_PASSPORT_API_BASE__;
  try {
    const { feedPassportApi } = await import(`./apiClient.js?no-feature-clerk=${Date.now()}`);
    await feedPassportApi.loadPassport();
    await assert.rejects(
      feedPassportApi.planFeatureIntent("Do not fabricate this proposal."),
      (error) => error?.status === 503 && /fixture mode will not impersonate AI/i.test(error.message),
    );
  } finally {
    if (previousBase === undefined) delete globalThis.__CURATOR_API_URL__;
    else globalThis.__CURATOR_API_URL__ = previousBase;
    if (previousLegacyBase === undefined) delete globalThis.__FEED_PASSPORT_API_BASE__;
    else globalThis.__FEED_PASSPORT_API_BASE__ = previousLegacyBase;
  }
});

test("service hydration preserves an overlay's exact positive elapsed minutes", async () => {
  const previousBase = globalThis.__CURATOR_API_URL__;
  const previousFetch = globalThis.fetch;
  globalThis.__CURATOR_API_URL__ = "http://curator.test/api";
  globalThis.fetch = async (input) => {
    const url = new URL(String(input));
    if (url.pathname === "/api/demo") {
      return jsonResponse({
        passports: [demoPassport],
        migrations: [],
        overlays: [{
          id: "overlay-exact-555",
          base_passport_id: "passport-demo",
          name: "Exact field pass",
          mode: "isolated",
          status: "active",
          starts_at: "2026-08-29T10:00:00Z",
          expires_at: "2026-08-29T19:15:00Z",
        }],
        companions: [],
        shares: [],
        receipts: [],
      });
    }
    if (url.pathname === "/health") return jsonResponse({ status: "healthy", scheduler: "active" });
    return jsonResponse({ detail: `Unexpected request: GET ${url.pathname}` }, 500);
  };

  try {
    const { feedPassportApi } = await import(`./apiClient.js?exact-duration=${Date.now()}`);
    await feedPassportApi.loadPassport();
    const state = await feedPassportApi.listState();
    assert.equal(state.data.activeVisas[0].duration, "9 hours 15 minutes");
  } finally {
    globalThis.fetch = previousFetch;
    if (previousBase === undefined) delete globalThis.__CURATOR_API_URL__;
    else globalThis.__CURATOR_API_URL__ = previousBase;
  }
});

test("fixture mode preserves portability and reversible lifecycle state", async () => {
  const previousBase = globalThis.__CURATOR_API_URL__;
  const previousLegacyBase = globalThis.__FEED_PASSPORT_API_BASE__;
  delete globalThis.__CURATOR_API_URL__;
  delete globalThis.__FEED_PASSPORT_API_BASE__;

  try {
    const { feedPassportApi } = await import(`./apiClient.js?fixture=${Date.now()}`);
    const loaded = await feedPassportApi.loadPassport();
    assert.equal(loaded.source, "fixture");

    const revised = {
      ...loaded.data.passport,
      version: 4,
      intent: "A stateful fixture Passport for lifecycle verification.",
    };
    const saved = await feedPassportApi.saveConstitution(revised);
    assert.equal(saved.data.receipt.reversible, true);
    assert.equal(saved.data.receipt._previousConstitution.intent, loaded.data.passport.intent);
    await feedPassportApi.rollback(saved.data.receipt);
    let state = await feedPassportApi.listState();
    assert.equal(state.data.activePassport.intent, loaded.data.passport.intent);
    await feedPassportApi.saveConstitution(revised);
    const checkpoint = await feedPassportApi.createCheckpoint("Before experiment");
    await feedPassportApi.saveConstitution({ ...revised, version: 5, intent: "Temporary revision" });
    const restored = await feedPassportApi.restoreCheckpoint(checkpoint.data.id);
    assert.equal(restored.data.constitution.intent, revised.intent);
    assert.equal(restored.data.constitution.version, 6);

    const exported = await feedPassportApi.exportPassport(restored.data.constitution);
    assert.equal(exported.data.format, "feed-passport/v1");
    assert.equal(exported.data.passport.intent, revised.intent);
    assert.equal("raw_history" in exported.data.passport, false);

    await assert.rejects(
      feedPassportApi.importPassport({
        ...exported.data,
        passport: { ...exported.data.passport, raw_history: ["forbidden"] },
      }),
      /unsupported: raw_history/,
    );
    const imported = await feedPassportApi.importPassport(exported.data);
    assert.equal(imported.data.constitution.version, 1);
    assert.equal(imported.data.constitution.intent, revised.intent);

    const visa = await feedPassportApi.issueTemporaryVisa({
      name: "Fixture field pass",
      purpose: "Research conference",
      duration: "6 hours",
      mode: "Isolated Lab",
      expiresAt: "TODAY",
    });
    assert.equal(visa.data.visa.status, "Active");
    assert.equal((await feedPassportApi.listState()).data.activeVisas.length, 1);
    await feedPassportApi.rollback(visa.data.receipt);
    assert.equal((await feedPassportApi.listState()).data.activeVisas.length, 0);

    const invitationStartedAt = Date.now();
    const invitation = await feedPassportApi.createCompanionInvitation({
      share: { topics: false, creators: false, serendipity: true, exclusions: true, formats: true },
      partnerCode: "HARBOR-1936",
      mode: "Bridge View",
      weight: 30,
      duration: "48 hours",
      durationMinutes: 4321,
    });
    assert.equal(invitation.data.activationPerformed, false);
    assert.equal(invitation.data.invitation.ownerConsent.principalId, "fixture-owner");
    assert.equal(invitation.data.invitation.ownerConsent.scope, "continuous");
    const invitationDurationMs = new Date(invitation.data.invitation.expiresAt).getTime() - invitationStartedAt;
    assert.ok(invitationDurationMs >= (4321 * 60_000) + 60_000);
    assert.ok(invitationDurationMs < (4321 * 60_000) + 65_000);
    assert.equal((await feedPassportApi.listState()).data.activeCompanion, null);
    const companion = await feedPassportApi.acceptCompanionInvitation({
      invitationId: invitation.data.invitation.id,
      share: { topics: true, creators: false, serendipity: false, exclusions: false, formats: true },
    });
    assert.equal(companion.data.companion.weight, 30);
    assert.equal(companion.data.companion.effectiveWeight, 40);
    assert.equal(companion.data.companion.scope, "continuous");
    assert.equal(companion.data.companion.syncRevision, 1);
    assert.equal(companion.data.companion.ownerSelectedFields.include_serendipity, true);
    assert.equal(companion.data.companion.partnerSelectedFields.include_serendipity, false);
    assert.notEqual(companion.data.companion.ownerPrincipalId, companion.data.companion.partnerPrincipalId);
    assert.equal((await feedPassportApi.listState()).data.activeCompanion.id, companion.data.companion.id);
    await feedPassportApi.rollback(companion.data.receipt);
    assert.equal((await feedPassportApi.listState()).data.activeCompanion, null);

    const drift = await feedPassportApi.checkDrift();
    assert.equal(drift.data.status, "decision_required");
    const correction = await feedPassportApi.applyDriftCorrection();
    assert.equal(correction.data.applied, false);
    assert.equal(correction.data.simulated, true);
    assert.equal(correction.data.receipt.reversible, false);

    const creator = await feedPassportApi.preserveCreator({
      id: "studio-a",
      name: "Studio A",
      destination: "YouTube",
      destinationHandle: "@studio-a",
      confidence: 100,
    });
    assert.equal((await feedPassportApi.listState()).data.creator_links.length, 1);
    await feedPassportApi.rollback(creator.data.receipt);
    assert.equal((await feedPassportApi.listState()).data.creator_links.length, 0);

    const monitor = await feedPassportApi.createDriftMonitor({ mode: "alert_only", intervalMinutes: 60 });
    assert.equal(monitor.data.status, "active");
    assert.equal((await feedPassportApi.stopDriftMonitor(monitor.data.id)).data.status, "stopped");
    state = await feedPassportApi.listState();
    assert.equal(state.data.drift_monitors.find((item) => item.id === monitor.data.id).status, "stopped");
  } finally {
    if (previousBase === undefined) delete globalThis.__CURATOR_API_URL__;
    else globalThis.__CURATOR_API_URL__ = previousBase;
    if (previousLegacyBase === undefined) delete globalThis.__FEED_PASSPORT_API_BASE__;
    else globalThis.__FEED_PASSPORT_API_BASE__ = previousLegacyBase;
  }
});

test("fixture mission owns a bounded observe-act-measure-adapt loop with exact rollback", async () => {
  const previousBase = globalThis.__CURATOR_API_URL__;
  const previousLegacyBase = globalThis.__FEED_PASSPORT_API_BASE__;
  delete globalThis.__CURATOR_API_URL__;
  delete globalThis.__FEED_PASSPORT_API_BASE__;

  try {
    const { feedPassportApi, missionRollbackIsVerified } = await import(`./apiClient.js?mission=${Date.now()}`);
    await feedPassportApi.loadPassport();
    const preview = await feedPassportApi.previewAgentMission({
      goal: "Make the local YouTube control twin match my useful internet and stop at the target.",
      platform: "youtube",
      accountId: "destination-new",
      maxIterations: 3,
      maxTotalActions: 6,
      maxActionsPerIteration: 3,
      maxTopicDistance: 0.18,
    });

    assert.equal(preview.source, "fixture");
    assert.equal(preview.data.environment, "local_platform_control_twin");
    assert.equal(preview.data.platform, "twin:youtube");
    assert.equal(preview.data.status, "awaiting_approval");
    assert.match(preview.data.fidelity_disclaimer, /does not reproduce/i);
    assert.equal(preview.data.trace.find((step) => step.stage === "consent").status, "required");
    assert.ok(preview.data.action_envelope.every((action) => action.reversible));

    const executed = await feedPassportApi.runAgentMission(preview.data.id);
    assert.equal(executed.data.status, "completed");
    assert.equal(executed.data.stop_reason, "target_reached");
    assert.ok(executed.data.iterations.length >= 2);
    assert.ok(executed.data.after.total_variation_distance < executed.data.before.total_variation_distance);
    assert.ok(executed.data.receipt_ids.length >= 2);
    assert.ok(executed.data.remaining_actions >= 0);
    assert.equal(executed.data.rollback_available, true);

    const rolledBack = await feedPassportApi.rollbackAgentMission(preview.data.id);
    assert.equal(rolledBack.data.status, "rolled_back");
    assert.deepEqual(rolledBack.data.after, rolledBack.data.before);
    assert.equal(rolledBack.data.rollback_available, false);
    assert.equal(rolledBack.data.rollback.status, "completed");
    assert.equal(rolledBack.data.rollback.failure_count, 0);
    assert.equal(rolledBack.data.rollback.verification.status, "completed");
    assert.equal(rolledBack.data.rollback.verification.state_restored, true);
    assert.equal(rolledBack.data.rollback.verification.method, "deterministic_fixture_snapshot");
    assert.deepEqual(rolledBack.data.rollback.verification.evaluation, rolledBack.data.before);
    assert.equal(missionRollbackIsVerified(rolledBack.data), true);
    assert.equal(missionRollbackIsVerified({ ...rolledBack.data, rollback: { ...rolledBack.data.rollback, verification: { status: "completed", state_restored: false } } }), false);
    assert.equal(missionRollbackIsVerified({ ...rolledBack.data, rollback: undefined }), false);
  } finally {
    if (previousBase === undefined) delete globalThis.__CURATOR_API_URL__;
    else globalThis.__CURATOR_API_URL__ = previousBase;
    if (previousLegacyBase === undefined) delete globalThis.__FEED_PASSPORT_API_BASE__;
    else globalThis.__FEED_PASSPORT_API_BASE__ = previousLegacyBase;
  }
});

test("fixture mission actions stay inside every authoritative backend twin profile", async () => {
  const previousBase = globalThis.__CURATOR_API_URL__;
  const previousLegacyBase = globalThis.__FEED_PASSPORT_API_BASE__;
  delete globalThis.__CURATOR_API_URL__;
  delete globalThis.__FEED_PASSPORT_API_BASE__;

  try {
    const { feedPassportApi } = await import(`./apiClient.js?platform-contract=${Date.now()}`);
    await feedPassportApi.loadPassport();

    for (const platform of PLATFORM_PROFILE_FILES) {
      const profileSource = await readFile(
        new URL(`../services/curator/src/feed_passport/adapters/platforms/${platform}.py`, import.meta.url),
        "utf8",
      );
      const declared = declaredProfileActions(profileSource);
      const preview = await feedPassportApi.previewAgentMission({
        goal: `Exercise the ${platform} local control twin within its declared surface.`,
        platform,
        accountId: "destination-new",
        maxIterations: 2,
        maxTotalActions: 4,
        maxActionsPerIteration: 2,
      });
      const actionTypes = preview.data.action_envelope.map((action) => action.action_type);

      assert.equal(preview.source, "fixture", platform);
      assert.equal(preview.data.platform, `twin:${platform}`, platform);
      assert.ok(actionTypes.length > 0, `${platform} fixture must expose at least one bounded control`);
      assert.ok(
        actionTypes.every((actionType) => declared.has(actionType)),
        `${platform} fixture proposed actions outside its backend profile: ${actionTypes
          .filter((actionType) => !declared.has(actionType))
          .join(", ")}`,
      );
    }
  } finally {
    if (previousBase === undefined) delete globalThis.__CURATOR_API_URL__;
    else globalThis.__CURATOR_API_URL__ = previousBase;
    if (previousLegacyBase === undefined) delete globalThis.__FEED_PASSPORT_API_BASE__;
    else globalThis.__FEED_PASSPORT_API_BASE__ = previousLegacyBase;
  }
});
