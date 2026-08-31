import assert from "node:assert/strict";


const apiBase = process.env.CURATOR_BASE || "http://127.0.0.1:8000";
const serviceRoot = apiBase.replace(/\/api\/?$/i, "");
globalThis.__CURATOR_API_URL__ = apiBase;

const { feedPassportApi } = await import(`../src/apiClient.js?live=${Date.now()}`);
const checks = [];
const pass = (name) => checks.push(name);
const apiJson = async (path, options = {}) => {
  const response = await fetch(`${serviceRoot}${path}`, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  const payload = await response.json();
  assert.equal(response.ok, true, `${path}: ${JSON.stringify(payload)}`);
  return payload;
};

const loaded = await feedPassportApi.loadPassport();
assert.equal(loaded.source, "service");
assert.equal(loaded.data.scheduler, "active");
pass("service-and-autonomous-scheduler");

const firstState = await feedPassportApi.listState();
assert.equal(firstState.source, "service");
assert.ok(firstState.data.activePassportId);
pass("state-hydration");

const schedulerStartsAt = new Date();
const schedulerVisa = await apiJson("/api/visas", {
  method: "POST",
  body: JSON.stringify({
    actor_id: firstState.data.ownerId,
    passport_id: firstState.data.activePassportId,
    name: "Autonomous scheduler smoke visa",
    topic_adjustments: { research: 0.05 },
    add_exclusions: [],
    remove_exclusions: [],
    starts_at: schedulerStartsAt.toISOString(),
    expires_at: new Date(schedulerStartsAt.getTime() + 800).toISOString(),
    mode: "isolated",
  }),
});
let schedulerExpired = false;
const schedulerDeadline = Date.now() + 8_000;
while (Date.now() < schedulerDeadline && !schedulerExpired) {
  await new Promise((resolve) => setTimeout(resolve, 250));
  const demo = await apiJson("/api/demo");
  schedulerExpired = demo.overlays.some(
    (item) => item.id === schedulerVisa.id && item.status === "expired" && item.expired_at,
  );
}
assert.equal(schedulerExpired, true);
pass("autonomous-visa-expiry");

const checkpoint = await feedPassportApi.createCheckpoint("Live client smoke checkpoint");
assert.ok(checkpoint.data.id);
const restored = await feedPassportApi.restoreCheckpoint(checkpoint.data.id);
assert.ok(restored.data.constitution.version > checkpoint.data.passport_version);
pass("checkpoint-create-and-restore");

const preview = await feedPassportApi.previewMigration({ source: "lab", destination: "lab" });
assert.ok(preview.data.previewId);
const migration = await feedPassportApi.applyMigration({
  previewId: preview.data.previewId,
  sourceName: "Feed Passport Lab",
  destinationName: "Feed Passport Lab",
});
assert.ok(migration.data.receipt.id);
pass("exact-consent-lab-migration");

const isolatedVisa = await feedPassportApi.issueTemporaryVisa({
  name: "Live smoke field pass",
  purpose: "Research conference and human-centered agents",
  duration: "6 hours",
  mode: "Isolated Lab",
  expiresAt: "SIX HOURS",
});
assert.equal(isolatedVisa.data.visa.status, "Active");
let hydrated = await feedPassportApi.listState();
assert.ok(hydrated.data.activeVisas.some((item) => item.id === isolatedVisa.data.visa.id));
await feedPassportApi.revokeTemporaryVisa(isolatedVisa.data.visa.id);
pass("temporary-visa-hydrate-and-revoke");

const liveVisa = await feedPassportApi.issueTemporaryVisa({
  name: "Reversible Lab pass",
  purpose: "Temporarily emphasize design research",
  duration: "6 hours",
  mode: "Reversible Lab",
  expiresAt: "SIX HOURS",
});
assert.equal(liveVisa.data.visa.mode, "Reversible Lab");
const activeLiveVisa = (await apiJson("/api/demo")).overlays.find(
  (item) => item.id === liveVisa.data.visa.id,
);
assert.equal(activeLiveVisa.activations.length, 1);
assert.equal(activeLiveVisa.activations[0].status, "active");
await feedPassportApi.revokeTemporaryVisa(liveVisa.data.visa.id);
const revokedLiveVisa = (await apiJson("/api/demo")).overlays.find(
  (item) => item.id === liveVisa.data.visa.id,
);
assert.equal(revokedLiveVisa.status, "revoked");
assert.equal(revokedLiveVisa.activations[0].status, "rolled_back");
pass("reversible-lab-visa-and-rollback");

for (const [index, mode] of ["Bridge View", "Weighted Mix", "Common Ground", "Taste Swap"].entries()) {
  const ownerShare = {
    topics: true,
    creators: index % 2 === 1,
    serendipity: true,
    exclusions: true,
    formats: false,
  };
  const partnerShare = {
    topics: mode === "Common Ground",
    creators: false,
    serendipity: false,
    exclusions: false,
    formats: true,
  };
  const invitation = await feedPassportApi.createCompanionInvitation({
    share: ownerShare,
    partnerCode: `SMOKE-${index + 1}936`,
    mode,
    weight: 30,
    duration: "48 hours",
  });
  assert.equal(invitation.source, "service");
  assert.equal(invitation.data.activationPerformed, false);
  assert.equal(invitation.data.invitation.status, "Awaiting second consent");
  assert.equal(invitation.data.invitation.scope, "continuous");
  assert.equal(invitation.data.invitation.refreshOnRevision, true);
  assert.equal(invitation.data.invitation.ownerConsent.scope, "continuous");
  assert.equal(invitation.data.invitation.ownerConsent.refreshOnRevision, true);
  assert.equal(invitation.data.invitation.ownerConsent.selectedFields.include_serendipity, true);

  hydrated = await feedPassportApi.listState();
  assert.equal(hydrated.source, "service");
  assert.equal(hydrated.data.activeCompanion, null);
  assert.equal(hydrated.data.pendingCompanionConsent.id, invitation.data.invitation.id);

  const afterFirstConsent = await apiJson("/api/demo");
  const ownerSlice = afterFirstConsent.shares.find(
    (item) => item.id === invitation.data.invitation.ownerConsent.id,
  );
  assert.ok(ownerSlice, "The first principal's persisted consent slice is missing");
  assert.equal(ownerSlice.owner_id, firstState.data.ownerId);
  assert.equal(ownerSlice.passport_id, firstState.data.activePassportId);
  assert.equal(ownerSlice.scope, "continuous");
  assert.equal(ownerSlice.refresh_on_revision, true);
  assert.deepEqual(ownerSlice.target_passport_ids, [firstState.data.activePassportId]);
  assert.equal(ownerSlice.selected_fields.include_serendipity, true);
  assert.equal(
    afterFirstConsent.companions.some((item) => (item.slice_ids || []).includes(ownerSlice.id)),
    false,
    "A companion was activated before the second principal consented",
  );

  const activated = await feedPassportApi.acceptCompanionInvitation({
    invitationId: invitation.data.invitation.id,
    share: partnerShare,
  });
  const companion = activated.data.companion;
  assert.equal(activated.source, "service");
  assert.equal(companion.mode, mode);
  assert.equal(companion.scope, "continuous");
  assert.equal(companion.refreshOnRevision, true);
  assert.equal(companion.syncRevision, 1);
  assert.ok(Number.isFinite(companion.effectiveWeight));
  assert.notEqual(companion.ownerPrincipalId, companion.partnerPrincipalId);
  assert.notEqual(companion.ownerPassportId, companion.partnerPassportId);
  assert.equal(companion.ownerSelectedFields.include_serendipity, true);
  assert.equal(companion.partnerSelectedFields.include_serendipity, false);
  assert.equal(companion.ownerSelectedFields.include_exclusions, true);
  assert.equal(companion.partnerSelectedFields.include_formats, true);

  hydrated = await feedPassportApi.listState();
  assert.equal(hydrated.data.activeCompanion.id, companion.id);
  assert.equal(hydrated.data.activeCompanion.scope, "continuous");
  assert.equal(hydrated.data.activeCompanion.syncRevision, 1);

  const afterActivation = await apiJson("/api/demo");
  const persistedCompanion = afterActivation.companions.find((item) => item.id === companion.id);
  assert.ok(persistedCompanion, "The continuous companion projection is missing");
  assert.equal(persistedCompanion.scope, "continuous");
  assert.equal(persistedCompanion.refresh_on_revision, true);
  assert.equal(persistedCompanion.sync_revision, 1);
  assert.equal(persistedCompanion.slice_ids.length, 2);
  assert.equal(new Set(persistedCompanion.consent_ids).size, 2);
  assert.equal(new Set(persistedCompanion.participant_ids).size, 2);
  const participantSlices = persistedCompanion.slice_ids.map((sliceId) =>
    afterActivation.shares.find((item) => item.id === sliceId),
  );
  assert.equal(participantSlices.every(Boolean), true);
  assert.equal(new Set(participantSlices.map((item) => item.owner_id)).size, 2);
  assert.equal(participantSlices.every((item) => item.scope === "continuous"), true);
  assert.equal(participantSlices.every((item) => item.refresh_on_revision === true), true);
  assert.equal(
    participantSlices.every(
      (item) => item.target_passport_ids.length === 1 && item.target_passport_ids[0] === item.passport_id,
    ),
    true,
  );

  let ownerBaseBeforeRefresh = null;
  if (index === 0) {
    const ownerBeforeRefresh = await apiJson(`/api/passports/${encodeURIComponent(companion.ownerPassportId)}`);
    const partnerBeforeRefresh = await apiJson(`/api/passports/${encodeURIComponent(companion.partnerPassportId)}`);
    ownerBaseBeforeRefresh = structuredClone(ownerBeforeRefresh.base);
    const refreshProbe = "companion_refresh_probe";
    await apiJson(`/api/passports/${encodeURIComponent(companion.partnerPassportId)}`, {
      method: "PATCH",
      body: JSON.stringify({
        actor_id: companion.partnerPrincipalId,
        changes: {
          format_preferences: {
            ...partnerBeforeRefresh.base.format_preferences,
            [refreshProbe]: 0.91,
          },
        },
      }),
    });
    const afterRevision = await apiJson("/api/demo");
    const refreshedCompanion = afterRevision.companions.find((item) => item.id === companion.id);
    assert.equal(refreshedCompanion.sync_revision, 2);
    assert.equal(
      refreshedCompanion.source_passport_versions[companion.partnerPassportId],
      partnerBeforeRefresh.base.version + 1,
    );
    const refreshedPartnerSlice = afterRevision.shares.find(
      (item) => item.passport_id === companion.partnerPassportId && persistedCompanion.slice_ids.includes(item.id),
    );
    assert.equal(refreshedPartnerSlice.passport_version, partnerBeforeRefresh.base.version + 1);
    const ownerDuringSync = await apiJson(`/api/passports/${encodeURIComponent(companion.ownerPassportId)}`);
    assert.deepEqual(ownerDuringSync.base, ownerBaseBeforeRefresh);
    assert.ok(
      Number(ownerDuringSync.effective.format_preferences[refreshProbe]) > 0,
      "The partner's consented format revision did not refresh the owner's effective projection",
    );
    hydrated = await feedPassportApi.listState();
    assert.equal(hydrated.data.activeCompanion.syncRevision, 2);
  }

  await feedPassportApi.revokeCompanion(companion);
  hydrated = await feedPassportApi.listState();
  assert.equal(hydrated.data.activeCompanion, null);
  const afterRevocation = await apiJson("/api/demo");
  assert.equal(
    afterRevocation.companions.find((item) => item.id === companion.id).status,
    "consent_invalidated",
  );
  if (ownerBaseBeforeRefresh) {
    const ownerAfterRevocation = await apiJson(`/api/passports/${encodeURIComponent(companion.ownerPassportId)}`);
    assert.deepEqual(ownerAfterRevocation.base, ownerBaseBeforeRefresh);
    assert.equal("companion_refresh_probe" in ownerAfterRevocation.effective.format_preferences, false);
  }
}
pass("two-person-continuous-companion-sync-and-revoke");

const drift = await feedPassportApi.checkDrift();
assert.ok(Number.isFinite(drift.data.score));
const correction = await feedPassportApi.applyDriftCorrection();
assert.ok(correction.data.receipt.id);
const monitor = await feedPassportApi.createDriftMonitor({
  platform: "lab",
  mode: "bounded_auto",
  intervalMinutes: 15,
  duration: "48 hours",
  allowedActions: [
    "follow_creator",
    "mute_creator",
    "hide_topic",
    "set_topic_preference",
    "set_serendipity",
    "set_source_cap",
  ],
  maxActionsPerRun: 3,
  minimumConfidence: 0.8,
});
assert.equal(monitor.data.status, "active");
assert.equal((await feedPassportApi.stopDriftMonitor(monitor.data.id)).data.status, "stopped");
pass("drift-check-correction-monitor-and-stop");

const creator = await feedPassportApi.preserveCreator({
  id: "studio-a",
  name: "Studio A",
  source: "Bluesky",
  destination: "YouTube",
  destinationHandle: "@studio-a",
  confidence: 100,
});
assert.equal(creator.data.creator.destinationHandle, "@studio-a");
pass("creator-continuity");

const exported = await feedPassportApi.exportPassport(restored.data.constitution);
assert.equal(exported.data.format, "feed-passport/v1");
assert.equal("raw_history" in exported.data.passport, false);
const imported = await feedPassportApi.importPassport(exported.data);
assert.equal(imported.data.constitution.version, 1);
assert.notEqual(imported.data.passport.id, exported.data.passport.id);
pass("strict-export-and-new-identity-import");

const missionPreview = await feedPassportApi.previewAgentMission({
  platform: "twin:x",
  accountId: "destination-new",
  goal: "Improve this account-free local control twin within two reversible actions.",
  maxIterations: 2,
  maxTotalActions: 2,
  maxActionsPerIteration: 1,
  maxTopicDistance: 0,
  maxUnwantedRate: 0,
  maxSourceConcentration: 0,
  minSerendipity: 1,
  maxSerendipity: 1,
  minImprovement: 0,
});
assert.equal(missionPreview.source, "service");
assert.equal(missionPreview.data.status, "awaiting_approval");
assert.equal(missionPreview.data.environment, "local_platform_control_twin");
assert.equal(missionPreview.data.platform, "twin:x");
assert.ok(missionPreview.data.action_envelope.length > 0);
assert.match(missionPreview.data.fidelity_disclaimer, /does not reproduce/i);

const missionExecution = await feedPassportApi.runAgentMission(missionPreview.data.id);
assert.equal(missionExecution.source, "service");
assert.ok(["completed", "needs_human"].includes(missionExecution.data.status));
assert.equal(missionExecution.data.receipt_ids.length, 2);
assert.ok(missionExecution.data.iterations.length <= 2);
assert.ok(
  missionExecution.data.iterations.every((iteration) => iteration.submitted_action_count <= 1),
);
assert.ok(
  missionExecution.data.iterations.reduce(
    (total, iteration) => total + iteration.submitted_action_count,
    0,
  ) <= 2,
);
const missionStages = new Set(missionExecution.data.trace.map((item) => item.stage));
for (const stage of [
  "observe",
  "evaluate",
  "plan",
  "policy",
  "consent",
  "act",
  "reobserve",
  "adapt",
  "receipt",
]) {
  assert.equal(missionStages.has(stage), true, `agent mission trace is missing ${stage}`);
}

const missionRollback = await feedPassportApi.rollbackAgentMission(missionPreview.data.id);
assert.equal(missionRollback.source, "service");
assert.equal(missionRollback.data.status, "rolled_back");
assert.deepEqual(
  missionRollback.data.rollback.receipt_order,
  [...missionExecution.data.receipt_ids].reverse(),
);
assert.equal(missionRollback.data.rollback.failure_count, 0);
assert.equal(missionRollback.data.rollback.verification.status, "completed");
assert.equal(missionRollback.data.rollback.verification.state_restored, true);
assert.equal(missionRollback.data.rollback.verification.evaluation_status, "completed");
assert.deepEqual(
  missionRollback.data.rollback.verification.expected_fingerprint,
  missionRollback.data.rollback.verification.observed_fingerprint,
);
assert.deepEqual(
  missionRollback.data.rollback.verification.expected_fingerprint,
  missionExecution.data.rollback_baseline.fingerprint,
);
assert.equal(missionRollback.data.rollback_available, false);
pass("bounded-local-agent-mission-and-rollback");

const agent = await feedPassportApi.runAgentCommand({
  command: "Preview copying my current feed policy to YouTube",
  context: { constitutionVersion: imported.data.constitution.version },
});
assert.ok(agent.data.response);
pass("deterministic-agent-command");

const captured = await feedPassportApi.capturePassport({
  source: "x",
  name: "Captured X declared demo snapshot",
  intent: "Prove portable capture from a declared dummy-account observation without claiming live access.",
});
assert.equal(captured.source, "service");
assert.deepEqual(captured.data.passport.topic_targets, {
  breaking_news: 0.45,
  policy: 0.25,
  security: 0.3,
});
assert.equal(captured.data.passport.provenance[0].source, "synthetic-demo-normalized-snapshot");
assert.match(captured.data.passport.provenance[0].reference, /^synthetic-demo-normalized-snapshot:x:account:/);
assert.notEqual(captured.data.passportId, imported.data.passport.id);
pass("external-declared-snapshot-capture");

process.stdout.write(`${JSON.stringify({ passed: checks.length, checks }, null, 2)}\n`);
