export const INSTAGRAM_IMPORT_SELECTION_LIMIT = 500;

const HANDLE_PATTERN = /^[a-z0-9_](?:[a-z0-9._]{0,28}[a-z0-9_])?$/;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;

const STATUS_PRESENTATION = Object.freeze({
  empty: Object.freeze({ label: "LOCAL INTAKE", tone: "purple" }),
  ready: Object.freeze({ label: "READY", tone: "blue" }),
  applying: Object.freeze({ label: "OUTCOME PENDING", tone: "orange" }),
  expired: Object.freeze({ label: "EXPIRED", tone: "orange" }),
  consumed: Object.freeze({ label: "CONSUMED", tone: "green" }),
  discarded: Object.freeze({ label: "DISCARDED", tone: "purple" }),
  invalid: Object.freeze({ label: "UNAVAILABLE", tone: "orange" }),
});

const WARNING_COPY = Object.freeze({
  ignored_archive_entries: ["unrelated archive entry was ignored", "unrelated archive entries were ignored"],
  ignored_top_level_fields: ["unrecognized top-level field was ignored", "unrecognized top-level fields were ignored"],
  rejected_relationships: ["malformed following record was rejected", "malformed following records were rejected"],
  duplicate_relationships: ["duplicate following record was merged", "duplicate following records were merged"],
  missing_relationship_timestamps: ["accepted record had no relationship timestamp", "accepted records had no relationship timestamp"],
});

const FIELD_COPY = Object.freeze({
  followed_creators: "followed creators",
  topic_distribution: "topic distribution",
  feed_items: "feed items",
  muted_creators: "muted creators",
  hidden_words: "Hidden Words",
  format_preferences: "format preferences",
  languages: "languages",
  serendipity: "serendipity",
  outrage: "outrage limits",
  source_concentration: "source concentration",
  recommendation_state: "recommendation state",
});

function exactString(value) {
  return typeof value === "string" ? value : "";
}

function exactStringList(value) {
  return Array.isArray(value) && value.every((entry) => typeof entry === "string")
    ? [...value]
    : null;
}

function exactNonNegativeInteger(value) {
  return Number.isSafeInteger(value) && value >= 0 ? value : null;
}

function exactReadyHandles(value) {
  const handles = exactStringList(value);
  if (!handles) return null;
  if (new Set(handles).size !== handles.length) return null;
  if (handles.some((handle) => (
    handle !== handle.toLocaleLowerCase("en-US")
    || handle.includes("..")
    || !HANDLE_PATTERN.test(handle)
  ))) return null;
  return handles;
}

function basePresentation(phase) {
  return {
    phase,
    ...STATUS_PRESENTATION[phase],
    sessionId: "",
    sourceSha256: "",
    parserId: "",
    followedHandles: [],
    warnings: [],
    observedFields: [],
    unobservedFields: [],
    sourceRelationshipCount: 0,
    acceptedRelationshipCount: 0,
    duplicateRelationshipCount: 0,
    rejectedRelationshipCount: 0,
    ignoredArchiveEntryCount: 0,
    selectedRelationshipCount: 0,
    createdAt: "",
    expiresAt: "",
    selectionLimit: INSTAGRAM_IMPORT_SELECTION_LIMIT,
    hasCapacityHint: false,
  };
}

function invalidPresentation() {
  return basePresentation("invalid");
}

export function instagramImportExpiryDelay(expiresAt, nowMs = Date.now()) {
  const expiry = Date.parse(exactString(expiresAt));
  if (!Number.isFinite(expiry) || typeof nowMs !== "number" || !Number.isFinite(nowMs)) {
    return null;
  }
  return Math.max(0, expiry - nowMs);
}

export function scheduleInstagramImportExpiry({
  expiresAt,
  expire,
  now = Date.now,
  schedule = globalThis.setTimeout,
  cancel = globalThis.clearTimeout,
}) {
  if (
    typeof expire !== "function"
    || typeof now !== "function"
    || typeof schedule !== "function"
    || typeof cancel !== "function"
  ) {
    throw new TypeError("Instagram import expiry requires callable timer dependencies");
  }
  const delay = instagramImportExpiryDelay(expiresAt, now());
  if (delay == null) return () => {};
  if (delay === 0) {
    expire();
    return () => {};
  }
  const timerId = schedule(expire, delay);
  return () => cancel(timerId);
}

export function expiredInstagramImportSession(session) {
  return {
    status: "expired",
    expires_at: exactString(session?.expires_at),
  };
}

export function applyingInstagramImportSession(session, selectedRelationshipCount) {
  return {
    status: "applying",
    source_sha256: exactString(session?.source_sha256),
    selected_relationship_count: exactNonNegativeInteger(selectedRelationshipCount) ?? 0,
  };
}

export function instagramImportPresentation(session, nowMs = Date.now()) {
  if (session == null) return basePresentation("empty");
  if (typeof session !== "object" || Array.isArray(session)) return invalidPresentation();

  const status = exactString(session.status);
  if (status === "expired") {
    const expiresAt = exactString(session.expires_at);
    if (!expiresAt || !Number.isFinite(Date.parse(expiresAt))) return invalidPresentation();
    return { ...basePresentation("expired"), expiresAt };
  }
  const sourceSha256 = exactString(session.source_sha256);
  if (!SHA256_PATTERN.test(sourceSha256)) return invalidPresentation();

  if (status === "applying") {
    const selectedRelationshipCount = exactNonNegativeInteger(session.selected_relationship_count);
    if (selectedRelationshipCount == null || selectedRelationshipCount === 0) return invalidPresentation();
    return {
      ...basePresentation("applying"),
      sourceSha256,
      selectedRelationshipCount,
    };
  }

  if (status === "ready") {
    const sessionId = exactString(session.session_id);
    const parserId = exactString(session.parser_id);
    const followedHandles = exactReadyHandles(session.followed_handles);
    const warnings = exactStringList(session.warnings);
    const observedFields = exactStringList(session.observed_fields);
    const unobservedFields = exactStringList(session.unobserved_fields);
    const sourceRelationshipCount = exactNonNegativeInteger(session.source_relationship_count);
    const acceptedRelationshipCount = exactNonNegativeInteger(session.accepted_relationship_count);
    const duplicateRelationshipCount = exactNonNegativeInteger(session.duplicate_relationship_count);
    const rejectedRelationshipCount = exactNonNegativeInteger(session.rejected_relationship_count);
    const ignoredArchiveEntryCount = exactNonNegativeInteger(session.ignored_archive_entry_count);
    const createdAt = exactString(session.created_at);
    const expiresAt = exactString(session.expires_at);
    const hasCapacityHint = Object.hasOwn(session, "selection_limit");
    const selectionLimit = hasCapacityHint
      ? exactNonNegativeInteger(session.selection_limit)
      : INSTAGRAM_IMPORT_SELECTION_LIMIT;
    if (
      !sessionId
      || !parserId
      || !followedHandles
      || !warnings
      || !observedFields
      || !unobservedFields
      || sourceRelationshipCount == null
      || acceptedRelationshipCount == null
      || duplicateRelationshipCount == null
      || rejectedRelationshipCount == null
      || ignoredArchiveEntryCount == null
      || acceptedRelationshipCount !== followedHandles.length
      || sourceRelationshipCount !== acceptedRelationshipCount + duplicateRelationshipCount + rejectedRelationshipCount
      || !createdAt
      || !expiresAt
      || !Number.isFinite(Date.parse(createdAt))
      || instagramImportExpiryDelay(expiresAt, nowMs) == null
      || selectionLimit == null
      || selectionLimit > INSTAGRAM_IMPORT_SELECTION_LIMIT
    ) return invalidPresentation();

    if (instagramImportExpiryDelay(expiresAt, nowMs) === 0) {
      return { ...basePresentation("expired"), expiresAt };
    }

    return {
      ...basePresentation("ready"),
      sessionId,
      sourceSha256,
      parserId,
      followedHandles,
      warnings,
      observedFields,
      unobservedFields,
      sourceRelationshipCount,
      acceptedRelationshipCount,
      duplicateRelationshipCount,
      rejectedRelationshipCount,
      ignoredArchiveEntryCount,
      createdAt,
      expiresAt,
      selectionLimit,
      hasCapacityHint,
    };
  }

  if (status === "consumed" || status === "discarded") {
    const sourceRelationshipCount = exactNonNegativeInteger(session.source_relationship_count);
    const acceptedRelationshipCount = exactNonNegativeInteger(session.accepted_relationship_count);
    const selectedRelationshipCount = exactNonNegativeInteger(session.selected_relationship_count);
    const duplicateRelationshipCount = exactNonNegativeInteger(session.duplicate_relationship_count);
    const rejectedRelationshipCount = exactNonNegativeInteger(session.rejected_relationship_count);
    if (
      sourceRelationshipCount == null
      || acceptedRelationshipCount == null
      || selectedRelationshipCount == null
      || duplicateRelationshipCount == null
      || rejectedRelationshipCount == null
      || sourceRelationshipCount !== acceptedRelationshipCount + duplicateRelationshipCount + rejectedRelationshipCount
      || (status === "discarded" && selectedRelationshipCount !== 0)
    ) return invalidPresentation();

    return {
      ...basePresentation(status),
      sourceSha256,
      sourceRelationshipCount,
      acceptedRelationshipCount,
      selectedRelationshipCount,
      duplicateRelationshipCount,
      rejectedRelationshipCount,
    };
  }

  return invalidPresentation();
}

export function shortInstagramDigest(value) {
  return SHA256_PATTERN.test(String(value || ""))
    ? `${String(value).slice(0, 12)}…${String(value).slice(-8)}`
    : "Unavailable";
}

export function formatInstagramImportWarning(value) {
  const match = /^([a-z_]+):([1-9][0-9]*)$/.exec(String(value || ""));
  if (!match) return "The parser reported an unrecognized warning.";
  const count = Number(match[2]);
  const copy = WARNING_COPY[match[1]];
  if (!copy) return `The parser reported ${count} ${match[1].replaceAll("_", " ")}.`;
  return `${count} ${copy[count === 1 ? 0 : 1]}.`;
}

export function formatInstagramImportField(value) {
  const key = String(value || "");
  return FIELD_COPY[key] || key.replaceAll("_", " ");
}
