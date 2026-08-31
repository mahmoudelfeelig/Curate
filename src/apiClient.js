import {
  DRIFT_FIXTURE,
  INITIAL_CONSTITUTION,
  MIGRATION_PREVIEW,
} from "./data.js";
import {
  actionLabel,
  clone,
  companionStrategy,
  continuousShareBody,
  destinationAccount,
  deterministicPairId,
  deterministicPartnerId,
  durationLabel,
  formatDateLabel,
  futureIso,
  futureIsoForPayload,
  invitationProjection,
  migrationPreviewForUi,
  normalizeLanguage,
  normalizePlatform,
  overlayMode,
  overlayProjectionForUi,
  projectCompanionForUi,
  projectDriftForUi,
  serverConstitution,
  serverReceipt,
  shareFromSelectedFields,
  shareSelectionForPassport,
  topicAdjustmentsForPurpose,
  uiTopicTargets,
} from "./api/clientProjections.js";
import {
  agentArgumentsForCommand,
  classifyAgentCommand,
  fixtureAgentResponseForCommand,
} from "./api/agentCommands.js";
import {
  executeFixtureMission,
  missionRollbackIsVerified,
  previewFixtureMission,
} from "./api/agentMissions.js";

export { classifyAgentCommand, missionRollbackIsVerified };

const FIXTURE_EPOCH = Date.UTC(2026, 7, 29, 10, 15, 0);
const CREATOR_DIRECTORY_MAP = {
  "studio-a": { source: "studio-a", destination: "youtube" },
  "paper-lab": { source: "paper-lab", destination: "youtube" },
  "city-zine": { source: "city.zine", destination: "bluesky" },
  "studio-a-x": { source: "studio-a", destination: "x" },
};
const env = import.meta.env || {};
const configuredApiBase =
  env.VITE_CURATOR_API_URL ||
  env.VITE_FEED_PASSPORT_API_BASE ||
  globalThis.__CURATOR_API_URL__ ||
  globalThis.__FEED_PASSPORT_API_BASE__ ||
  "";

let fixtureSequence = 0;
let serviceAvailable = Boolean(configuredApiBase);
const runtime = {
  demo: null,
  passport: null,
  passportId: null,
  actorId: null,
  migrations: new Map(),
  visas: new Map(),
  companions: new Map(),
  companionInvitations: new Map(),
  fixtureShares: new Map(),
  fixturePartnerPassports: new Map(),
  driftMonitors: new Map(),
  creatorLinks: new Map(),
  agentMissions: new Map(),
  lastDrift: null,
  fixturePassportId: "FP-74128",
  fixtureOwnerId: "fixture-owner",
  fixtureConstitution: clone(INITIAL_CONSTITUTION),
  fixtureCheckpoints: new Map(),
};

function companionProjectionForUi(companion, codeOverride = null, context = {}) {
  return projectCompanionForUi(companion, codeOverride, {
    ownerPrincipalId: context.ownerPrincipalId || runtime.actorId,
    ownerPassportId: context.ownerPassportId || runtime.passportId,
    fixtureOwnerId: runtime.fixtureOwnerId,
    fixturePassportId: runtime.fixturePassportId,
  });
}

function driftForUi(raw) {
  return projectDriftForUi(raw, runtime.passport);
}

function agentArguments(commandName, command) {
  return agentArgumentsForCommand(commandName, command, runtime.passportId);
}

function fixtureAgentResponse(command) {
  return fixtureAgentResponseForCommand(command, nextFixtureIdentity);
}

function fixtureMissionPreview(spec) {
  return previewFixtureMission(spec, {
    missions: runtime.agentMissions,
    nextFixtureIdentity,
  });
}

function fixtureMissionExecution(missionId) {
  return executeFixtureMission(missionId, {
    missions: runtime.agentMissions,
    nextFixtureIdentity,
    createNotFoundError: () => new CuratorApiError(404, "Local mission was not found", null),
  });
}

class CuratorApiError extends Error {
  constructor(status, message, payload) {
    super(message);
    this.name = "CuratorApiError";
    this.status = status;
    this.payload = payload;
  }
}

function nextFixtureIdentity(prefix = "RCPT") {
  fixtureSequence += 1;
  const at = new Date(FIXTURE_EPOCH + fixtureSequence * 60_000);
  const id = `${prefix}-${String(1100 + fixtureSequence).padStart(4, "0")}`;
  return {
    id,
    iso: at.toISOString(),
    label: formatDateLabel(at),
  };
}

function fixtureReceipt(type, detail, extra = {}) {
  const identity = nextFixtureIdentity();
  return {
    id: identity.id,
    type,
    detail,
    time: identity.label,
    status: "Succeeded",
    reversible: true,
    checkpoint: "v3",
    completedAt: identity.iso,
    ...extra,
  };
}

function serviceUrl(path) {
  const base = configuredApiBase.replace(/\/$/, "");
  if (!base) return path;
  if (/\/api$/i.test(base) && path === "/health") {
    return `${base.slice(0, -4)}/health`;
  }
  if (/\/api$/i.test(base) && path.startsWith("/api/")) {
    return `${base}${path.slice(4)}`;
  }
  return `${base}${path}`;
}

async function requestJson(path, requestOptions = {}) {
  const { timeoutMs = 3500, ...options } = requestOptions;
  const controller = new AbortController();
  const timeout = globalThis.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(serviceUrl(path), {
      ...options,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json")
      ? await response.json()
      : await response.text();
    if (!response.ok) {
      const detail = payload && typeof payload === "object"
        ? payload.detail || payload.error || JSON.stringify(payload)
        : String(payload || response.statusText);
      throw new CuratorApiError(response.status, detail, payload);
    }
    if (!contentType.includes("application/json")) {
      throw new CuratorApiError(response.status, "Curator API returned a non-JSON response", payload);
    }
    return payload;
  } finally {
    globalThis.clearTimeout(timeout);
  }
}

function agentMissionRequestBody(spec) {
  return {
    actor_id: runtime.actorId || runtime.fixtureOwnerId,
    passport_id: runtime.passportId || runtime.fixturePassportId,
    platform: String(spec.platform || "twin:youtube").startsWith("twin:") ? spec.platform : `twin:${spec.platform}`,
    account_id: spec.accountId || "destination-new",
    goal: spec.goal,
    max_iterations: Number(spec.maxIterations || 3),
    max_total_actions: Number(spec.maxTotalActions || 6),
    max_actions_per_iteration: Number(spec.maxActionsPerIteration || 3),
    allowed_actions: spec.allowedActions || [],
    acceptance: {
      max_topic_distance: Number(spec.maxTopicDistance ?? 0.18),
      max_unwanted_rate: Number(spec.maxUnwantedRate ?? 0.05),
      max_source_concentration: Number(spec.maxSourceConcentration ?? 0.45),
      min_serendipity: Number(spec.minSerendipity ?? 0.12),
      max_serendipity: Number(spec.maxSerendipity ?? 0.28),
    },
    min_improvement: Number(spec.minImprovement ?? 0.02),
  };
}

function isConnectivityFailure(error) {
  return !(error instanceof CuratorApiError);
}

async function withFixtureFallback(serviceWork, fixtureWork, { mutation = false } = {}) {
  if (!serviceAvailable) {
    return { source: "fixture", data: await fixtureWork() };
  }
  try {
    await ensureServiceContext();
  } catch (error) {
    if (!isConnectivityFailure(error)) throw error;
    serviceAvailable = false;
    return { source: "fixture", data: await fixtureWork() };
  }
  try {
    return { source: "service", data: await serviceWork() };
  } catch (error) {
    if (!isConnectivityFailure(error)) throw error;
    serviceAvailable = false;
    if (mutation) {
      throw new CuratorApiError(
        0,
        "The Curator service response was interrupted. The outcome is unknown; refresh state before retrying.",
        { outcome: "unknown", retry_safe: false },
      );
    }
    return { source: "fixture", data: await fixtureWork() };
  }
}

function withMutationFallback(serviceWork, fixtureWork) {
  return withFixtureFallback(serviceWork, fixtureWork, { mutation: true });
}

function resetPassportRuntime() {
  runtime.demo = null;
  runtime.migrations.clear();
  runtime.visas.clear();
  runtime.companions.clear();
  runtime.companionInvitations.clear();
  runtime.fixtureShares.clear();
  runtime.fixturePartnerPassports.clear();
  runtime.driftMonitors.clear();
  runtime.creatorLinks.clear();
  runtime.agentMissions.clear();
  runtime.lastDrift = null;
  runtime.fixtureCheckpoints.clear();
}

function activateServicePassport(passport) {
  resetPassportRuntime();
  runtime.passport = passport;
  runtime.passportId = passport.id;
  runtime.actorId = passport.owner_id;
  runtime.demo = {
    passports: [passport],
    platforms: [],
    templates: [],
    overlays: [],
    shares: [],
    companions: [],
    checkpoints: [],
    migrations: [],
    receipts: [],
    drift_alerts: [],
    drift_monitors: [],
    creator_links: [],
  };
}

function ingestDemo(demo) {
  const passports = demo?.passports || [];
  const passport = passports.find((item) => item.id === runtime.passportId)
    || passports.find((item) => item.owner_id === runtime.actorId)
    || passports.find((item) => item.owner_id === "demo-owner")
    || passports[0];
  if (!passport) throw new CuratorApiError(404, "The Curator API has no Passport to operate on", demo);
  runtime.demo = demo;
  runtime.passport = passport;
  runtime.passportId = passport.id;
  runtime.actorId = passport.owner_id;
  runtime.migrations.clear();
  runtime.visas.clear();
  runtime.companions.clear();
  const existingInvitations = new Map(runtime.companionInvitations);
  runtime.companionInvitations.clear();
  for (const migration of demo.migrations || []) runtime.migrations.set(migration.id, migration);
  for (const visa of demo.overlays || []) runtime.visas.set(visa.id, visa);
  for (const companion of demo.companions || []) {
    const ownSlice = (demo.shares || []).find(
      (item) => (companion.slice_ids || []).includes(item.id) && item.owner_id === runtime.actorId,
    );
    const partnerSlice = (demo.shares || []).find(
      (item) => (companion.slice_ids || []).includes(item.id) && item.owner_id !== runtime.actorId,
    );
    runtime.companions.set(companion.id, {
      blend: companion,
      ownSliceId: ownSlice?.id,
      partnerSliceId: partnerSlice?.id,
    });
  }
  const usedSliceIds = new Set(
    (demo.companions || []).flatMap((companion) => companion.slice_ids || []),
  );
  for (const slice of demo.shares || []) {
    if (
      slice.owner_id !== runtime.actorId
      || slice.scope !== "continuous"
      || slice.status !== "active"
      || usedSliceIds.has(slice.id)
    ) continue;
    const existing = existingInvitations.get(slice.id);
    const payload = existing?.payload || {
      partnerCode: `RESTORED-${String(slice.id).slice(-8).toUpperCase()}`,
      share: shareFromSelectedFields(slice.selected_fields),
      mode: "Bridge View",
      weight: 30,
      duration: durationLabel(slice.created_at, slice.expires_at),
    };
    const invitation = invitationProjection({ slice, payload, ownerPrincipalId: runtime.actorId });
    runtime.companionInvitations.set(slice.id, { invitation, ownerSlice: slice, payload });
  }
  return {
    passport: serverConstitution(passport),
    passportId: passport.id,
    ownerId: passport.owner_id,
    receipts: (demo.receipts || []).map((item) => serverReceipt(item, demo)),
    health: "ready",
  };
}

async function ensureServiceContext() {
  if (!serviceAvailable || runtime.passportId) return;
  ingestDemo(await requestJson("/api/demo", { method: "GET" }));
}

async function previewMigrationOnService({ destination }) {
  const platform = normalizePlatform(destination);
  const migration = await requestJson("/api/migrations/preview", {
    method: "POST",
    body: JSON.stringify({
      actor_id: runtime.actorId,
      passport_id: runtime.passportId,
      platform,
      destination_account_id: destinationAccount(platform),
    }),
  });
  runtime.migrations.set(migration.id, migration);
  return migration;
}

async function approveAndExecuteMigration(migration) {
  const actionCount = migration.plan?.actions?.length || 0;
  if (!actionCount) return null;
  const approval = await requestJson(`/api/migrations/${encodeURIComponent(migration.id)}/approval`, {
    method: "POST",
    body: JSON.stringify({
      actor_id: runtime.actorId,
      max_total_actions: actionCount,
      ttl_seconds: 600,
    }),
  });
  const executed = await requestJson(`/api/migrations/${encodeURIComponent(migration.id)}/execute`, {
    method: "POST",
    body: JSON.stringify({
      actor_id: runtime.actorId,
      approval_token: approval.token || approval.approval_token,
    }),
  });
  runtime.migrations.set(migration.id, executed);
  return executed;
}

function companionPairBinding(record, expectedOwnerId) {
  const ownerSlice = record?.ownerSlice;
  const ownerId = String(expectedOwnerId || "").trim();
  const pairId = String(ownerSlice?.pair_id || "").trim();
  const counterpartyOwnerId = String(ownerSlice?.counterparty_owner_id || "").trim();
  const participantOwnerIds = Array.isArray(ownerSlice?.participant_owner_ids)
    ? ownerSlice.participant_owner_ids.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  const expectedParticipantOwnerIds = [ownerId, counterpartyOwnerId].sort();
  const validBinding = Boolean(
    ownerId
    && String(ownerSlice?.owner_id || "").trim() === ownerId
    && ownerSlice?.scope === "continuous"
    && ownerSlice?.status === "active"
    && pairId
    && counterpartyOwnerId
    && counterpartyOwnerId !== ownerId
    && participantOwnerIds.length === 2
    && new Set(participantOwnerIds).size === 2
    && participantOwnerIds.slice().sort().every(
      (item, index) => item === expectedParticipantOwnerIds[index],
    )
  );
  if (!validBinding) {
    throw new CuratorApiError(
      409,
      "The pending companion consent is missing a valid pair binding",
      ownerSlice || null,
    );
  }
  return { pairId, counterpartyOwnerId, participantOwnerIds: expectedParticipantOwnerIds };
}

async function partnerPassport(payload, ownerId) {
  const existing = (runtime.demo?.passports || []).find((item) => item.owner_id === ownerId);
  if (existing) return existing;
  const baseTargets = runtime.passport.topic_targets || { research: 0.4, design: 0.3, local: 0.2, indie: 0.1 };
  const created = await requestJson("/api/passports", {
    method: "POST",
    body: JSON.stringify({
      owner_id: ownerId,
      name: `Companion ${payload.partnerCode}`,
      intent: "An explicit demo companion slice with no credentials or raw activity history.",
      topic_targets: baseTargets,
      creator_preferences: payload.share.creators ? { "radio-night": 1.0 } : {},
      format_preferences: payload.share.formats ? { longform: 0.65, short_video: -0.25 } : {},
      languages: runtime.passport.languages || ["en"],
      hard_exclusions: payload.share.exclusions ? ["ragebait", "spoilers"] : [],
      serendipity: runtime.passport.serendipity ?? 0.2,
      max_outrage: runtime.passport.max_outrage ?? 0.05,
      max_source_share: runtime.passport.max_source_share ?? 0.4,
    }),
  });
  runtime.demo.passports.push(created);
  return created;
}

function fixtureOwnerPassport() {
  return {
    id: runtime.fixturePassportId,
    owner_id: runtime.fixtureOwnerId,
    version: Number(runtime.fixtureConstitution.version || 1),
    topic_targets: uiTopicTargets(runtime.fixtureConstitution),
    creator_preferences: {},
    format_preferences: {},
    languages: ["en"],
    hard_exclusions: [],
    serendipity: Number(runtime.fixtureConstitution.serendipity || 20) / 100,
    max_outrage: Number(runtime.fixtureConstitution.outrageCeiling || 5) / 100,
    max_source_share: Number(runtime.fixtureConstitution.creatorCeiling || 15) / 100,
  };
}

function fixturePartnerPassport(payload, ownerId) {
  const existing = runtime.fixturePartnerPassports.get(ownerId);
  if (existing) return existing;
  const baseTargets = uiTopicTargets(runtime.fixtureConstitution);
  const created = {
    id: `fixture-passport-${ownerId.replace(/^demo-partner-/, "")}`,
    owner_id: ownerId,
    version: 1,
    name: `Companion ${payload.partnerCode}`,
    intent: "A second local test principal's explicit demo Passport; no social account or production authentication is represented.",
    topic_targets: Object.keys(baseTargets).length ? baseTargets : { research: 0.4, design: 0.3, local: 0.2, indie: 0.1 },
    creator_preferences: payload.share.creators ? { "radio-night": 1.0 } : {},
    format_preferences: payload.share.formats ? { longform: 0.65, short_video: -0.25 } : {},
    languages: ["en"],
    hard_exclusions: payload.share.exclusions ? ["ragebait", "spoilers"] : [],
    serendipity: Number(runtime.fixtureConstitution.serendipity || 20) / 100,
    max_outrage: Number(runtime.fixtureConstitution.outrageCeiling || 5) / 100,
    max_source_share: Number(runtime.fixtureConstitution.creatorCeiling || 15) / 100,
  };
  runtime.fixturePartnerPassports.set(ownerId, created);
  return created;
}

function fixtureContinuousSlice({
  actorId,
  passport,
  share,
  expiresAt,
  pairId,
  counterpartyOwnerId,
}) {
  const identity = nextFixtureIdentity("SLICE");
  const slice = {
    id: identity.id,
    consent_id: `CONSENT-${identity.id}`,
    owner_id: actorId,
    passport_id: passport.id,
    passport_version: Number(passport.version || 1),
    selected_fields: shareSelectionForPassport(passport, share),
    status: "active",
    scope: "continuous",
    refresh_on_revision: true,
    target_passport_ids: [passport.id],
    pair_id: pairId,
    counterparty_owner_id: counterpartyOwnerId,
    participant_owner_ids: [actorId, counterpartyOwnerId].sort(),
    created_at: identity.iso,
    expires_at: expiresAt,
    revoked_at: null,
  };
  runtime.fixtureShares.set(slice.id, slice);
  return slice;
}

export const feedPassportApi = {
  async loadPassport() {
    if (!serviceAvailable) {
      return {
        source: "fixture",
        data: { passport: clone(runtime.fixtureConstitution), receipts: clone([]), health: "ready", scheduler: "fixture" },
      };
    }
    try {
      const demo = await requestJson("/api/demo", { method: "GET" });
      let health = { scheduler: "unavailable" };
      try {
        health = await requestJson("/health", { method: "GET" });
      } catch {
        // A healthy demo endpoint remains usable even if deployment health
        // metadata is unavailable through a proxy or older service version.
      }
      return { source: "service", data: { ...ingestDemo(demo), scheduler: health.scheduler || "unknown" } };
    } catch (error) {
      if (!isConnectivityFailure(error)) throw error;
      serviceAvailable = false;
      return {
        source: "fixture",
        data: { passport: clone(runtime.fixtureConstitution), receipts: clone([]), health: "ready", scheduler: "fixture" },
      };
    }
  },

  listState() {
    return withFixtureFallback(
      async () => {
        const demo = await requestJson("/api/demo", { method: "GET" });
        const ui = ingestDemo(demo);
        const activeVisas = (demo.overlays || [])
          .filter((item) => item.base_passport_id === ui.passportId)
          .map(overlayProjectionForUi);
        const activeCompanion = (demo.companions || []).find(
          (item) => item.status === "active" && (item.participant_ids || []).includes(ui.ownerId),
        );
        const pendingCompanionConsent = [...runtime.companionInvitations.values()]
          .find((record) => record.invitation.status === "Awaiting second consent")?.invitation || null;
        return {
          ...demo,
          activePassport: ui.passport,
          activePassportId: ui.passportId,
          ownerId: ui.ownerId,
          activeVisas,
          activeCompanion: activeCompanion ? companionProjectionForUi(activeCompanion) : null,
          pendingCompanionConsent: pendingCompanionConsent ? clone(pendingCompanionConsent) : null,
        };
      },
      () => {
        const activePassport = clone(runtime.fixtureConstitution);
        const activeVisas = [...runtime.visas.values()]
          .filter((visa) => String(visa.status).toLowerCase() === "active")
          .map(clone);
        const activeCompanion = [...runtime.companions.values()]
          .find((companion) => String(companion.status).toLowerCase() === "active") || null;
        const pendingCompanionConsent = [...runtime.companionInvitations.values()]
          .find((record) => record.invitation.status === "Awaiting second consent")?.invitation || null;
        return {
        passports: [activePassport],
        platforms: [],
        templates: [],
        overlays: activeVisas,
        shares: [...runtime.fixtureShares.values()].map(clone),
        companions: [...runtime.companions.values()].map(clone),
        checkpoints: [],
        migrations: [],
        receipts: [],
        drift_alerts: [],
        drift_monitors: [...runtime.driftMonitors.values()].map(clone),
        creator_links: [...runtime.creatorLinks.values()].map(clone),
        activeVisas,
        activeCompanion: activeCompanion ? clone(activeCompanion) : null,
        pendingCompanionConsent: pendingCompanionConsent ? clone(pendingCompanionConsent) : null,
        activePassport,
        activePassportId: runtime.fixturePassportId,
        ownerId: runtime.fixtureOwnerId,
        };
      },
    );
  },

  capturePassport({ source, name, intent }) {
    return withMutationFallback(
      async () => {
        const platform = normalizePlatform(source || "lab");
        const accountId = platform === "feed_passport_lab" ? "source-main" : destinationAccount(platform);
        const captured = await requestJson("/api/passports/capture", {
          method: "POST",
          body: JSON.stringify({
            platform,
            account_id: accountId,
            owner_id: runtime.actorId,
            name: name || `${actionLabel(platform)} source snapshot`,
            intent: intent || "A portable policy inferred from an explicitly authorized source observation.",
          }),
        });
        activateServicePassport(captured);
        const lab = platform === "feed_passport_lab";
        return {
          passport: captured,
          passportId: captured.id,
          constitution: serverConstitution(captured),
          receipt: {
            id: `CAPTURE-${captured.id}`,
            type: lab ? "Lab source captured" : "Declared demo snapshot captured",
            detail: lab
              ? "A new Passport was inferred from the seeded closed-loop Lab source account."
              : `${actionLabel(platform)} used its declared deterministic dummy-account snapshot; no live social account was accessed.`,
            time: formatDateLabel(captured.created_at),
            status: "Succeeded",
            reversible: false,
            checkpoint: `v${captured.version}`,
          },
        };
      },
      () => {
        resetPassportRuntime();
        runtime.fixturePassportId = nextFixtureIdentity("PASSPORT").id;
        runtime.fixtureConstitution = {
          ...clone(runtime.fixtureConstitution),
          version: 1,
          title: name || "Captured fixture Passport",
          intent: intent || runtime.fixtureConstitution.intent,
        };
        return {
          passport: {
            id: runtime.fixturePassportId,
            owner_id: runtime.fixtureOwnerId,
            version: 1,
          },
          passportId: runtime.fixturePassportId,
          constitution: clone(runtime.fixtureConstitution),
          receipt: fixtureReceipt(
            "Fixture source captured",
            `${actionLabel(normalizePlatform(source || "lab"))} was represented by a deterministic local fixture; no social account was accessed.`,
            { reversible: false, status: "Simulated" },
          ),
        };
      },
    );
  },

  exportPassport(constitution = runtime.fixtureConstitution) {
    return withFixtureFallback(
      () => requestJson(`/api/passports/${encodeURIComponent(runtime.passportId)}/export`, { method: "GET" }),
      () => ({
        format: "feed-passport/v1",
        passport: {
          id: runtime.fixturePassportId,
          owner_id: runtime.fixtureOwnerId,
          version: Number(constitution.version || 1),
          name: constitution.title,
          intent: constitution.intent,
          topic_targets: uiTopicTargets(constitution),
          creator_preferences: {},
          format_preferences: { longform: 0.9, short_video: -0.8 },
          languages: (constitution.languages || ["English"]).map(normalizeLanguage),
          hard_exclusions: ["ragebait"],
          serendipity: Number(constitution.serendipity || 0) / 100,
          max_outrage: Number(constitution.outrageCeiling || 0) / 100,
          max_source_share: Number(constitution.creatorCeiling || 0) / 100,
          created_at: new Date(FIXTURE_EPOCH).toISOString(),
          updated_at: new Date(FIXTURE_EPOCH).toISOString(),
        },
        privacy: "Preference intent only; no raw feed bodies, credentials, or private history are included.",
      }),
    );
  },

  importPassport(document) {
    return withMutationFallback(
      async () => {
        const imported = await requestJson("/api/passports/import", {
          method: "POST",
          body: JSON.stringify({
            actor_id: runtime.actorId,
            format: document.format || "feed-passport/v1",
            passport: document.passport || document,
          }),
        });
        const passport = imported.passport || imported;
        activateServicePassport(passport);
        return { ...imported, constitution: serverConstitution(passport) };
      },
      () => {
        if (document.format !== "feed-passport/v1" || !document.passport || typeof document.passport !== "object" || Array.isArray(document.passport)) {
          throw new CuratorApiError(422, "Import requires a strict feed-passport/v1 document", document);
        }
        const allowedFields = new Set([
          "id", "owner_id", "name", "version", "intent", "topic_targets",
          "creator_preferences", "format_preferences", "languages", "hard_exclusions",
          "serendipity", "max_outrage", "max_source_share", "created_at", "updated_at",
          "expires_at", "provenance",
        ]);
        const requiredFields = [
          "id", "owner_id", "name", "version", "intent", "topic_targets",
          "creator_preferences", "format_preferences", "languages", "hard_exclusions",
          "serendipity", "max_outrage", "max_source_share", "created_at", "updated_at",
        ];
        const unexpected = Object.keys(document.passport).filter((key) => !allowedFields.has(key));
        const missing = requiredFields.filter((key) => !(key in document.passport));
        if (unexpected.length || missing.length) {
          throw new CuratorApiError(422, `Import schema mismatch${unexpected.length ? `; unsupported: ${unexpected.join(", ")}` : ""}${missing.length ? `; missing: ${missing.join(", ")}` : ""}`, document);
        }
        if (document.passport.owner_id !== "fixture-owner") {
          throw new CuratorApiError(403, "Fixture imports must belong to the active fixture owner", document);
        }
        const sourcePassport = clone(document.passport);
        const imported = Array.isArray(sourcePassport.topics)
          ? sourcePassport
          : serverConstitution(sourcePassport, runtime.fixtureConstitution);
        resetPassportRuntime();
        runtime.fixtureConstitution = { ...imported, version: 1 };
        runtime.fixturePassportId = nextFixtureIdentity("PASSPORT").id;
        return {
          passport: { ...sourcePassport, id: runtime.fixturePassportId, version: 1 },
          constitution: clone(runtime.fixtureConstitution),
          source_passport_id: sourcePassport.id || runtime.fixturePassportId,
        };
      },
    );
  },

  createCheckpoint(label = "Manual Passport checkpoint") {
    return withMutationFallback(
      () => requestJson(`/api/passports/${encodeURIComponent(runtime.passportId)}/checkpoints`, {
        method: "POST",
        body: JSON.stringify({ actor_id: runtime.actorId, label }),
      }),
      () => {
        const identity = nextFixtureIdentity("CHECKPOINT");
        const checkpoint = {
          id: identity.id,
          passport_id: "FP-74128",
          passport_version: runtime.fixtureConstitution.version,
          owner_id: "fixture-owner",
          label,
          created_at: identity.iso,
        };
        runtime.fixtureCheckpoints.set(checkpoint.id, {
          checkpoint,
          constitution: clone(runtime.fixtureConstitution),
        });
        return checkpoint;
      },
    );
  },

  restoreCheckpoint(checkpointId) {
    return withMutationFallback(
      async () => {
        const restored = await requestJson(`/api/checkpoints/${encodeURIComponent(checkpointId)}/restore`, {
          method: "POST",
          body: JSON.stringify({ actor_id: runtime.actorId }),
        });
        runtime.passport = restored;
        runtime.passportId = restored.id;
        return { passport: restored, constitution: serverConstitution(restored) };
      },
      () => {
        const record = runtime.fixtureCheckpoints.get(checkpointId);
        if (!record) throw new CuratorApiError(404, "Fixture checkpoint was not found", { checkpointId });
        runtime.fixtureConstitution = {
          ...clone(record.constitution),
          version: Number(runtime.fixtureConstitution.version || 1) + 1,
        };
        return {
          passport: clone(runtime.fixtureConstitution),
          constitution: clone(runtime.fixtureConstitution),
        };
      },
    );
  },

  createDriftMonitor(options = {}) {
    return withMutationFallback(
      () => {
        const platform = normalizePlatform(options.platform || "lab");
        return requestJson("/api/drift/monitors", {
          method: "POST",
          body: JSON.stringify({
            actor_id: runtime.actorId,
            passport_id: runtime.passportId,
            platform,
            account_id: options.accountId || destinationAccount(platform),
            interval_minutes: options.intervalMinutes || 60,
            expires_at: futureIso(options.duration || "7 days"),
            mode: options.mode || "alert_only",
            allowed_actions: options.allowedActions || [],
            max_actions_per_run: options.maxActionsPerRun || 3,
            minimum_confidence: options.minimumConfidence || 0.8,
          }),
        });
      },
      () => {
        const monitor = {
          id: nextFixtureIdentity("MONITOR").id,
          passport_id: runtime.fixturePassportId,
          status: "active",
          platform: normalizePlatform(options.platform || "lab"),
          mode: options.mode || "alert_only",
        };
        runtime.driftMonitors.set(monitor.id, monitor);
        return clone(monitor);
      },
    );
  },

  stopDriftMonitor(monitorId) {
    return withMutationFallback(
      () => requestJson(`/api/drift/monitors/${encodeURIComponent(monitorId)}/stop`, {
        method: "POST",
        body: JSON.stringify({ actor_id: runtime.actorId }),
      }),
      () => {
        const stopped = {
          ...(runtime.driftMonitors.get(monitorId) || { id: monitorId }),
          status: "stopped",
          stopped_at: new Date(FIXTURE_EPOCH).toISOString(),
        };
        runtime.driftMonitors.set(monitorId, stopped);
        return clone(stopped);
      },
    );
  },

  saveConstitution(constitution) {
    return withMutationFallback(
      async () => {
        const revised = await requestJson(`/api/passports/${encodeURIComponent(runtime.passportId)}`, {
          method: "PATCH",
          body: JSON.stringify({
            actor_id: runtime.actorId,
            changes: {
              name: constitution.title,
              intent: constitution.intent,
              topic_targets: uiTopicTargets(constitution),
              languages: (constitution.languages || ["English"]).map(normalizeLanguage),
              serendipity: Number(constitution.serendipity) / 100,
              max_outrage: Number(constitution.outrageCeiling) / 100,
              max_source_share: Number(constitution.creatorCeiling) / 100,
            },
          }),
        });
        runtime.passport = revised;
        return {
          constitution: serverConstitution(revised, constitution),
          receipt: {
            id: `REV-${revised.id}-V${revised.version}`,
            type: "Constitution saved",
            detail: `Version ${revised.version} became the active portable policy through the Curator API.`,
            time: formatDateLabel(revised.updated_at),
            status: "Succeeded",
            reversible: false,
            checkpoint: `v${revised.version}`,
          },
        };
      },
      () => {
        const previousConstitution = clone(runtime.fixtureConstitution);
        runtime.fixtureConstitution = clone(constitution);
        return {
          constitution: clone(runtime.fixtureConstitution),
          receipt: fixtureReceipt(
            "Constitution saved",
            `Version ${constitution.version} became the active portable policy.`,
            { _previousConstitution: previousConstitution },
          ),
        };
      },
    );
  },

  issuePassport({ destinations, expiry }) {
    return withMutationFallback(
      async () => {
        const checkpoint = await requestJson(`/api/passports/${encodeURIComponent(runtime.passportId)}/checkpoints`, {
          method: "POST",
          body: JSON.stringify({
            actor_id: runtime.actorId,
            label: `Issued for ${destinations.map(normalizePlatform).join(", ")} · ${expiry}`,
          }),
        });
        return {
          passportId: runtime.passportId,
          status: "active",
          checkpoint,
          receipt: {
            id: `ISSUE-${checkpoint.id}`,
            type: "Passport itinerary sealed",
            detail: `${destinations.length} destination manifests were sealed with a ${expiry.toLowerCase()} review window at checkpoint ${checkpoint.id}.`,
            time: formatDateLabel(checkpoint.created_at),
            status: "Succeeded",
            reversible: true,
            checkpoint: `v${checkpoint.passport_version}`,
            _checkpointId: checkpoint.id,
          },
        };
      },
      () => ({
        passportId: runtime.fixturePassportId,
        status: "active",
        receipt: fixtureReceipt("Passport itinerary sealed", `${destinations.length} destination manifests were selected with a ${expiry.toLowerCase()} review window.`, { checkpoint: "v3" }),
      }),
    );
  },

  previewMigration(payload) {
    return withMutationFallback(
      async () => migrationPreviewForUi(await previewMigrationOnService(payload)),
      () => ({
        ...clone(MIGRATION_PREVIEW),
        actions: MIGRATION_PREVIEW.actions.map((item) => ({ ...item, mode: "Fixture" })),
        previewId: nextFixtureIdentity("PRV").id,
      }),
    );
  },

  applyMigration(payload) {
    return withMutationFallback(
      async () => {
        const migration = runtime.migrations.get(payload.previewId);
        if (!migration) throw new CuratorApiError(409, "Migration preview is missing or stale", payload);
        const actionCount = migration.plan?.actions?.length || 0;
        const executed = await approveAndExecuteMigration(migration);
        if (!executed) {
          return {
            applied: 0,
            guided: 0,
            skipped: 0,
            receipt: {
              id: `ALIGNED-${migration.id}`,
              type: "Migration already aligned",
              detail: `${payload.destinationName} required no destination changes after the exact preview.`,
              time: formatDateLabel(migration.created_at),
              status: "Succeeded",
              reversible: false,
              checkpoint: `v${migration.passport_version}`,
            },
          };
        }
        const guided = (executed.decisions || []).filter((item) => item.requires_handoff).length;
        const skipped = (executed.decisions || []).filter((item) => !item.allowed).length;
        const applied = Math.max(0, actionCount - guided - skipped);
        const resultType = applied > 0
          ? "Migration applied"
          : guided > 0
            ? "Guided handoff prepared"
            : "Migration completed without destination changes";
        const resultDetail = applied > 0
          ? `${payload.sourceName} to ${payload.destinationName}; ${applied} approved Lab actions executed, ${guided} guided handoffs prepared, and ${skipped} actions skipped.`
          : `${payload.sourceName} to ${payload.destinationName}; no destination controls were executed. ${guided} guided handoff steps were prepared and ${skipped} actions were skipped.`;
        return {
          applied,
          guided,
          skipped,
          receipt: {
            id: executed.receipt_id,
            type: resultType,
            detail: resultDetail,
            time: formatDateLabel(executed.completed_at),
            status: "Succeeded",
            reversible: applied > 0 && Boolean((migration.plan?.actions || []).some((item) => item.reversible)),
            checkpoint: `v${migration.passport_version}`,
            _platform: migration.platform,
          },
        };
      },
      () => {
        const simulated = MIGRATION_PREVIEW.actions.reduce((total, item) => total + item.count, 0);
        return {
          applied: 0,
          guided: 0,
          skipped: 0,
          simulated,
          receipt: fixtureReceipt(
            "Fixture migration simulated",
            `${payload.sourceName} to ${payload.destinationName}; ${simulated} planned actions were simulated locally and no external account was changed.`,
            { reversible: false, status: "Simulated" },
          ),
        };
      },
    );
  },

  issueTemporaryVisa(payload) {
    return withMutationFallback(
      async () => {
        const startsAt = new Date().toISOString();
        const expiresAt = futureIsoForPayload(payload);
        const mode = overlayMode(payload.mode);
        const overlay = await requestJson("/api/visas", {
          method: "POST",
          body: JSON.stringify({
            actor_id: runtime.actorId,
            passport_id: runtime.passportId,
            name: payload.name,
            topic_adjustments: topicAdjustmentsForPurpose(payload.purpose),
            add_exclusions: [],
            remove_exclusions: [],
            starts_at: startsAt,
            expires_at: expiresAt,
            mode,
            serendipity: runtime.passport.serendipity,
            max_outrage: runtime.passport.max_outrage,
          }),
        });
        runtime.visas.set(overlay.id, overlay);
        let activation = null;
        if (mode === "reversible_live") {
          try {
            const migration = await requestJson("/api/migrations/preview", {
              method: "POST",
              body: JSON.stringify({
                actor_id: runtime.actorId,
                passport_id: runtime.passportId,
                platform: "feed_passport_lab",
                destination_account_id: "destination-new",
                overlay_id: overlay.id,
              }),
            });
            runtime.migrations.set(migration.id, migration);
            activation = await approveAndExecuteMigration(migration);
          } catch (error) {
            if (!isConnectivityFailure(error)) {
              try {
                const revoked = await requestJson(`/api/visas/${encodeURIComponent(overlay.id)}/revoke`, {
                  method: "POST",
                  body: JSON.stringify({ actor_id: runtime.actorId }),
                });
                runtime.visas.set(overlay.id, revoked);
              } catch {
                // Preserve the activation error; state hydration exposes any
                // partial overlay that could not be compensated immediately.
              }
            }
            throw error;
          }
        }
        return {
          visa: {
            id: overlay.id,
            name: overlay.name,
            purpose: payload.purpose,
            duration: payload.duration,
            mode: payload.mode,
            status: String(overlay.status || "active").replace(/^./, (item) => item.toUpperCase()),
            issuedAt: formatDateLabel(overlay.starts_at),
            expiresAt: formatDateLabel(overlay.expires_at),
          },
          receipt: {
            id: activation?.receipt_id || `VISA-${overlay.id}`,
            type: activation ? "Temporary visa activated" : "Temporary visa issued",
            detail: activation
              ? `${overlay.name} applied its approved Lab controls through ${formatDateLabel(overlay.expires_at)}; expiry or revocation rolls back receipt ${activation.receipt_id}.`
              : `${overlay.name} is isolated through ${formatDateLabel(overlay.expires_at)} and leaves the base Passport unchanged.`,
            time: formatDateLabel(activation?.completed_at || overlay.starts_at),
            status: "Succeeded",
            reversible: true,
            checkpoint: `v${runtime.passport.version}`,
            _visaId: overlay.id,
            _platform: "feed_passport_lab",
          },
        };
      },
      () => {
        const identity = nextFixtureIdentity("VISA");
        const visa = { id: identity.id, name: payload.name, purpose: payload.purpose, duration: payload.duration, mode: payload.mode, status: "Active", issuedAt: identity.label, expiresAt: payload.expiresAt };
        runtime.visas.set(visa.id, visa);
        return {
          visa: clone(visa),
          receipt: fixtureReceipt("Temporary visa issued", `${payload.name} is active for ${payload.duration.toLowerCase()} in ${payload.mode.toLowerCase()} mode.`, { _visaId: visa.id }),
        };
      },
    );
  },

  revokeTemporaryVisa(visaId) {
    return withMutationFallback(
      async () => {
        const revoked = await requestJson(`/api/visas/${encodeURIComponent(visaId)}/revoke`, {
          method: "POST",
          body: JSON.stringify({ actor_id: runtime.actorId }),
        });
        runtime.visas.set(visaId, revoked);
        return {
          visa: revoked,
          receipt: {
            id: `REVOKE-${visaId}`,
            type: "Temporary visa revoked",
            detail: `${visaId} was revoked through the Curator API and its active Lab overlay was closed.`,
            time: formatDateLabel(revoked.revoked_at || new Date()),
            status: "Succeeded",
            reversible: false,
            checkpoint: `v${runtime.passport.version}`,
          },
        };
      },
      () => {
        const current = runtime.visas.get(visaId);
        if (current) runtime.visas.set(visaId, { ...current, status: "Revoked", expiresAt: "REVOKED NOW" });
        return {
          receipt: fixtureReceipt("Temporary visa revoked", `${visaId} was revoked and its overlay closed.`, { reversible: false }),
        };
      },
    );
  },

  createCompanionInvitation(payload) {
    return withMutationFallback(
      async () => {
        if (!Object.values(payload.share || {}).some(Boolean)) {
          throw new CuratorApiError(422, "A companion requires at least one explicitly shared Passport field", payload);
        }
        if (!String(payload.partnerCode || "").trim()) {
          throw new CuratorApiError(422, "A companion invitation requires a visible local demo code", payload);
        }
        const expiresAt = futureIsoForPayload(payload, 60_000);
        const partnerOwnerId = deterministicPartnerId(payload.partnerCode);
        const pairId = deterministicPairId(runtime.actorId, partnerOwnerId, payload.partnerCode);
        const ownSlice = await requestJson("/api/shares", {
          method: "POST",
          body: JSON.stringify(continuousShareBody({
            actorId: runtime.actorId,
            passport: runtime.passport,
            share: payload.share,
            expiresAt,
            pairId,
            counterpartyOwnerId: partnerOwnerId,
          })),
        });
        const invitation = invitationProjection({
          slice: ownSlice,
          payload,
          ownerPrincipalId: runtime.actorId,
        });
        runtime.companionInvitations.set(invitation.id, {
          invitation,
          ownerSlice: ownSlice,
          payload: clone(payload),
        });
        return {
          invitation: clone(invitation),
          activationPerformed: false,
          receipt: {
            id: `CONSENT-${ownSlice.id}`,
            type: "First companion consent recorded",
            detail: `${runtime.actorId} created one continuous consent slice for Passport ${runtime.passportId}; no blend was activated.`,
            time: formatDateLabel(ownSlice.created_at || new Date()),
            status: "Awaiting second consent",
            reversible: true,
            checkpoint: `v${runtime.passport.version}`,
            _sliceId: ownSlice.id,
            _companionInvitationId: invitation.id,
          },
        };
      },
      () => {
        if (!Object.values(payload.share || {}).some(Boolean)) {
          throw new CuratorApiError(422, "A companion requires at least one explicitly shared Passport field", payload);
        }
        if (!String(payload.partnerCode || "").trim()) {
          throw new CuratorApiError(422, "A companion invitation requires a visible local demo code", payload);
        }
        const ownerPassport = fixtureOwnerPassport();
        const expiresAt = futureIsoForPayload(payload, 60_000);
        const partnerOwnerId = deterministicPartnerId(payload.partnerCode);
        const pairId = deterministicPairId(
          runtime.fixtureOwnerId,
          partnerOwnerId,
          payload.partnerCode,
        );
        const ownSlice = fixtureContinuousSlice({
          actorId: runtime.fixtureOwnerId,
          passport: ownerPassport,
          share: payload.share,
          expiresAt,
          pairId,
          counterpartyOwnerId: partnerOwnerId,
        });
        const invitation = invitationProjection({
          slice: ownSlice,
          payload,
          ownerPrincipalId: runtime.fixtureOwnerId,
        });
        runtime.companionInvitations.set(invitation.id, {
          invitation,
          ownerSlice: ownSlice,
          payload: clone(payload),
        });
        return {
          invitation: clone(invitation),
          activationPerformed: false,
          receipt: fixtureReceipt(
            "First companion consent recorded",
            `${runtime.fixtureOwnerId} created one continuous fixture consent slice; no blend was activated.`,
            {
              status: "Awaiting second consent",
              _sliceId: ownSlice.id,
              _companionInvitationId: invitation.id,
            },
          ),
        };
      },
    );
  },

  acceptCompanionInvitation(payload) {
    return withMutationFallback(
      async () => {
        const invitationId = payload.invitationId || payload.invitation?.id;
        const record = runtime.companionInvitations.get(invitationId);
        if (!record || record.invitation.status !== "Awaiting second consent") {
          throw new CuratorApiError(409, "The first local test principal's consent is missing, revoked, or already used", payload);
        }
        if (!Object.values(payload.share || {}).some(Boolean)) {
          throw new CuratorApiError(422, "The second local test principal must explicitly select at least one Passport field", payload);
        }
        const pairBinding = companionPairBinding(record, runtime.actorId);
        const partner = await partnerPassport(
          { ...record.payload, share: payload.share },
          pairBinding.counterpartyOwnerId,
        );
        let partnerSlice = null;
        try {
          partnerSlice = await requestJson("/api/shares", {
            method: "POST",
            body: JSON.stringify(continuousShareBody({
              actorId: partner.owner_id,
              passport: partner,
              share: payload.share,
              expiresAt: record.invitation.expiresAt,
              pairId: pairBinding.pairId,
              counterpartyOwnerId: runtime.actorId,
            })),
          });
          const partnerPairBinding = companionPairBinding({ ownerSlice: partnerSlice }, partner.owner_id);
          if (
            partnerPairBinding.pairId !== pairBinding.pairId
            || partnerPairBinding.counterpartyOwnerId !== runtime.actorId
          ) {
            throw new CuratorApiError(
              409,
              "The second companion consent does not match the pending pair binding",
              partnerSlice,
            );
          }
          const blend = await requestJson("/api/companions", {
            method: "POST",
            body: JSON.stringify({
              actor_id: runtime.actorId,
              name: `${record.payload.mode} · ${record.payload.partnerCode}`,
              slice_ids: [record.ownerSlice.id, partnerSlice.id],
              weights: {
                [runtime.actorId]: Math.max(1, 100 - Number(record.payload.weight)),
                [partner.owner_id]: Math.max(1, Number(record.payload.weight)),
              },
              strategy: companionStrategy(record.payload.mode),
              expires_at: record.invitation.expiresAt,
              scope: "continuous",
            }),
          });
          const blendForProjection = {
            ...blend,
            participant_selected_fields: blend.participant_selected_fields || {
              [runtime.passportId]: record.ownerSlice.selected_fields,
              [partner.id]: partnerSlice.selected_fields,
            },
            source_passport_ids: blend.source_passport_ids || [runtime.passportId, partner.id],
          };
          runtime.companions.set(blend.id, {
            blend: blendForProjection,
            ownSliceId: record.ownerSlice.id,
            partnerSliceId: partnerSlice.id,
          });
          const companion = companionProjectionForUi(blendForProjection, record.payload.partnerCode);
          const activatedInvitation = { ...record.invitation, status: "Activated", activationPerformed: true };
          runtime.companionInvitations.set(invitationId, { ...record, invitation: activatedInvitation });
          return {
            invitation: clone(activatedInvitation),
            companion,
            receipt: {
              id: `COMPANION-${blend.id}`,
              type: "Companion blend activated",
              detail: `${record.payload.mode} used two independently approved continuous slices with ${record.payload.weight}% requested partner influence and expiry ${formatDateLabel(blend.expires_at)}.`,
              time: formatDateLabel(blend.created_at),
              status: "Succeeded",
              reversible: true,
              checkpoint: `v${runtime.passport.version}`,
              _sliceId: record.ownerSlice.id,
              _partnerSliceId: partnerSlice.id,
              _companionId: blend.id,
            },
          };
        } catch (error) {
          if (!isConnectivityFailure(error) && partnerSlice?.id) {
            try {
              await requestJson(`/api/shares/${encodeURIComponent(partnerSlice.id)}/revoke`, {
                method: "POST",
                body: JSON.stringify({ actor_id: partner.owner_id }),
              });
            } catch {
              // The first person's pending consent stays visible. A later state
              // refresh exposes any second-person slice that could not be compensated.
            }
          }
          throw error;
        }
      },
      () => {
        const invitationId = payload.invitationId || payload.invitation?.id;
        const record = runtime.companionInvitations.get(invitationId);
        if (!record || record.invitation.status !== "Awaiting second consent") {
          throw new CuratorApiError(409, "The first local test principal's consent is missing, revoked, or already used", payload);
        }
        if (!Object.values(payload.share || {}).some(Boolean)) {
          throw new CuratorApiError(422, "The second local test principal must explicitly select at least one Passport field", payload);
        }
        const pairBinding = companionPairBinding(record, runtime.fixtureOwnerId);
        const partner = fixturePartnerPassport(
          { ...record.payload, share: payload.share },
          pairBinding.counterpartyOwnerId,
        );
        const partnerSlice = fixtureContinuousSlice({
          actorId: partner.owner_id,
          passport: partner,
          share: payload.share,
          expiresAt: record.invitation.expiresAt,
          pairId: pairBinding.pairId,
          counterpartyOwnerId: runtime.fixtureOwnerId,
        });
        const partnerPairBinding = companionPairBinding({ ownerSlice: partnerSlice }, partner.owner_id);
        if (
          partnerPairBinding.pairId !== pairBinding.pairId
          || partnerPairBinding.counterpartyOwnerId !== runtime.fixtureOwnerId
        ) {
          throw new CuratorApiError(
            409,
            "The second companion consent does not match the pending pair binding",
            partnerSlice,
          );
        }
        const identity = nextFixtureIdentity("PAIR");
        const rawBlend = {
          id: identity.id,
          name: `${record.payload.mode} · ${record.payload.partnerCode}`,
          status: "active",
          participant_ids: [runtime.fixtureOwnerId, partner.owner_id],
          slice_ids: [record.ownerSlice.id, partnerSlice.id],
          consent_ids: [record.ownerSlice.consent_id, partnerSlice.consent_id],
          requested_weights: {
            [runtime.fixtureOwnerId]: Math.max(1, 100 - Number(record.payload.weight)),
            [partner.owner_id]: Math.max(1, Number(record.payload.weight)),
          },
          strategy: companionStrategy(record.payload.mode),
          scope: "continuous",
          refresh_on_revision: true,
          pair_id: pairBinding.pairId,
          sync_revision: 1,
          source_passport_ids: [runtime.fixturePassportId, partner.id],
          target_passport_ids: [runtime.fixturePassportId, partner.id],
          participant_selected_fields: {
            [runtime.fixturePassportId]: record.ownerSlice.selected_fields,
            [partner.id]: partnerSlice.selected_fields,
          },
          selected_fields: {
            topic_names: [...new Set([
              ...record.ownerSlice.selected_fields.topic_names,
              ...partnerSlice.selected_fields.topic_names,
            ])].sort(),
            creator_ids: [...new Set([
              ...record.ownerSlice.selected_fields.creator_ids,
              ...partnerSlice.selected_fields.creator_ids,
            ])].sort(),
            include_serendipity: Boolean(record.ownerSlice.selected_fields.include_serendipity || partnerSlice.selected_fields.include_serendipity),
            include_formats: Boolean(record.ownerSlice.selected_fields.include_formats || partnerSlice.selected_fields.include_formats),
            include_exclusions: Boolean(record.ownerSlice.selected_fields.include_exclusions || partnerSlice.selected_fields.include_exclusions),
          },
          created_at: identity.iso,
          last_synced_at: identity.iso,
          expires_at: record.invitation.expiresAt,
        };
        const companion = companionProjectionForUi(rawBlend, record.payload.partnerCode, {
          ownerPrincipalId: runtime.fixtureOwnerId,
          ownerPassportId: runtime.fixturePassportId,
        });
        runtime.companions.set(companion.id, {
          ...companion,
          ownSliceId: record.ownerSlice.id,
          partnerSliceId: partnerSlice.id,
        });
        const activatedInvitation = { ...record.invitation, status: "Activated", activationPerformed: true };
        runtime.companionInvitations.set(invitationId, { ...record, invitation: activatedInvitation });
        return {
          invitation: clone(activatedInvitation),
          companion: clone(companion),
          receipt: fixtureReceipt(
            "Companion blend activated",
            `${record.payload.mode} activated only after two distinct local test principals recorded continuous consent; no raw history or social account was used.`,
            {
              _sliceId: record.ownerSlice.id,
              _partnerSliceId: partnerSlice.id,
              _companionId: companion.id,
            },
          ),
        };
      },
    );
  },

  revokeCompanionInvitation(invitation) {
    return withMutationFallback(
      async () => {
        const record = runtime.companionInvitations.get(invitation.id);
        if (!record || record.invitation.status !== "Awaiting second consent") {
          throw new CuratorApiError(409, "This pending companion consent is unavailable", invitation);
        }
        const revoked = await requestJson(`/api/shares/${encodeURIComponent(record.ownerSlice.id)}/revoke`, {
          method: "POST",
          body: JSON.stringify({ actor_id: runtime.actorId }),
        });
        runtime.companionInvitations.delete(invitation.id);
        return {
          invitation: { ...record.invitation, status: "Revoked" },
          receipt: {
            id: `REVOKE-${record.ownerSlice.id}`,
            type: "Pending companion consent revoked",
            detail: `${record.ownerSlice.id} was revoked before a second person consented; no blend existed.`,
            time: formatDateLabel(revoked.revoked_at || new Date()),
            status: "Succeeded",
            reversible: false,
            checkpoint: `v${runtime.passport.version}`,
          },
        };
      },
      () => {
        const record = runtime.companionInvitations.get(invitation.id);
        if (!record || record.invitation.status !== "Awaiting second consent") {
          throw new CuratorApiError(409, "This pending companion consent is unavailable", invitation);
        }
        const current = runtime.fixtureShares.get(record.ownerSlice.id);
        if (current) runtime.fixtureShares.set(current.id, { ...current, status: "revoked", revoked_at: new Date().toISOString() });
        runtime.companionInvitations.delete(invitation.id);
        return {
          invitation: { ...record.invitation, status: "Revoked" },
          receipt: fixtureReceipt(
            "Pending companion consent revoked",
            `${record.ownerSlice.id} was revoked before a second person consented; no fixture blend existed.`,
            { reversible: false },
          ),
        };
      },
    );
  },

  revokeCompanion(companion) {
    return withMutationFallback(
      async () => {
        const record = runtime.companions.get(companion.id);
        if (!record?.ownSliceId) throw new CuratorApiError(404, "Companion consent slice is unavailable", companion);
        const revoked = await requestJson(`/api/shares/${encodeURIComponent(record.ownSliceId)}/revoke`, {
          method: "POST",
          body: JSON.stringify({ actor_id: runtime.actorId }),
        });
        runtime.companions.set(companion.id, { ...record, blend: { ...record.blend, status: "consent_invalidated" } });
        return {
          companion: { ...companion, status: "Revoked" },
          receipt: {
            id: `REVOKE-${companion.id}`,
            type: "Companion revoked",
            detail: `${companion.id} was invalidated by revoking its explicit owner slice ${revoked.id}.`,
            time: formatDateLabel(revoked.revoked_at || new Date()),
            status: "Succeeded",
            reversible: false,
            checkpoint: `v${runtime.passport.version}`,
          },
        };
      },
      () => {
        const current = runtime.companions.get(companion.id) || companion;
        runtime.companions.set(companion.id, { ...current, status: "Revoked" });
        if (current.ownSliceId) {
          const slice = runtime.fixtureShares.get(current.ownSliceId);
          if (slice) runtime.fixtureShares.set(slice.id, { ...slice, status: "revoked", revoked_at: new Date().toISOString() });
        }
        return {
          receipt: fixtureReceipt("Companion revoked", `${companion.code || companion.id} was revoked; both base constitutions remain unchanged.`, { reversible: false }),
        };
      },
    );
  },

  checkDrift() {
    return withMutationFallback(
      async () => {
        const raw = await requestJson("/api/drift", {
          method: "POST",
          body: JSON.stringify({ actor_id: runtime.actorId, passport_id: runtime.passportId, platform: "feed_passport_lab", account_id: "destination-new" }),
        });
        runtime.lastDrift = raw;
        return driftForUi(raw);
      },
      () => {
        runtime.lastDrift = {
          ...clone(DRIFT_FIXTURE),
          score: 87,
          status: "decision_required",
          correctable: true,
          checkedAt: "29 AUG 2026 · 10:21",
          recommendation: "Reduce repeated creators and add three independent sources before changing topic weights.",
        };
        return clone(runtime.lastDrift);
      },
    );
  },

  applyDriftCorrection() {
    return withMutationFallback(
      async () => {
        if (runtime.lastDrift?.status !== "decision_required" || !runtime.lastDrift?.proposed_plan?.actions?.length) {
          return {
            applied: false,
            aligned: true,
            receipt: {
              id: `ALIGNED-${runtime.lastDrift?.id || nextFixtureIdentity("DRIFT").id}`,
              type: "Drift check aligned",
              detail: "The Lab observation was already aligned, so no corrective controls were executed.",
              time: formatDateLabel(runtime.lastDrift?.observed_at || new Date()),
              status: "Succeeded",
              reversible: false,
              checkpoint: `v${runtime.passport.version}`,
            },
          };
        }
        const migration = await previewMigrationOnService({ destination: "lab" });
        const executed = await approveAndExecuteMigration(migration);
        return {
          applied: Boolean(executed),
          aligned: !executed,
          receipt: {
            id: executed?.receipt_id || `ALIGNED-${migration.id}`,
            type: executed ? "Drift correction applied" : "Drift check aligned",
            detail: executed
              ? `Applied the exact ${migration.plan.actions.length}-action Lab correction under one-time consent.`
              : "The fresh Lab preview required no corrective controls.",
            time: formatDateLabel(executed?.completed_at || migration.created_at),
            status: "Succeeded",
            reversible: Boolean(executed),
            checkpoint: `v${migration.passport_version}`,
            _platform: "feed_passport_lab",
          },
        };
      },
      () => ({
        applied: false,
        simulated: true,
        aligned: false,
        receipt: fixtureReceipt(
          "Fixture drift correction simulated",
          "The fixture simulated three independent source candidates and lower repeated-creator weight without changing an external account.",
          { status: "Simulated", reversible: false },
        ),
      }),
    );
  },

  preserveCreator(creator) {
    return withMutationFallback(
      async () => {
        const mapping = CREATOR_DIRECTORY_MAP[creator.id] || {
          source: creator.id,
          destination: normalizePlatform(creator.destination),
        };
        const match = await requestJson(`/api/creators/${encodeURIComponent(mapping.source)}/continuity/${encodeURIComponent(mapping.destination)}`, { method: "GET" });
        if (match.status !== "verified") throw new CuratorApiError(409, "Creator identity could not be verified", match);
        const preserved = await requestJson("/api/creator-continuity", {
          method: "POST",
          body: JSON.stringify({ actor_id: runtime.actorId, passport_id: runtime.passportId, creator_id: mapping.source, destination_platform: mapping.destination }),
        });
        return {
          creator: { ...creator, destinationHandle: preserved.destination_identity, confidence: Math.round(Number(preserved.confidence) * 100), preserved: true },
          receipt: {
            id: preserved.id,
            type: "Creator continuity",
            detail: `${preserved.display_name} was preserved as ${preserved.destination_identity} with ${Math.round(Number(preserved.confidence) * 100)}% deterministic directory-match confidence.`,
            time: formatDateLabel(preserved.preserved_at),
            status: "Succeeded",
            reversible: false,
            checkpoint: `v${preserved.passport_version}`,
          },
        };
      },
      () => {
        const link = {
          id: nextFixtureIdentity("CREATOR").id,
          passport_id: runtime.fixturePassportId,
          destination_platform: normalizePlatform(creator.destination),
          destination_identity: creator.destinationHandle,
          creator_id: creator.id,
          status: "reviewed_fixture",
        };
        runtime.creatorLinks.set(creator.id, link);
        return {
          creator: { ...creator, preserved: true },
          receipt: fixtureReceipt(
            "Creator continuity",
            `${creator.name} was preserved as ${creator.destinationHandle} with ${creator.confidence}% deterministic directory-match confidence.`,
            { _creatorId: creator.id },
          ),
        };
      },
    );
  },

  rollback(receipt) {
    return withMutationFallback(
      async () => {
        if (receipt._checkpointId) {
          const restored = await requestJson(`/api/checkpoints/${encodeURIComponent(receipt._checkpointId)}/restore`, {
            method: "POST",
            body: JSON.stringify({ actor_id: runtime.actorId }),
          });
          runtime.passport = restored;
          return {
            receipt: {
              id: `RESTORE-${receipt._checkpointId}`,
              type: "Checkpoint restored",
              detail: `${receipt._checkpointId} was restored as Passport version ${restored.version}.`,
              time: formatDateLabel(restored.updated_at),
              status: "Succeeded",
              reversible: false,
              checkpoint: `v${restored.version}`,
            },
            rollbackComplete: true,
          };
        }
        if (receipt._visaId) {
          const revokedVisa = (await feedPassportApi.revokeTemporaryVisa(receipt._visaId)).data;
          return { ...revokedVisa, rollbackComplete: true };
        }
        if (receipt._sliceId) {
          const revoked = await requestJson(`/api/shares/${encodeURIComponent(receipt._sliceId)}/revoke`, {
            method: "POST",
            body: JSON.stringify({ actor_id: runtime.actorId }),
          });
          return { receipt: { id: `REVOKE-${receipt._sliceId}`, type: "Companion revoked", detail: `${revoked.id} was revoked and the companion consent became invalid.`, time: formatDateLabel(revoked.revoked_at), status: "Succeeded", reversible: false, checkpoint: receipt.checkpoint }, rollbackComplete: true };
        }
        const platform = normalizePlatform(receipt._platform || "lab");
        const approval = await requestJson(`/api/receipts/${encodeURIComponent(receipt.id)}/rollback-approval`, {
          method: "POST",
          body: JSON.stringify({ actor_id: runtime.actorId, platform, ttl_seconds: 600 }),
        });
        const rolledBack = await requestJson(`/api/receipts/${encodeURIComponent(receipt.id)}/rollback`, {
          method: "POST",
          body: JSON.stringify({ actor_id: runtime.actorId, platform, approval_token: approval.token || approval.approval_token }),
        });
        const rollbackComplete = rolledBack.status === "rolled_back";
        const projectedReceipt = serverReceipt(rolledBack, runtime.demo);
        return {
          receipt: rollbackComplete
            ? { ...projectedReceipt, _platform: platform }
            : {
              ...projectedReceipt,
              type: "Migration rollback incomplete",
              status: "Needs attention",
              reversible: Boolean(projectedReceipt.reversible || receipt.reversible),
              _platform: platform,
            },
          rollbackComplete,
        };
      },
      () => {
        if (receipt._previousConstitution) {
          runtime.fixtureConstitution = clone(receipt._previousConstitution);
          runtime.migrations.clear();
          runtime.lastDrift = null;
        }
        if (receipt._visaId) {
          const visa = runtime.visas.get(receipt._visaId);
          if (visa) runtime.visas.set(receipt._visaId, { ...visa, status: "Revoked", expiresAt: "REVOKED NOW" });
        }
        if (receipt._creatorId) runtime.creatorLinks.delete(receipt._creatorId);
        if (receipt._companionInvitationId && !receipt._companionId) {
          const record = runtime.companionInvitations.get(receipt._companionInvitationId);
          if (record) {
            const slice = runtime.fixtureShares.get(record.ownerSlice.id);
            if (slice) runtime.fixtureShares.set(slice.id, { ...slice, status: "revoked", revoked_at: new Date().toISOString() });
            runtime.companionInvitations.delete(receipt._companionInvitationId);
          }
        }
        if (receipt._companionId) {
          const companion = runtime.companions.get(receipt._companionId);
          if (companion) runtime.companions.set(receipt._companionId, { ...companion, status: "Revoked" });
        }
        return {
          receipt: fixtureReceipt("Rollback completed", `${receipt.id} was reversed to checkpoint ${receipt.checkpoint}.`, { reversible: false, checkpoint: receipt.checkpoint }),
          rollbackComplete: true,
        };
      },
    );
  },

  previewAgentMission(spec) {
    const body = agentMissionRequestBody(spec);
    return withMutationFallback(
      async () => requestJson("/api/agent/missions/preview", {
        method: "POST",
        body: JSON.stringify(body),
      }),
      () => fixtureMissionPreview(body),
    );
  },

  async getAgentModelStatus() {
    if (!serviceAvailable) {
      return {
        source: "fixture",
        data: {
          configured: false,
          online: false,
          readiness: "service_required",
          provider: "disabled",
          model_id: null,
          endpoint_scope: "none",
          mode: "local_only",
          external_model_calls: false,
          paid_model_calls: false,
          reason: "The local Python service is required for genuine model inference.",
        },
      };
    }
    await ensureServiceContext();
    return {
      source: "service",
      data: await requestJson("/api/agent/model/status", { method: "GET", timeoutMs: 5000 }),
    };
  },

  async planFeatureIntent(request) {
    if (!serviceAvailable) {
      throw new CuratorApiError(
        503,
        "A genuine Feature Clerk proposal requires the local Python service and loopback model; fixture mode will not impersonate AI.",
        { fallback_permitted: false },
      );
    }
    const safeRequest = String(request || "").trim();
    if (!safeRequest || safeRequest.length > 1200) {
      throw new CuratorApiError(
        422,
        "A Feature Clerk request must contain between one and 1200 text characters.",
        null,
      );
    }
    await ensureServiceContext();
    const platforms = await requestJson("/api/platforms", { method: "GET", timeoutMs: 5000 });
    const proposal = await requestJson("/api/agent/features/plan", {
      method: "POST",
      body: JSON.stringify({
        actor_id: runtime.actorId,
        passport_id: runtime.passportId,
        request: safeRequest,
      }),
      timeoutMs: 150_000,
    });
    return { source: "service", data: proposal, platforms };
  },

  async previewAgentMissionWithModel(spec) {
    if (!serviceAvailable) {
      throw new CuratorApiError(
        503,
        "A genuine local-model preview requires the local Python service; fixture mode will not impersonate AI.",
        { fallback_permitted: false },
      );
    }
    await ensureServiceContext();
    const mission = await requestJson("/api/agent/missions/plan", {
      method: "POST",
      body: JSON.stringify(agentMissionRequestBody(spec)),
      timeoutMs: 150_000,
    });
    runtime.agentMissions.set(mission.id, mission);
    return { source: "service", data: mission };
  },

  getAgentMission(missionId) {
    return withFixtureFallback(
      () => requestJson(`/api/agent/missions/${encodeURIComponent(missionId)}?actor_id=${encodeURIComponent(runtime.actorId)}`, { method: "GET" }),
      () => {
        const mission = runtime.agentMissions.get(missionId);
        if (!mission) throw new CuratorApiError(404, "Local mission was not found", null);
        return clone(mission);
      },
    );
  },

  runAgentMission(missionId) {
    return withMutationFallback(
      async () => {
        const approval = await requestJson(`/api/agent/missions/${encodeURIComponent(missionId)}/approval`, {
          method: "POST",
          body: JSON.stringify({ actor_id: runtime.actorId, ttl_seconds: 600 }),
        });
        return requestJson(`/api/agent/missions/${encodeURIComponent(missionId)}/execute`, {
          method: "POST",
          body: JSON.stringify({
            actor_id: runtime.actorId,
            approval_token: approval.approval_token || approval.token,
          }),
        });
      },
      () => fixtureMissionExecution(missionId),
    );
  },

  cancelAgentMission(missionId) {
    return withMutationFallback(
      () => requestJson(`/api/agent/missions/${encodeURIComponent(missionId)}/cancel`, {
        method: "POST",
        body: JSON.stringify({ actor_id: runtime.actorId }),
      }),
      () => {
        const mission = runtime.agentMissions.get(missionId);
        if (!mission) throw new CuratorApiError(404, "Local mission was not found", null);
        const cancelled = { ...mission, status: "cancelled", stop_reason: "cancelled" };
        runtime.agentMissions.set(missionId, cancelled);
        return clone(cancelled);
      },
    );
  },

  rollbackAgentMission(missionId) {
    return withMutationFallback(
      async () => {
        const approval = await requestJson(`/api/agent/missions/${encodeURIComponent(missionId)}/rollback/approval`, {
          method: "POST",
          body: JSON.stringify({ actor_id: runtime.actorId, ttl_seconds: 600 }),
        });
        return requestJson(`/api/agent/missions/${encodeURIComponent(missionId)}/rollback`, {
          method: "POST",
          body: JSON.stringify({
            actor_id: runtime.actorId,
            approval_token: approval.approval_token || approval.token,
          }),
        });
      },
      () => {
        const mission = runtime.agentMissions.get(missionId);
        if (!mission) throw new CuratorApiError(404, "Local mission was not found", null);
        if (!mission.rollback_available) throw new CuratorApiError(409, "This mission has no reversible local actions", mission);
        const rolledBack = {
          ...mission,
          status: "rolled_back",
          stop_reason: "rolled_back",
          after: clone(mission.before),
          rollback_available: false,
          rollback: {
            status: "completed",
            failure_count: 0,
            receipt_order: [...mission.receipt_ids].reverse(),
            verification: {
              status: "completed",
              method: "deterministic_fixture_snapshot",
              state_restored: true,
              evidence: "deterministic_fixture_control_state_matches_pre_run_state",
              evaluation_status: "completed",
              evaluation: clone(mission.before),
            },
          },
          trace: mission.trace.map((step) => step.stage === "receipt"
            ? { ...step, status: "rolled_back", detail: `Simulated reverse-order restoration for ${mission.receipt_ids.length} fixture receipts; the local snapshot matched.` }
            : step),
        };
        runtime.agentMissions.set(missionId, rolledBack);
        return clone(rolledBack);
      },
    );
  },

  runAgentCommand({ command }) {
    return withMutationFallback(
      async () => {
        const commandName = classifyAgentCommand(command);
        const reply = await requestJson("/api/agent/command", {
          method: "POST",
          body: JSON.stringify({ command: commandName, actor_id: runtime.actorId, arguments: agentArguments(commandName, command) }),
        });
        let captured = null;
        if (commandName === "capture_passport" && reply.data?.id) {
          activateServicePassport(reply.data);
          captured = {
            passport: reply.data,
            passportId: reply.data.id,
            constitution: serverConstitution(reply.data),
          };
        }
        if (commandName === "preview_migration" && reply.data?.id) runtime.migrations.set(reply.data.id, reply.data);
        if (commandName === "watch_drift" && reply.data?.id) runtime.lastDrift = reply.data;
        return {
          response: reply.message,
          ...captured,
          activity: {
            id: reply.trace_id,
            actor: "Passport agent",
            detail: `Classified as ${commandName}. ${reply.message}`,
            time: "NOW",
            state: reply.requires_confirmation ? "Plan only" : "Verified",
          },
        };
      },
      async () => {
        const response = fixtureAgentResponse(command);
        if (classifyAgentCommand(command) !== "capture_passport") return response;
        const argumentsValue = agentArguments("capture_passport", command);
        const captured = await feedPassportApi.capturePassport({
          source: argumentsValue.platform,
          name: argumentsValue.name,
          intent: argumentsValue.intent,
        });
        return { ...response, ...captured.data };
      },
    );
  },
};
