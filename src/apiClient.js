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
  guidedAttestationReceiptForUi,
  historyReceiptsForUi,
  instagramImportTransportFilename,
  invitationProjection,
  migrationPreviewForUi,
  normalizeLanguage,
  normalizePlatform,
  overlayMode,
  overlayProjectionForUi,
  projectCompanionForUi,
  projectDriftForUi,
  resumableGuidedMigrationForUi,
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
import { createAgentCoreGatewayClient } from "./api/agentCoreGateway.js";

export { classifyAgentCommand, missionRollbackIsVerified };

const FIXTURE_EPOCH = Date.UTC(2026, 7, 29, 10, 15, 0);
const CREATOR_DIRECTORY_MAP = {
  "studio-a": { source: "studio-a", destination: "youtube" },
  "paper-lab": { source: "paper-lab", destination: "youtube" },
  "city-zine": { source: "city.zine", destination: "bluesky" },
  "studio-a-x": { source: "studio-a", destination: "x" },
};
const env = import.meta.env || {};
export const LOCAL_MODEL_REQUEST_TIMEOUT_MS = 300_000;
const configuredApiBase =
  env.VITE_CURATOR_API_URL ||
  env.VITE_FEED_PASSPORT_API_BASE ||
  globalThis.__CURATOR_API_URL__ ||
  globalThis.__FEED_PASSPORT_API_BASE__ ||
  "";
const agentCoreEnvironment = {
  ...env,
  VITE_CURATE_AGENTCORE_GATEWAY_URL:
    env.VITE_CURATE_AGENTCORE_GATEWAY_URL
    || globalThis.__CURATE_AGENTCORE_GATEWAY_URL__
    || "",
};

let fixtureSequence = 0;
let serviceAvailable = Boolean(configuredApiBase);
let accessTokenProvider = async () => {
  const value = globalThis.__FEED_PASSPORT_ACCESS_TOKEN__;
  return typeof value === "string" ? value : "";
};
const agentCoreGatewayClient = createAgentCoreGatewayClient({
  environment: agentCoreEnvironment,
  getAccessToken: () => accessTokenProvider(),
});
const runtime = {
  demo: null,
  passport: null,
  passportId: null,
  actorId: null,
  connections: new Map(),
  migrations: new Map(),
  visas: new Map(),
  companions: new Map(),
  companionInvitations: new Map(),
  fixtureShares: new Map(),
  fixturePartnerPassports: new Map(),
  driftMonitors: new Map(),
  creatorLinks: new Map(),
  agentMissions: new Map(),
  liveCommissions: new Map(),
  feedEvidenceProposals: new Map(),
  feedEvidenceSnapshots: new Map(),
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
    const accessToken = String((await accessTokenProvider()) || "").trim();
    if (accessToken && /[\r\n]/.test(accessToken)) {
      throw new CuratorApiError(0, "The configured access token is invalid", null);
    }
    const response = await fetch(serviceUrl(path), {
      ...options,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
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

export function configureCuratorAuth(getAccessToken) {
  if (typeof getAccessToken !== "function") {
    throw new TypeError("configureCuratorAuth requires an asynchronous token provider");
  }
  accessTokenProvider = getAccessToken;
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
  runtime.liveCommissions.clear();
  runtime.feedEvidenceProposals.clear();
  runtime.feedEvidenceSnapshots.clear();
  runtime.lastDrift = null;
  runtime.fixtureCheckpoints.clear();
}

export function summarizeMigrationExecution(executed, { sourceName, destinationName }) {
  const summary = executed?.execution_summary;
  if (!summary || typeof summary !== "object" || Array.isArray(summary)) {
    throw new CuratorApiError(502, "Migration execution omitted its authoritative outcome summary", executed);
  }
  const applied = Number(summary.executed_action_count || 0);
  const remoteWrites = Number(summary.remote_write_count || 0);
  const guided = Number(summary.guided_action_count || 0);
  const skipped = Number(summary.skipped_action_count || 0) + Number(summary.denied_action_count || 0);
  const failed = Number(summary.failed_action_count || 0);
  const executionMode = String(summary.mode || "unknown");
  const migrationStatus = String(executed.status || "unknown");
  const needsAttention = failed > 0 || [
    "reconciliation_required",
    "failed_recoverable",
    "rollback_reconciliation_required",
    "needs_human",
  ].includes(migrationStatus);
  const resultType = needsAttention
    ? "Migration needs attention"
    : applied > 0
      ? "Migration applied"
      : guided > 0
        ? "Guided handoff prepared"
        : "Migration completed without destination changes";
  const resultDetail = needsAttention
    ? `${sourceName} to ${destinationName}; the run stopped as ${migrationStatus.replaceAll("_", " ")} with ${remoteWrites} confirmed external writes, ${Math.max(0, applied - remoteWrites)} local executions, ${skipped} skipped, and ${failed} failed. Inspect reconciliation state before retrying.`
    : remoteWrites > 0
      ? `${sourceName} to ${destinationName}; ${remoteWrites} authorized external account controls were written, ${skipped} actions were skipped, and ${failed} failed.`
      : applied > 0
        ? `${sourceName} to ${destinationName}; ${applied} approved local adapter actions executed, ${skipped} actions were skipped, and ${failed} failed.`
        : `${sourceName} to ${destinationName}; no destination controls were executed. ${guided} guided handoff steps were prepared and ${skipped} actions were skipped.`;
  return {
    applied,
    remoteWrites,
    guided,
    skipped,
    failed,
    executionMode,
    migrationStatus,
    needsAttention,
    resultType,
    resultDetail,
  };
}

function accountIdForPlatform(platform) {
  const normalized = normalizePlatform(platform);
  // The generic Migration desk never silently selects a connected account.
  // Authorized external writes belong to the separate exact-account commission
  // flow, where the account and sealed targets are visible during consent.
  return destinationAccount(normalized);
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
  const resumableGuidedMigration = resumableGuidedMigrationForUi(
    demo.migrations || [],
    { ownerId: passport.owner_id, passportId: passport.id },
  );
  return {
    passport: serverConstitution(passport),
    passportId: passport.id,
    ownerId: passport.owner_id,
    receipts: historyReceiptsForUi(demo, {
      ownerId: passport.owner_id,
      passportId: passport.id,
    }),
    resumableGuidedMigration,
    health: "ready",
  };
}

async function fetchDemoWithOnboarding() {
  let demo = await requestJson("/api/demo", { method: "GET" });
  if ((demo?.passports || []).length === 0) {
    await requestJson("/api/onboarding", {
      method: "POST",
      body: JSON.stringify({}),
    });
    demo = await requestJson("/api/demo", { method: "GET" });
  }
  return demo;
}

async function ensureServiceContext() {
  if (!serviceAvailable || runtime.passportId) return;
  ingestDemo(await fetchDemoWithOnboarding());
}

async function previewMigrationOnService({ destination }) {
  const platform = normalizePlatform(destination);
  const migration = await requestJson("/api/migrations/preview", {
    method: "POST",
    body: JSON.stringify({
      actor_id: runtime.actorId,
      passport_id: runtime.passportId,
      platform,
      destination_account_id: accountIdForPlatform(platform),
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
    hard_exclusions: runtime.fixtureConstitution.hardExclusions || [],
    serendipity: Number(runtime.fixtureConstitution.serendipity || 20) / 100,
    max_outrage: Number(runtime.fixtureConstitution.outrageCeiling || 5) / 100,
    max_source_share: Number(runtime.fixtureConstitution.creatorCeiling || 15) / 100,
  };
}

function agentCorePassportSnapshot() {
  const passport = runtime.passport || fixtureOwnerPassport();
  return {
    id: passport.id || runtime.fixturePassportId,
    owner_id: passport.owner_id || runtime.actorId || runtime.fixtureOwnerId,
    name: passport.name || runtime.fixtureConstitution.title || "My Curate Passport",
    version: Number(passport.version || runtime.fixtureConstitution.version || 1),
    intent: passport.intent || runtime.fixtureConstitution.intent,
    topic_targets: passport.topic_targets || uiTopicTargets(runtime.fixtureConstitution),
    creator_preferences: passport.creator_preferences || {},
    format_preferences: passport.format_preferences || {},
    languages: passport.languages || ["en"],
    hard_exclusions: passport.hard_exclusions || [],
    serendipity: passport.serendipity ?? Number(runtime.fixtureConstitution.serendipity || 20) / 100,
    max_outrage: passport.max_outrage ?? Number(runtime.fixtureConstitution.outrageCeiling || 5) / 100,
    max_source_share: passport.max_source_share ?? Number(runtime.fixtureConstitution.creatorCeiling || 15) / 100,
  };
}

const FIXTURE_EVIDENCE_LEXICON = {
  astronomy: ["astronomy", "nebula", "space", "telescope", "planet", "stars"],
  coding: ["coding", "programming", "software", "developer", "code"],
  drawing: ["drawing", "illustration", "sketch", "art"],
  anime: ["anime"],
  naruto: ["naruto"],
  one_piece: ["one piece"],
  perfumes: ["perfume", "perfumes", "fragrance"],
  science: ["science", "study", "research", "evidence", "experiment"],
  pet_science: ["pet science", "animal behavior", "animal behaviour", "veterinary", "zoology"],
  cute_drawing: ["cute drawing", "kawaii", "watercolor", "watercolour"],
  design: ["design", "typography", "architecture", "interface"],
  independent_games: ["indie game", "independent game", "game design", "devlog"],
  local_culture: ["local culture", "neighborhood", "neighbourhood", "community"],
};
const FIXTURE_RAGEBAIT_TERMS = [
  "ragebait", "rage bait", "outrage", "destroyed", "furious", "shocking",
  "you won't believe", "exposed", "slams",
];

function evidenceTopicSlug(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 64);
}

function roundedTopicMix(values, fixedTopics = new Set()) {
  const result = Object.fromEntries(
    Object.entries(values)
      .filter(([, value]) => Number.isFinite(Number(value)) && Number(value) > 0)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, value]) => [key, Math.round(Number(value) * 1_000_000) / 1_000_000]),
  );
  const adjustable = Object.keys(result).filter((key) => !fixedTopics.has(key));
  const residual = Math.round((1 - Object.values(result).reduce((sum, value) => sum + value, 0)) * 1_000_000) / 1_000_000;
  if (adjustable.length && residual) {
    const selected = adjustable.sort((left, right) => result[right] - result[left])[0];
    result[selected] = Math.round((result[selected] + residual) * 1_000_000) / 1_000_000;
  }
  return result;
}

function fixtureGoalTargets(goal, current) {
  const explicit = {};
  const percentage = /(\d{1,3}(?:\.\d+)?)\s*(?:%|percent)\s+(?:of\s+)?([a-z][a-z0-9 '&/-]{1,60}?)(?=\s*(?:,|;|\band\b|\bplus\b|\bwith\b|\bwhile\b|\.|$))/gi;
  for (const match of String(goal).matchAll(percentage)) {
    const value = Number(match[1]);
    const topic = evidenceTopicSlug(match[2]);
    if (!(value > 0 && value <= 100) || !topic) {
      throw new CuratorApiError(422, "Topic percentages must be greater than zero and at most 100", null);
    }
    if (Object.hasOwn(explicit, topic)) {
      throw new CuratorApiError(422, "Each explicit topic percentage can appear only once", null);
    }
    explicit[topic] = value / 100;
  }
  const increased = new Set();
  const decreased = new Set();
  const removed = new Set();
  const relative = /\b(more|increase|increased|boost|prioritize|prioritise|focus\s+on|less|fewer|reduce|decrease|decreased|cut\s+back\s+on|no|avoid|without|exclude|remove|stop\s+showing(?:\s+me)?)\s+(?:of\s+)?([a-z0-9][a-z0-9 '&/-]{0,80}?)(?=\s*(?:,|;|\.|!|\?|$|\band\b|\bbut\b|\bwhile\b|\binstead\b))/gi;
  for (const match of String(goal).matchAll(relative)) {
    const direction = match[1].toLowerCase().replace(/\s+/g, " ");
    const tokens = match[2].toLowerCase().match(/[a-z0-9]+/g) || [];
    while (["account", "accounts", "channel", "channels", "content", "creator", "creators", "page", "pages", "post", "posts", "video", "videos"].includes(tokens.at(-1))) tokens.pop();
    if (tokens.at(-1) === "based") tokens.pop();
    const topic = evidenceTopicSlug(tokens.join(" "));
    if (!topic) continue;
    if (["more", "increase", "increased", "boost", "prioritize", "prioritise", "focus on"].includes(direction)) increased.add(topic);
    else if (["less", "fewer", "reduce", "decrease", "decreased", "cut back on"].includes(direction)) decreased.add(topic);
    else removed.add(topic);
  }
  const conflicts = [...increased].filter((topic) => decreased.has(topic) || removed.has(topic));
  if (conflicts.length) throw new CuratorApiError(422, `Topic directions conflict for: ${conflicts.sort().join(", ")}`, null);
  const fixed = new Set(Object.keys(explicit));
  const explicitTotal = Object.values(explicit).reduce((sum, value) => sum + value, 0);
  if (explicitTotal > 1.000001) {
    throw new CuratorApiError(422, "Explicit topic percentages cannot exceed 100%", null);
  }
  if (!fixed.size && !increased.size && !decreased.size && !removed.size) return roundedTopicMix(current);
  const result = { ...explicit };
  const remaining = Math.max(0, 1 - explicitTotal);
  if (remaining && /(?:remainder|rest)\s+(?:exploratory|exploration)/i.test(goal)) {
    result.exploration = remaining;
    return roundedTopicMix(result, fixed);
  }
  const flexible = Object.fromEntries(
    Object.entries(current).filter(([key, value]) => !fixed.has(key) && Number(value) > 0),
  );
  const baseValues = Object.values(flexible);
  const baseline = baseValues.length
    ? baseValues.reduce((sum, value) => sum + Number(value), 0) / baseValues.length
    : 1;
  for (const topic of removed) delete flexible[topic];
  for (const topic of decreased) if (Object.hasOwn(flexible, topic)) flexible[topic] *= 0.2;
  for (const topic of increased) if (!fixed.has(topic)) flexible[topic] = Math.max(flexible[topic] || baseline, baseline * 0.5) * 2;
  const flexibleTotal = Object.values(flexible).reduce((sum, value) => sum + Number(value), 0);
  if (remaining && flexibleTotal) {
    for (const [key, value] of Object.entries(flexible)) result[key] = remaining * Number(value) / flexibleTotal;
  } else if (remaining) {
    result.exploration = remaining;
  }
  return roundedTopicMix(result, fixed);
}

function fixtureEvidencePlatform(rawUrl) {
  let url;
  try {
    url = new URL(String(rawUrl || ""));
  } catch {
    throw new CuratorApiError(422, "Feed evidence links must be valid HTTPS URLs", null);
  }
  if (url.protocol !== "https:" || url.username || url.password || url.port) {
    throw new CuratorApiError(422, "Feed evidence links must be ordinary public HTTPS URLs", null);
  }
  const host = url.hostname.toLowerCase();
  if (["youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"].includes(host)) return "youtube";
  if (["bsky.app", "www.bsky.app"].includes(host)) return "bluesky";
  if (["instagram.com", "www.instagram.com"].includes(host)) return "instagram";
  throw new CuratorApiError(422, "Feed evidence currently supports YouTube, Bluesky, and Instagram", null);
}

function fixtureEvidenceInference(text) {
  const lowered = String(text || "").toLowerCase();
  const topics = Object.entries(FIXTURE_EVIDENCE_LEXICON)
    .filter(([, terms]) => terms.some((term) => lowered.includes(term)))
    .map(([topic]) => topic);
  const ragebait = FIXTURE_RAGEBAIT_TERMS.some((term) => lowered.includes(term));
  const hits = topics.length + Number(ragebait);
  return {
    topics,
    ragebait_signal: ragebait,
    matched_terms: [],
    confidence: hits ? Math.min(0.95, 0.25 + hits * 0.12) : 0.1,
    method: "deterministic_keyword_taxonomy_v1",
  };
}

function fixtureEvidenceMetrics(items) {
  const topicCounts = {};
  let ragebait = 0;
  for (const item of items) {
    const topics = item.inference.topics.length ? item.inference.topics : ["unclassified"];
    for (const topic of topics) topicCounts[topic] = (topicCounts[topic] || 0) + 1 / topics.length;
    ragebait += Number(item.inference.ragebait_signal);
  }
  const total = Object.values(topicCounts).reduce((sum, value) => sum + value, 0) || 1;
  return {
    topic_distribution: Object.fromEntries(Object.entries(topicCounts).map(([key, value]) => [key, Math.round(value / total * 10_000) / 10_000])),
    ragebait_rate: Math.round(ragebait / items.length * 10_000) / 10_000,
    source_concentration: 0,
    provider_verified_rate: 0,
    sample_size: items.length,
  };
}

function fixtureProviderControls() {
  return [
    { platform: "youtube", mode: "connected_or_guided", supported_controls: ["subscribe_creator", "unsubscribe_creator"], candidate_creators: [], manual_controls: ["Not interested", "Don't recommend channel"], excluded_controls: ["automated likes", "private Home ranking access"] },
    { platform: "bluesky", mode: "connected_or_guided", supported_controls: ["follow_creator", "unfollow_creator", "mute_keyword"], candidate_creators: [], manual_controls: ["select or pin a custom feed"], excluded_controls: ["automated likes", "private Discover ranking access"] },
    { platform: "instagram", mode: "guided_only", supported_controls: ["selected export import"], candidate_creators: [], manual_controls: ["Interested", "Not interested", "Following feed review"], excluded_controls: ["consumer FYP API", "automated likes", "automated follows"] },
  ];
}

function fixtureAnalyzeFeedEvidence(spec) {
  const goal = String(spec.goal || "").trim();
  if (!goal || goal.length > 1200) throw new CuratorApiError(422, "A feed goal of at most 1200 characters is required", null);
  if (!Array.isArray(spec.links) || spec.links.length < 1 || spec.links.length > 12) throw new CuratorApiError(422, "Provide between one and 12 feed evidence links", null);
  if (spec.stage === "after" && !spec.baselineSnapshotId) throw new CuratorApiError(422, "An after sample requires its before sample", null);
  const observedAt = nextFixtureIdentity("OBSERVED");
  const items = spec.links.map((link, index) => {
    const note = String(link.note || "").trim();
    if (note.length > 600 || note.includes("\0")) throw new CuratorApiError(422, "Each feed evidence note must be at most 600 characters", null);
    const platform = fixtureEvidencePlatform(link.url);
    return {
      platform,
      provider_id: `selected-link-${index + 1}`,
      metadata_source: "user_selected_link_only",
      metadata_verified: false,
      observed_at: observedAt.iso,
      title: "",
      description: "",
      author: "",
      tags: [],
      url: String(link.url),
      user_note: note,
      inference: fixtureEvidenceInference(note),
    };
  });
  const metrics = fixtureEvidenceMetrics(items);
  const snapshotId = nextFixtureIdentity("EVIDENCE").id;
  const passport = fixtureOwnerPassport();
  const snapshot = {
    id: snapshotId,
    owner_id: runtime.fixtureOwnerId,
    passport_id: runtime.fixturePassportId,
    passport_version: passport.version,
    stage: spec.stage || "before",
    observed_at: observedAt.iso,
    item_count: items.length,
    items,
    metrics,
    claim_boundary: "A sample of links and notes selected by the judge. No provider metadata or private feed history was read.",
  };
  runtime.feedEvidenceSnapshots.set(snapshotId, snapshot);
  const targets = fixtureGoalTargets(goal, passport.topic_targets);
  const reduceRagebait = /(?:less|reduce|avoid|no)\s+(?:rage\s?bait|outrage)/i.test(goal);
  const hardExclusions = new Set(passport.hard_exclusions);
  if (reduceRagebait) hardExclusions.add("ragebait");
  const changes = { intent: goal, topic_targets: targets };
  if (reduceRagebait) {
    changes.hard_exclusions = [...hardExclusions].sort();
    changes.max_outrage = Math.min(passport.max_outrage, 0.03);
  }
  const proposal = {
    goal_interpretation: Object.entries(targets).sort(([, left], [, right]) => right - left).map(([topic, value]) => `${Math.round(value * 100)}% ${topic.replaceAll("_", " ")}`).join(", "),
    target_topic_weights: targets,
    passport_changes: changes,
    observed_metrics: metrics,
    provider_controls: fixtureProviderControls(),
    translation_losses: ["Topic percentages are targets for the samples judges choose, not direct ranking controls.", "This browser-only sample uses the judge's notes; provider metadata was not fetched."],
    execution_boundary: "Applying this result updates the portable Curate Passport only.",
    interpretation_source: "browser_local",
  };
  let comparison = null;
  if (spec.stage === "after") {
    const baseline = runtime.feedEvidenceSnapshots.get(spec.baselineSnapshotId);
    if (!baseline || baseline.stage !== "before") throw new CuratorApiError(404, "The selected before sample was not found", null);
    const topics = new Set([...Object.keys(baseline.metrics.topic_distribution), ...Object.keys(metrics.topic_distribution)]);
    comparison = {
      baseline_snapshot_id: baseline.id,
      after_snapshot_id: snapshot.id,
      topic_shift: Object.fromEntries([...topics].sort().map((topic) => [topic, Math.round(((metrics.topic_distribution[topic] || 0) - (baseline.metrics.topic_distribution[topic] || 0)) * 10_000) / 10_000])),
      ragebait_rate_delta: Math.round((metrics.ragebait_rate - baseline.metrics.ragebait_rate) * 10_000) / 10_000,
      source_concentration_delta: 0,
      claim_boundary: "Change observed across two judge-selected samples; this does not prove platform causation.",
    };
  }
  const proposalId = nextFixtureIdentity("PROPOSAL").id;
  const result = {
    id: proposalId,
    owner_id: runtime.fixtureOwnerId,
    passport_id: runtime.fixturePassportId,
    passport_version: passport.version,
    status: spec.stage === "after" ? "comparison_recorded" : "awaiting_owner_consent",
    goal,
    snapshot_id: snapshotId,
    snapshot,
    created_at: observedAt.iso,
    proposal,
    comparison,
  };
  runtime.feedEvidenceProposals.set(proposalId, result);
  return clone(result);
}

function fixtureApplyFeedEvidence(proposalId, expectedPassportVersion) {
  const stored = runtime.feedEvidenceProposals.get(proposalId);
  if (!stored) throw new CuratorApiError(404, "This feed proposal was not found", null);
  if (stored.status !== "awaiting_owner_consent") throw new CuratorApiError(409, "This feed proposal cannot be applied", stored);
  const passport = fixtureOwnerPassport();
  if (passport.version !== Number(expectedPassportVersion) || passport.version !== stored.passport_version) {
    throw new CuratorApiError(409, "The Passport changed after this sample; capture it again", stored);
  }
  const changes = stored.proposal.passport_changes;
  const resultPassport = {
    ...passport,
    version: passport.version + 1,
    intent: changes.intent || passport.intent,
    topic_targets: changes.topic_targets || passport.topic_targets,
    hard_exclusions: changes.hard_exclusions || passport.hard_exclusions,
    max_outrage: changes.max_outrage ?? passport.max_outrage,
  };
  runtime.fixtureConstitution = {
    ...serverConstitution(resultPassport, runtime.fixtureConstitution),
    hardExclusions: [...resultPassport.hard_exclusions],
  };
  const applied = {
    ...stored,
    status: "applied_to_passport",
    result_passport_version: resultPassport.version,
    passport: resultPassport,
  };
  runtime.feedEvidenceProposals.set(proposalId, applied);
  return {
    ...clone(applied),
    constitution: clone(runtime.fixtureConstitution),
  };
}

function sanitizedAgentCoreEvidence(snapshot) {
  return snapshot.items.map((item) => ({
    platform: item.platform,
    metadata_source: item.metadata_source,
    metadata_verified: false,
    title: String(item.user_note || "").replace(/(?:https?:\/\/|www\.)\S*/gi, "[link removed]").slice(0, 300),
    description: "",
    inferred_topics: item.inference.topics,
    ragebait_signal: item.inference.ragebait_signal,
    confidence: item.inference.confidence,
  }));
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
  agentCoreStatus() {
    return agentCoreGatewayClient.status();
  },

  async planFeedWithAgentCore({ request, evidence }) {
    return {
      source: "agentcore",
      data: await agentCoreGatewayClient.planFeed({
        passport: agentCorePassportSnapshot(),
        request,
        evidence,
      }),
    };
  },

  async loadPassport() {
    if (!serviceAvailable) {
      return {
        source: "fixture",
        data: { passport: clone(runtime.fixtureConstitution), receipts: clone([]), health: "ready", scheduler: "fixture" },
      };
    }
    try {
      const demo = await fetchDemoWithOnboarding();
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

  async loadConnections() {
    if (!serviceAvailable) {
      runtime.connections.clear();
      return { source: "fixture", data: { providers: [], connections: [], configuration: "service_required" } };
    }
    await ensureServiceContext();
    try {
      const health = await requestJson("/health", { method: "GET" });
      if (health?.connections === "local_keys_required") {
        let providers = [];
        try {
          providers = await requestJson("/api/oauth/providers", { method: "GET" });
        } catch {
          // Provider status is optional when local encryption keys are absent.
        }
        runtime.connections.clear();
        return {
          source: "service",
          data: { providers, connections: [], configuration: "local_keys_required" },
        };
      }
    } catch {
      // Older compatible Curator services may not expose connection readiness.
    }
    try {
      const [providers, connections] = await Promise.all([
        requestJson("/api/oauth/providers", { method: "GET" }),
        requestJson(`/api/connections?actor_id=${encodeURIComponent(runtime.actorId)}`, { method: "GET" }),
      ]);
      runtime.connections.clear();
      for (const connection of connections) runtime.connections.set(connection.id, connection);
      return { source: "service", data: { providers, connections, configuration: "configured" } };
    } catch (error) {
      if (error instanceof CuratorApiError && error.status === 503) {
        runtime.connections.clear();
        let providers = [];
        try {
          providers = await requestJson("/api/oauth/providers", { method: "GET" });
        } catch {
          // Provider status is optional when local encryption keys are absent.
        }
        return {
          source: "service",
          data: { providers, connections: [], configuration: "local_keys_required" },
        };
      }
      throw error;
    }
  },

  async beginOAuthConnection(platform, { handle = "" } = {}) {
    if (!serviceAvailable) {
      throw new CuratorApiError(503, "The local Curator service is required for OAuth", null);
    }
    await ensureServiceContext();
    const redirectUri = env.VITE_FEED_PASSPORT_OAUTH_REDIRECT_URI
      || `${globalThis.location?.origin || "http://127.0.0.1:5173"}/oauth/callback`;
    const value = await requestJson(`/api/connections/${encodeURIComponent(platform)}/oauth/start`, {
      method: "POST",
      body: JSON.stringify({
        actor_id: runtime.actorId,
        ...(platform === "bluesky"
          ? { handle: String(handle).trim() }
          : { redirect_uri: redirectUri }),
      }),
    });
    globalThis.sessionStorage?.setItem("feed-passport-oauth-platform", platform);
    return { source: "service", data: value };
  },

  async completeOAuthConnection({ platform, state, code, callbackQuery = "" }) {
    if (!serviceAvailable) {
      throw new CuratorApiError(503, "The local Curator service is required for OAuth", null);
    }
    await ensureServiceContext();
    const value = await requestJson(`/api/connections/${encodeURIComponent(platform)}/oauth/callback`, {
      method: "POST",
      body: JSON.stringify(platform === "bluesky"
        ? {
            actor_id: runtime.actorId,
            query: String(callbackQuery).replace(/^\?/, "").trim(),
          }
        : {
            actor_id: runtime.actorId,
            state,
            code,
          }),
    });
    runtime.connections.set(value.id, value);
    globalThis.sessionStorage?.removeItem("feed-passport-oauth-platform");
    return { source: "service", data: value };
  },

  async revokeOAuthConnection(connection) {
    if (!serviceAvailable) {
      throw new CuratorApiError(503, "The local Curator service is required for OAuth", null);
    }
    await ensureServiceContext();
    const value = await requestJson(`/api/connections/${encodeURIComponent(connection.id)}/revoke`, {
      method: "POST",
      body: JSON.stringify({
        actor_id: runtime.actorId,
        expected_version: connection.version,
      }),
    });
    runtime.connections.set(value.id, value);
    return { source: "service", data: value };
  },

  listState() {
    return withFixtureFallback(
      async () => {
        const demo = await fetchDemoWithOnboarding();
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
          resumableGuidedMigration: ui.resumableGuidedMigration,
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
        resumableGuidedMigration: null,
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
        const accountId = platform === "feed_passport_lab" ? "source-main" : accountIdForPlatform(platform);
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
              ? "A new Passport was created from the Curate Lab starting feed."
              : `${actionLabel(platform)} used the saved test-account starting point.`,
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
          title: name || "Captured practice Passport",
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
            "Practice source captured",
            `${actionLabel(normalizePlatform(source || "lab"))} was represented by a local practice feed.`,
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
          throw new CuratorApiError(403, "Practice imports must belong to the current Curate Passport", document);
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

  async previewInstagramImport(file) {
    if (!serviceAvailable) {
      throw new CuratorApiError(
        503,
        "Instagram export intake is available when the local Curate service is connected.",
        { fallback_permitted: false },
      );
    }
    if (!(file instanceof Blob)) {
      throw new CuratorApiError(422, "Choose an Instagram JSON or ZIP export file", null);
    }
    if (file.size < 1 || file.size > 64 * 1024 * 1024) {
      throw new CuratorApiError(413, "Instagram export input must be between 1 byte and 64 MiB", null);
    }
    await ensureServiceContext();
    // Preserve only the parser discriminator. Local filenames can contain a
    // person's name or export-folder details and must not enter HTTP logs.
    const filename = instagramImportTransportFilename(file);
    const preview = await requestJson(
      `/api/platform-imports/instagram/preview?actor_id=${encodeURIComponent(runtime.actorId)}&passport_id=${encodeURIComponent(runtime.passportId)}&filename=${encodeURIComponent(filename)}`,
      {
        method: "POST",
        body: file,
        headers: {
          "Content-Type": "application/octet-stream",
          "X-Feed-Passport-Local-Import": "1",
        },
        timeoutMs: 30_000,
      },
    );
    return { source: "service", data: preview };
  },

  async applyInstagramImport(importId, selectedHandles) {
    if (!serviceAvailable) {
      throw new CuratorApiError(503, "Instagram export intake requires the local Curator service", { fallback_permitted: false });
    }
    await ensureServiceContext();
    const result = await requestJson(`/api/platform-imports/instagram/${encodeURIComponent(importId)}/apply`, {
      method: "POST",
      headers: { "X-Feed-Passport-Local-Import": "1" },
      body: JSON.stringify({
        actor_id: runtime.actorId,
        passport_id: runtime.passportId,
        expected_passport_version: Number(runtime.passport?.version || 0),
        selected_handles: [...new Set(selectedHandles)].sort((left, right) => left.localeCompare(right)),
      }),
      timeoutMs: 15_000,
    });
    const passport = result.passport;
    if (!passport) throw new CuratorApiError(502, "Curator API omitted the revised Passport", result);
    activateServicePassport(passport);
    return { source: "service", data: { ...result, constitution: serverConstitution(passport) } };
  },

  async discardInstagramImport(importId) {
    if (!serviceAvailable) {
      throw new CuratorApiError(
        503,
        "Instagram import discard requires the local Curator service; the private preview will otherwise remain only until its short expiry.",
        { fallback_permitted: false },
      );
    }
    await ensureServiceContext();
    const result = await requestJson(`/api/platform-imports/instagram/${encodeURIComponent(importId)}/discard`, {
      method: "POST",
      headers: { "X-Feed-Passport-Local-Import": "1" },
      body: JSON.stringify({ actor_id: runtime.actorId }),
    });
    return { source: "service", data: result };
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
            account_id: options.accountId || accountIdForPlatform(platform),
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
        const outcome = summarizeMigrationExecution(executed, payload);
        return {
          ...outcome,
          guided_handoff: executed.guided_handoff || null,
          receipt: executed.receipt_id ? {
            id: executed.receipt_id,
            type: outcome.resultType,
            detail: outcome.resultDetail,
            time: formatDateLabel(executed.completed_at),
            status: outcome.needsAttention ? "Needs attention" : "Succeeded",
            reversible: outcome.applied > 0 && Boolean((migration.plan?.actions || []).some((item) => item.reversible)),
            checkpoint: `v${migration.passport_version}`,
            _platform: migration.platform,
          } : null,
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

  async resolveGuidedHandoffStep(handoffId, stepId, resolution) {
    if (!serviceAvailable) {
      throw new CuratorApiError(503, "Guided handoff records require the local Curator service", { fallback_permitted: false });
    }
    await ensureServiceContext();
    const handoff = await requestJson(`/api/guided-handoffs/${encodeURIComponent(handoffId)}/steps/${encodeURIComponent(stepId)}/resolve`, {
      method: "POST",
      body: JSON.stringify({ actor_id: runtime.actorId, resolution }),
    });
    return { source: "service", data: handoff };
  },

  async finalizeGuidedHandoff(handoffId) {
    if (!serviceAvailable) {
      throw new CuratorApiError(503, "Guided handoff records require the local Curator service", { fallback_permitted: false });
    }
    await ensureServiceContext();
    const handoff = await requestJson(`/api/guided-handoffs/${encodeURIComponent(handoffId)}/finalize`, {
      method: "POST",
      body: JSON.stringify({ actor_id: runtime.actorId }),
    });
    const migration = [...runtime.migrations.values()].find(
      (value) => value.guided_handoff_id === handoff.id
        || value.guided_handoff?.id === handoff.id,
    );
    const receipt = handoff.receipt ? guidedAttestationReceiptForUi({
      ...(migration || {}),
      id: migration?.id || `guided:${handoff.id}`,
      owner_id: handoff.owner_id,
      passport_id: handoff.passport_id,
      passport_version: handoff.passport_version,
      platform: handoff.platform,
      guided_handoff: handoff,
    }) : null;
    return { source: "service", data: { handoff, receipt } };
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
            "The first practice profile chose what to share; the blend is waiting for the second profile.",
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
            "The invitation was withdrawn before the second profile joined.",
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
          "Practice feed correction tried",
          "Curate added three different sources and reduced repeated-creator weight in the practice feed.",
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
            detail: `${preserved.display_name} was matched to ${preserved.destination_identity} with ${Math.round(Number(preserved.confidence) * 100)}% directory confidence.`,
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
            `${creator.name} was matched to ${creator.destinationHandle} with ${creator.confidence}% directory confidence.`,
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
      const agentCoreStatus = agentCoreGatewayClient.status();
      const oidcConfigured = Boolean(
        (env.VITE_CURATE_OIDC_CLIENT_ID || env.VITE_FEED_PASSPORT_OIDC_CLIENT_ID)
        && (env.VITE_CURATE_OIDC_HOSTED_UI_URL || env.VITE_FEED_PASSPORT_OIDC_HOSTED_UI_URL)
        && (env.VITE_CURATE_OIDC_ISSUER || env.VITE_FEED_PASSPORT_OIDC_ISSUER),
      );
      if (agentCoreStatus.configured && oidcConfigured) {
        const signedIn = Boolean(String((await accessTokenProvider()) || "").trim());
        return {
          source: "agentcore",
          data: {
            configured: true,
            online: signedIn,
            readiness: signedIn ? "ready" : "sign_in_required",
            provider: "aws_bedrock_agentcore",
            model_id: "deployment_configured",
            endpoint_scope: "aws_managed",
            mode: "proposal_only",
            external_model_calls: true,
            paid_model_calls: true,
            reason: signedIn ? null : "Sign in with the judge account to use the cloud planner.",
          },
        };
      }
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
        "Open-ended Curate suggestions are available when the local Curate helper is connected.",
        { fallback_permitted: false },
      );
    }
    const safeRequest = String(request || "").trim();
    if (!safeRequest || safeRequest.length > 1200) {
      throw new CuratorApiError(
        422,
        "Your request must contain between one and 1200 text characters.",
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
      timeoutMs: LOCAL_MODEL_REQUEST_TIMEOUT_MS,
    });
    return { source: "service", data: proposal, platforms };
  },

  async analyzeFeedEvidence(spec) {
    if (!serviceAvailable) {
      return { source: "fixture", data: fixtureAnalyzeFeedEvidence(spec) };
    }
    await ensureServiceContext();
    const proposal = await requestJson("/api/agent/feed-evidence/analyze", {
      method: "POST",
      body: JSON.stringify({
        actor_id: runtime.actorId,
        passport_id: runtime.passportId,
        goal: String(spec.goal || "").trim(),
        stage: spec.stage || "before",
        links: spec.links,
        youtube_connection_id: spec.youtubeConnectionId || null,
        baseline_snapshot_id: spec.baselineSnapshotId || null,
      }),
      timeoutMs: 30_000,
    });
    return { source: "service", data: proposal };
  },

  async applyFeedEvidence(proposalId, expectedPassportVersion) {
    if (!serviceAvailable) {
      return {
        source: "fixture",
        data: fixtureApplyFeedEvidence(proposalId, expectedPassportVersion),
      };
    }
    await ensureServiceContext();
    const result = await requestJson(
      `/api/agent/feed-evidence/${encodeURIComponent(proposalId)}/apply`,
      {
        method: "POST",
        body: JSON.stringify({
          actor_id: runtime.actorId,
          expected_passport_version: Number(expectedPassportVersion),
        }),
      },
    );
    if (!result.passport) {
      throw new CuratorApiError(502, "Curator API omitted the revised Passport", result);
    }
    runtime.passport = { ...runtime.passport, ...result.passport };
    return {
      source: "service",
      data: {
        ...result,
        constitution: serverConstitution(runtime.passport, runtime.fixtureConstitution),
      },
    };
  },

  async planFeedEvidenceWithModel(proposalId, expectedPassportVersion) {
    if (!serviceAvailable) {
      const stored = runtime.feedEvidenceProposals.get(proposalId);
      if (!stored) throw new CuratorApiError(404, "This feed proposal was not found", null);
      if (stored.status !== "awaiting_owner_consent") throw new CuratorApiError(409, "This feed proposal cannot be cloud-planned", stored);
      if (stored.passport_version !== Number(expectedPassportVersion)) throw new CuratorApiError(409, "The Passport changed after this sample; capture it again", stored);
      const cloud = await agentCoreGatewayClient.planFeed({
        passport: agentCorePassportSnapshot(),
        request: stored.goal,
        evidence: sanitizedAgentCoreEvidence(stored.snapshot),
      });
      const modelTargets = cloud.proposal.target_topic_weights;
      const hardExclusions = [...new Set([
        ...(stored.proposal.passport_changes.hard_exclusions || []),
        ...(cloud.proposal.hard_exclusions || []),
      ])].sort();
      const updated = {
        ...stored,
        proposal: {
          ...stored.proposal,
          target_topic_weights: modelTargets,
          passport_changes: {
            ...stored.proposal.passport_changes,
            topic_targets: modelTargets,
            hard_exclusions: hardExclusions,
          },
          agent_rationale: cloud.proposal.rationale,
          interpretation_source: "aws_bedrock_agentcore",
        },
        agent_evidence: cloud.evidence,
      };
      runtime.feedEvidenceProposals.set(proposalId, updated);
      return { source: "agentcore", data: clone(updated) };
    }
    await ensureServiceContext();
    const result = await requestJson(
      `/api/agent/feed-evidence/${encodeURIComponent(proposalId)}/model-plan`,
      {
        method: "POST",
        body: JSON.stringify({
          actor_id: runtime.actorId,
          expected_passport_version: Number(expectedPassportVersion),
        }),
        timeoutMs: LOCAL_MODEL_REQUEST_TIMEOUT_MS,
      },
    );
    return { source: "service", data: result };
  },

  async previewAgentMissionWithModel(spec) {
    if (!serviceAvailable) {
      throw new CuratorApiError(
        503,
        "Agent-made previews are available when the local Curate helper is connected. A quick practice preview is still available.",
        { fallback_permitted: false },
      );
    }
    await ensureServiceContext();
    const mission = await requestJson("/api/agent/missions/plan", {
      method: "POST",
      body: JSON.stringify(agentMissionRequestBody(spec)),
      timeoutMs: LOCAL_MODEL_REQUEST_TIMEOUT_MS,
    });
    runtime.agentMissions.set(mission.id, mission);
    return { source: "service", data: mission };
  },

  async listLiveCommissions() {
    if (!serviceAvailable) {
      throw new CuratorApiError(
        503,
        "Connected-account runs are available when the local Curate service is connected.",
        { fallback_permitted: false },
      );
    }
    await ensureServiceContext();
    const commissions = await requestJson(
      `/api/agent/live-commissions?actor_id=${encodeURIComponent(runtime.actorId)}`,
      { method: "GET" },
    );
    runtime.liveCommissions.clear();
    for (const commission of commissions) runtime.liveCommissions.set(commission.id, commission);
    return { source: "service", data: commissions };
  },

  async previewLiveCommission(spec) {
    if (!serviceAvailable) {
      throw new CuratorApiError(
        503,
        "Curate needs the local service and helper model to prepare a connected-account run.",
        { fallback_permitted: false },
      );
    }
    await ensureServiceContext();
    const commission = await requestJson("/api/agent/live-commissions/preview", {
      method: "POST",
      timeoutMs: LOCAL_MODEL_REQUEST_TIMEOUT_MS,
      body: JSON.stringify({
        actor_id: runtime.actorId,
        passport_id: runtime.passportId,
        priority_mode: spec.priorityMode,
        platform: spec.platform,
        destination_connection_id: spec.connectionId,
        max_total_actions: Number(spec.maxTotalActions),
      }),
    });
    runtime.liveCommissions.set(commission.id, commission);
    return { source: "service", data: commission };
  },

  async runLiveCommission(commissionId) {
    if (!serviceAvailable) throw new CuratorApiError(503, "The connected service is unavailable.", null);
    await ensureServiceContext();
    const approval = await requestJson(`/api/agent/live-commissions/${encodeURIComponent(commissionId)}/approval`, {
      method: "POST",
      body: JSON.stringify({ actor_id: runtime.actorId, ttl_seconds: 600 }),
    });
    const commission = await requestJson(`/api/agent/live-commissions/${encodeURIComponent(commissionId)}/execute`, {
      method: "POST",
      body: JSON.stringify({
        actor_id: runtime.actorId,
        approval_token: approval.approval_token || approval.token,
      }),
    });
    runtime.liveCommissions.set(commission.id, commission);
    return { source: "service", data: commission };
  },

  async reconcileLiveCommission(commissionId) {
    if (!serviceAvailable) throw new CuratorApiError(503, "The connected service is unavailable.", null);
    await ensureServiceContext();
    const commission = await requestJson(`/api/agent/live-commissions/${encodeURIComponent(commissionId)}/reconcile`, {
      method: "POST",
      body: JSON.stringify({ actor_id: runtime.actorId }),
    });
    runtime.liveCommissions.set(commission.id, commission);
    return { source: "service", data: commission };
  },

  async cancelLiveCommission(commissionId) {
    if (!serviceAvailable) throw new CuratorApiError(503, "The connected service is unavailable.", null);
    await ensureServiceContext();
    const commission = await requestJson(`/api/agent/live-commissions/${encodeURIComponent(commissionId)}/cancel`, {
      method: "POST",
      body: JSON.stringify({ actor_id: runtime.actorId }),
    });
    runtime.liveCommissions.set(commission.id, commission);
    return { source: "service", data: commission };
  },

  async rollbackLiveCommission(commissionId) {
    if (!serviceAvailable) throw new CuratorApiError(503, "The connected service is unavailable.", null);
    await ensureServiceContext();
    const approval = await requestJson(`/api/agent/live-commissions/${encodeURIComponent(commissionId)}/rollback/approval`, {
      method: "POST",
      body: JSON.stringify({ actor_id: runtime.actorId, ttl_seconds: 600 }),
    });
    const commission = await requestJson(`/api/agent/live-commissions/${encodeURIComponent(commissionId)}/rollback`, {
      method: "POST",
      body: JSON.stringify({
        actor_id: runtime.actorId,
        approval_token: approval.approval_token || approval.token,
      }),
    });
    runtime.liveCommissions.set(commission.id, commission);
    return { source: "service", data: commission };
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
            ? { ...step, status: "rolled_back", detail: `Undid ${mission.receipt_ids.length} practice-feed changes; the starting state matched.` }
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
            actor: "Curate",
            detail: reply.message,
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
