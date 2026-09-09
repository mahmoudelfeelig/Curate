import { INITIAL_CONSTITUTION } from "../data.js";

const UI_TO_API_TOPIC = {
  independent_games: "indie",
  design: "design",
  local_culture: "local",
  research: "research",
};
export function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

export function formatDateLabel(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value || "NOW").toUpperCase();
  return date
    .toLocaleString("en-GB", {
      timeZone: "UTC",
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    })
    .replace(",", " ·")
    .toUpperCase();
}

export function normalizePlatform(value) {
  const normalized = String(value || "").trim().toLowerCase().replace(/\s+/g, "_");
  return ["lab", "proof_lab", "feed_passport_lab"].includes(normalized)
    ? "feed_passport_lab"
    : normalized;
}

export function instagramImportTransportFilename(file) {
  const originalName = typeof file?.name === "string" ? file.name : "";
  const mediaType = typeof file?.type === "string" ? file.type.toLowerCase() : "";
  return originalName.toLowerCase().endsWith(".zip") || mediaType.includes("zip")
    ? "accounts-center.zip"
    : "following.json";
}

export function destinationAccount(platform) {
  return platform === "feed_passport_lab" ? "destination-new" : `${platform}-demo-account`;
}

export function uiTopicTargets(constitution) {
  const values = {};
  for (const topic of constitution.topics || []) {
    const key = UI_TO_API_TOPIC[topic.id] || topic.id;
    values[key] = Number(topic.percent || 0) / 100;
  }
  return values;
}

export function normalizeLanguage(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return { english: "en", german: "de", deutsch: "de" }[normalized]
    || normalized.slice(0, 2);
}

export function serverConstitution(passport, current = INITIAL_CONSTITUTION) {
  const next = clone(current);
  next.version = Number(passport.version || current.version);
  next.title = passport.name || current.title;
  next.intent = passport.intent || current.intent;
  next.topics = current.topics.map((topic) => ({
    ...topic,
    percent: Math.round(Number(passport.topic_targets?.[UI_TO_API_TOPIC[topic.id] || topic.id] || 0) * 100),
  }));
  next.serendipity = Math.round(Number(passport.serendipity ?? current.serendipity / 100) * 100);
  next.outrageCeiling = Math.round(Number(passport.max_outrage ?? current.outrageCeiling / 100) * 100);
  next.sourceDiversity = Math.round((1 - Number(passport.max_source_share ?? 0.3)) * 100);
  next.creatorCeiling = Math.round(Number(passport.max_source_share ?? current.creatorCeiling / 100) * 100);
  next.languages = (passport.languages || ["en"]).map((item) => {
    if (item === "en") return "English";
    if (item === "de") return "German";
    return String(item).toUpperCase();
  });
  return next;
}

export function receiptPlatform(receipt, demo) {
  const migration = (demo?.migrations || []).find((item) => item.receipt_id === receipt.id);
  return migration?.platform || "feed_passport_lab";
}

export function serverReceipt(receipt, demo) {
  const outcomes = receipt.outcomes || [];
  const rolledBack = receipt.status === "rolled_back";
  const rollbackIncomplete = ["rollback_partial", "rollback_failed"].includes(receipt.status);
  const reversible = !rolledBack && outcomes.some(
    (item) => item.status === "executed" && item.action?.reversible,
  );
  return {
    id: receipt.id,
    type: rolledBack
      ? "Migration rolled back"
      : rollbackIncomplete
        ? "Migration rollback incomplete"
        : "Migration receipt",
    detail: `${outcomes.length} destination outcomes recorded for ${receipt.destination_id}.`,
    time: formatDateLabel(receipt.issued_at || receipt.completed_at),
    status: rolledBack ? "Rolled back" : rollbackIncomplete ? "Needs attention" : "Succeeded",
    reversible,
    checkpoint: receipt.previous_checkpoint_id || `v${receipt.passport_version || 1}`,
    _platform: receiptPlatform(receipt, demo),
  };
}

function timestampValue(...values) {
  for (const value of values) {
    const parsed = Date.parse(String(value || ""));
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

function ownedPassportIds(demo, ownerId, activePassportId) {
  const ids = new Set(
    (demo?.passports || [])
      .filter((passport) => String(passport?.owner_id || "") === ownerId)
      .map((passport) => String(passport.id)),
  );
  if (activePassportId) ids.add(activePassportId);
  return ids;
}

function isOwnedProjection(value, ownerId, passportIds) {
  const directOwner = String(value?.owner_id || "");
  if (directOwner) return directOwner === ownerId;
  return passportIds.has(String(value?.passport_id || ""));
}

export function guidedAttestationReceiptForUi(migration) {
  const handoff = migration?.guided_handoff;
  const receipt = handoff?.receipt;
  const summary = receipt?.summary;
  if (handoff?.state !== "finalized" || !receipt || !summary) return null;

  const completed = Number(summary.completed_by_user || 0);
  const skipped = Number(summary.skipped_by_user || 0);
  const unavailable = Number(summary.control_not_found || 0);
  const apiWrites = Number(summary.api_writes);
  const verifiedOutcomes = Number(summary.recommendation_outcomes_verified);
  const platformVerified = summary.platform_verified === true;
  const boundaryValid = apiWrites === 0
    && verifiedOutcomes === 0
    && platformVerified === false;

  return {
    id: String(receipt.id),
    type: boundaryValid
      ? "User-attested guided handoff record"
      : "Guided handoff record needs attention",
    detail: boundaryValid
      ? `${completed} steps marked completed by the user; ${skipped + unavailable} skipped or unavailable. This is a user attestation, not a canonical API-write receipt: 0 API writes, 0 recommendation outcomes verified, and platform verification is false.`
      : "The stored guided record has inconsistent verification fields and is not being presented as platform execution evidence.",
    time: formatDateLabel(receipt.issued_at || handoff.finalized_at || handoff.updated_at),
    status: boundaryValid ? "User attested" : "Needs attention",
    reversible: false,
    checkpoint: `v${handoff.passport_version || migration.passport_version || 1}`,
    _platform: String(handoff.platform || migration.platform || "guided"),
    _guidedAttestation: true,
    _recordClass: "guided_user_attestation",
    _migrationId: String(migration.id || ""),
    _apiWrites: boundaryValid ? 0 : apiWrites,
    _recommendationOutcomesVerified: boundaryValid ? 0 : verifiedOutcomes,
    _platformVerified: boundaryValid ? false : platformVerified,
  };
}

export function historyReceiptsForUi(demo, { ownerId, passportId }) {
  const normalizedOwnerId = String(ownerId || "");
  const normalizedPassportId = String(passportId || "");
  const passportIds = ownedPassportIds(demo, normalizedOwnerId, normalizedPassportId);
  const records = [];

  for (const receipt of demo?.receipts || []) {
    if (!isOwnedProjection(receipt, normalizedOwnerId, passportIds)) continue;
    records.push({
      value: serverReceipt(receipt, demo),
      at: timestampValue(receipt.issued_at, receipt.completed_at),
    });
  }
  for (const migration of demo?.migrations || []) {
    if (!isOwnedProjection(migration, normalizedOwnerId, passportIds)) continue;
    const value = guidedAttestationReceiptForUi(migration);
    if (!value) continue;
    records.push({
      value,
      at: timestampValue(
        migration.guided_handoff?.receipt?.issued_at,
        migration.guided_handoff?.finalized_at,
        migration.guided_handoff?.updated_at,
      ),
    });
  }

  const seen = new Set();
  return records
    .sort((left, right) => right.at - left.at || right.value.id.localeCompare(left.value.id))
    .map((record) => record.value)
    .filter((record) => {
      if (seen.has(record.id)) return false;
      seen.add(record.id);
      return true;
    });
}

export function actionLabel(value) {
  return String(value || "planned action")
    .split("_")
    .map((item) => item.charAt(0).toUpperCase() + item.slice(1))
    .join(" ");
}

export function migrationPreviewForUi(migration) {
  const grouped = new Map();
  const planActions = migration.plan?.actions || [];
  for (const action of planActions) {
    const delivery = action.parameters?.delivery;
    const localProofEnvironment = migration.platform === "feed_passport_lab"
      || String(migration.platform || "").startsWith("twin:");
    const authorizedLiveDelivery = delivery === "official_api"
      && action.parameters?.certification === "authorized_live";
    const mode = localProofEnvironment
      ? "Lab"
      : delivery === "guided_handoff"
        ? "Guided"
        : authorizedLiveDelivery
          ? "Executable"
          : "Unavailable";
    const key = `${action.action_type}:${mode}`;
    const previous = grouped.get(key) || { action: actionLabel(action.action_type), count: 0, mode };
    previous.count += 1;
    grouped.set(key, previous);
  }
  const actions = [...grouped.values()];
  if (!actions.length) {
    actions.push({ action: "No destination change required", count: 0, mode: "Guided" });
  }
  const losses = (migration.plan?.losses || []).map((loss) => ({
    severity: ["high", "blocking"].includes(String(loss.severity).toLowerCase()) ? "Partial" : "Informational",
    title: actionLabel(loss.field || loss.intent_ref || "Translation boundary"),
    detail: [loss.reason, loss.workaround].filter(Boolean).join(" Nearest supported alternative: "),
  }));
  if (!losses.length) {
    losses.push({
      severity: "Informational",
      title: "No translation loss detected",
      detail: "The deterministic preview found a representation for every proposed action at the destination's declared capability level.",
    });
  }
  const guidedSteps = planActions
    .filter((action) => action.parameters?.delivery === "guided_handoff")
    .map((action, index) => ({
      id: typeof action.id === "string" ? action.id : "",
      ordinal: index + 1,
      actionType: typeof action.action_type === "string" ? action.action_type : "",
      action: actionLabel(action.action_type),
      target: typeof action.target === "string" ? action.target : "",
      instruction: typeof action.parameters?.instruction === "string"
        ? action.parameters.instruction
        : "",
    }));
  return { actions, guidedSteps, losses, previewId: migration.id };
}

export function resumableGuidedMigrationForUi(
  migrations,
  { ownerId, passportId },
) {
  const normalizedOwnerId = String(ownerId || "");
  const normalizedPassportId = String(passportId || "");
  const candidates = (migrations || []).filter((migration) => {
    const handoff = migration?.guided_handoff;
    return String(migration?.owner_id || "") === normalizedOwnerId
      && String(migration?.passport_id || "") === normalizedPassportId
      && ["awaiting_handoff", "user_resolved"].includes(String(handoff?.state || ""));
  });
  candidates.sort((left, right) => {
    const leftHandoff = left.guided_handoff || {};
    const rightHandoff = right.guided_handoff || {};
    const timeDelta = timestampValue(
      rightHandoff.updated_at,
      right.completed_at,
      right.created_at,
    ) - timestampValue(
      leftHandoff.updated_at,
      left.completed_at,
      left.created_at,
    );
    if (timeDelta) return timeDelta;
    const revisionDelta = Number(rightHandoff.revision || 0) - Number(leftHandoff.revision || 0);
    if (revisionDelta) return revisionDelta;
    return String(right.id || "").localeCompare(String(left.id || ""));
  });
  const migration = candidates[0];
  if (!migration) return null;
  return {
    migrationId: String(migration.id),
    platform: String(migration.platform),
    destinationAccountId: String(migration.destination_account_id || ""),
    handoff: clone(migration.guided_handoff),
    preview: migrationPreviewForUi(migration),
  };
}

export function hoursForDuration(value) {
  const normalized = String(value || "").toLowerCase();
  const amount = Number.parseInt(normalized, 10);
  if (normalized.includes("day")) return (Number.isFinite(amount) ? amount : 7) * 24;
  if (normalized.includes("hour")) return Number.isFinite(amount) ? amount : 48;
  if (normalized.includes("until")) return 30 * 24;
  return 48;
}

export function futureIso(duration, offsetMs = 0) {
  return new Date(Date.now() + hoursForDuration(duration) * 3_600_000 + offsetMs).toISOString();
}

export function futureIsoForPayload(payload, offsetMs = 0) {
  const exactMinutes = Number(payload?.durationMinutes);
  if (Number.isInteger(exactMinutes) && exactMinutes > 0) {
    return new Date(Date.now() + exactMinutes * 60_000 + offsetMs).toISOString();
  }
  return futureIso(payload?.duration, offsetMs);
}

export function webMcpTemporaryVisaForm(current, { purpose, duration }) {
  return {
    ...current,
    purpose,
    duration,
    durationMinutes: null,
  };
}

export function durationLabel(startValue, endValue) {
  const milliseconds = new Date(endValue).getTime() - new Date(startValue).getTime();
  const totalMinutes = Math.max(1, Math.round(milliseconds / 60_000));
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  return [
    days ? `${days} ${days === 1 ? "day" : "days"}` : "",
    hours ? `${hours} ${hours === 1 ? "hour" : "hours"}` : "",
    minutes ? `${minutes} ${minutes === 1 ? "minute" : "minutes"}` : "",
  ].filter(Boolean).join(" ");
}

export function overlayProjectionForUi(overlay) {
  return {
    id: overlay.id,
    name: overlay.name,
    purpose: "Persisted policy overlay; private activity history was never stored.",
    duration: durationLabel(overlay.starts_at, overlay.expires_at),
    mode: overlay.mode === "reversible_live" ? "Reversible Lab" : "Isolated Lab",
    status: String(overlay.status || "active").replace(/^./, (item) => item.toUpperCase()),
    issuedAt: formatDateLabel(overlay.starts_at),
    expiresAt: formatDateLabel(overlay.expires_at),
  };
}

export function companionModeLabel(strategy) {
  return {
    bridge: "Bridge View",
    weighted: "Weighted Mix",
    common_ground: "Common Ground",
    taste_swap: "Taste Swap",
  }[strategy] || "Bridge View";
}

export function effectivePartnerWeight(strategy, ownerWeight, partnerWeight) {
  let own = Number(ownerWeight || 1);
  let partner = Number(partnerWeight || 1);
  if (strategy === "bridge") {
    own = Math.sqrt(own);
    partner = Math.sqrt(partner);
  } else if (strategy === "taste_swap") {
    [own, partner] = [partner, own];
  }
  return Math.round((partner / Math.max(0.000001, own + partner)) * 100);
}

export function shareSelectionForPassport(passport, share = {}) {
  return {
    topic_names: share.topics ? Object.keys(passport?.topic_targets || {}) : [],
    creator_ids: share.creators ? Object.keys(passport?.creator_preferences || {}) : [],
    include_serendipity: Boolean(share.serendipity),
    include_formats: Boolean(share.formats),
    include_exclusions: Boolean(share.exclusions),
  };
}

export function shareFromSelectedFields(selectedFields = {}) {
  return {
    topics: Boolean(selectedFields.topic_names?.length),
    creators: Boolean(selectedFields.creator_ids?.length),
    serendipity: Boolean(selectedFields.include_serendipity),
    exclusions: Boolean(selectedFields.include_exclusions),
    formats: Boolean(selectedFields.include_formats),
  };
}

export function selectedFieldNames(selectedFields = {}) {
  const fields = [];
  if (selectedFields.topic_names?.length) fields.push("Topics");
  if (selectedFields.creator_ids?.length) fields.push("Creators");
  if (selectedFields.include_formats) fields.push("Formats");
  if (selectedFields.include_exclusions) fields.push("Exclusions");
  if (selectedFields.include_serendipity) fields.push("Serendipity");
  return fields;
}

export function continuousShareBody({
  actorId,
  passport,
  share,
  expiresAt,
  pairId,
  counterpartyOwnerId,
}) {
  const selectedFields = shareSelectionForPassport(passport, share);
  return {
    actor_id: actorId,
    passport_id: passport.id,
    ...selectedFields,
    expires_at: expiresAt,
    scope: "continuous",
    refresh_on_revision: true,
    target_passport_ids: [passport.id],
    pair_id: pairId,
    counterparty_owner_id: counterpartyOwnerId,
  };
}

export function consentProjection(slice, actorId) {
  const selectedFields = clone(slice.selected_fields || {});
  return {
    id: slice.id,
    consentId: slice.consent_id || null,
    principalId: actorId || slice.owner_id,
    passportId: slice.passport_id,
    passportVersion: slice.passport_version,
    status: String(slice.status || "active").replace(/^./, (item) => item.toUpperCase()),
    scope: slice.scope || "continuous",
    refreshOnRevision: Boolean(slice.refresh_on_revision),
    targetPassportIds: clone(slice.target_passport_ids || []),
    selectedFields,
    selectedFieldNames: selectedFieldNames(selectedFields),
    expiresAt: slice.expires_at,
  };
}

export function invitationProjection({ slice, payload, ownerPrincipalId }) {
  return {
    id: slice.id,
    code: payload.partnerCode,
    status: "Awaiting second consent",
    activationPerformed: false,
    scope: "continuous",
    refreshOnRevision: true,
    expiresAt: slice.expires_at,
    duration: payload.duration,
    mode: payload.mode,
    weight: Number(payload.weight),
    ownerShare: clone(payload.share),
    ownerConsent: consentProjection(slice, ownerPrincipalId),
  };
}

export function projectCompanionForUi(companion, codeOverride = null, context = {}) {
  const ownerPrincipalId = context.ownerPrincipalId || context.fixtureOwnerId;
  const activePassportId = context.ownerPassportId || context.fixturePassportId;
  const partnerId = (companion.participant_ids || []).find((item) => item !== ownerPrincipalId);
  const requested = companion.requested_weights || {};
  const ownerWeight = Number(requested[ownerPrincipalId] || 1);
  const partnerWeight = Number(requested[partnerId] || 1);
  const total = Math.max(0.000001, ownerWeight + partnerWeight);
  const [namePart, codePart] = String(companion.name || "Companion · RESTORED").split(" · ");
  const sourcePassportIds = companion.source_passport_ids || [];
  const ownerPassportId = sourcePassportIds.includes(activePassportId)
    ? activePassportId
    : sourcePassportIds[0];
  const partnerPassportId = sourcePassportIds.find((item) => item !== ownerPassportId);
  const participantSelected = companion.participant_selected_fields || {};
  return {
    id: companion.id,
    code: codeOverride || codePart || "RESTORED",
    status: String(companion.status || "active").replace(/^./, (item) => item.toUpperCase()),
    mode: companionModeLabel(companion.strategy) || namePart,
    weight: Math.round((partnerWeight / total) * 100),
    effectiveWeight: effectivePartnerWeight(companion.strategy, ownerWeight, partnerWeight),
    duration: durationLabel(companion.created_at, companion.expires_at),
    expiresAt: companion.expires_at,
    scope: companion.scope || "snapshot",
    refreshOnRevision: Boolean(companion.refresh_on_revision),
    syncRevision: Number(companion.sync_revision || 0),
    lastSyncedAt: companion.last_synced_at || null,
    ownerPrincipalId,
    partnerPrincipalId: partnerId,
    ownerPassportId,
    partnerPassportId,
    ownerSelectedFields: clone(participantSelected[ownerPassportId] || {}),
    partnerSelectedFields: clone(participantSelected[partnerPassportId] || {}),
    consentIds: clone(companion.consent_ids || []),
  };
}

export function overlayMode(value) {
  return String(value).toLowerCase().includes("reversible") ? "reversible_live" : "isolated";
}

export function topicAdjustmentsForPurpose(value) {
  const purpose = String(value || "").toLowerCase();
  const adjustments = {};
  if (/research|paper|conference|speaker|agent/.test(purpose)) adjustments.research = 0.3;
  if (/design|interface|product/.test(purpose)) adjustments.design = 0.2;
  if (/local|event|city|nearby/.test(purpose)) adjustments.local = 0.25;
  if (/game|indie|studio/.test(purpose)) adjustments.indie = 0.25;
  return Object.keys(adjustments).length ? adjustments : { research: 0.15 };
}

export function companionStrategy(value) {
  if (value === "Common Ground") return "common_ground";
  if (value === "Taste Swap") return "taste_swap";
  if (value === "Weighted Mix") return "weighted";
  return "bridge";
}

export function deterministicPartnerId(code) {
  let hash = 2166136261;
  for (const character of String(code)) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return `demo-partner-${(hash >>> 0).toString(16)}`;
}

export function deterministicPairId(firstOwnerId, secondOwnerId, code) {
  const participants = [String(firstOwnerId), String(secondOwnerId)].sort();
  let hash = 2166136261;
  for (const character of `${participants.join("|")}|${String(code)}`) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return `pair-${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

export function projectDriftForUi(raw, passport = null) {
  const evaluation = raw.evaluation || {};
  const topicFit = Math.round((1 - Number(evaluation.total_variation_distance || 0)) * 100);
  const sourceDiversity = Math.round((1 - Number(evaluation.source_concentration || 0)) * 100);
  const outrage = Math.round(Number(evaluation.unwanted_rate || 0) * 100);
  const serendipity = Math.round(Number(evaluation.serendipity_rate || 0) * 100);
  const repetition = Math.round(Number(evaluation.source_concentration || 0) * 100);
  const targetSerendipity = Math.round(Number(passport?.serendipity || 0.2) * 100);
  const outrageCeiling = Math.round(Number(passport?.max_outrage || 0.05) * 100);
  const sourceFloor = Math.round((1 - Number(passport?.max_source_share || 0.4)) * 100);
  const correctable = raw.status === "decision_required" && Boolean(raw.proposed_plan?.actions?.length);
  return {
    score: topicFit,
    status: raw.status || "observed",
    correctable,
    checkedAt: formatDateLabel(raw.observed_at),
    signals: [
      { label: "Topic fit", value: topicFit, target: "at least 80", state: topicFit >= 80 ? "on-course" : "attention" },
      { label: "Source diversity", value: sourceDiversity, target: `at least ${sourceFloor}`, state: sourceDiversity >= sourceFloor ? "on-course" : "attention" },
      { label: "Creator repetition", value: repetition, target: `at most ${Math.round(Number(passport?.max_source_share || 0.4) * 100)}`, state: repetition <= Number(passport?.max_source_share || 0.4) * 100 ? "on-course" : "attention" },
      { label: "Outrage indicators", value: outrage, target: `at most ${outrageCeiling}`, state: outrage <= outrageCeiling ? "on-course" : "attention" },
      { label: "Serendipity", value: serendipity, target: `exactly ${targetSerendipity}`, state: Math.abs(serendipity - targetSerendipity) <= 3 ? "on-course" : "attention" },
    ],
    recommendation: (evaluation.recommendations || []).join(" ") || "The observed Lab account is aligned with the active Passport.",
  };
}
