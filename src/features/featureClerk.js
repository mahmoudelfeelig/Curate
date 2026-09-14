const FEATURE_TOOL_PREFIX = [
  "inspect_selected_passport",
  "inspect_safe_feature_catalog",
];

export const FEATURE_SUBMISSION_TOOLS = Object.freeze({
  migration: "submit_migration_proposal",
  temporary_visa: "submit_temporary_visa_proposal",
  companion_sync: "submit_companion_sync_proposal",
});

export const FEATURE_KIND_LABELS = Object.freeze({
  migration: "Migration",
  temporary_visa: "Temporary Visa",
  companion_sync: "Companion Sync",
});

const COMPANION_STRATEGY_LABELS = Object.freeze({
  bridge: "Bridge View",
  weighted: "Weighted Mix",
  common_ground: "Common Ground",
  taste_swap: "Taste Swap",
});

const UI_COMPANION_FIELDS = Object.freeze([
  "topics",
  "creators",
  "serendipity",
  "exclusions",
  "formats",
]);

export function normalizeFeatureDestination(value) {
  const destination = String(value || "").trim().toLowerCase();
  return ["feed_passport_lab", "proof_lab"].includes(destination) ? "lab" : destination;
}

export function formatExactDuration(minutesValue) {
  const minutes = Number(minutesValue);
  if (!Number.isInteger(minutes) || minutes <= 0) {
    throw new TypeError("Feature proposal duration must be a positive whole number of minutes");
  }

  let human;
  if (minutes % 1440 === 0) {
    const days = minutes / 1440;
    human = `${days} ${days === 1 ? "day" : "days"}`;
  } else if (minutes % 60 === 0) {
    const hours = minutes / 60;
    human = `${hours} ${hours === 1 ? "hour" : "hours"}`;
  } else {
    const days = Math.floor(minutes / 1440);
    const hours = Math.floor((minutes % 1440) / 60);
    const remainder = minutes % 60;
    human = [
      days ? `${days} ${days === 1 ? "day" : "days"}` : "",
      hours ? `${hours} ${hours === 1 ? "hour" : "hours"}` : "",
      remainder ? `${remainder} ${remainder === 1 ? "minute" : "minutes"}` : "",
    ].filter(Boolean).join(" ");
  }
  return `${human} · exactly ${minutes} minutes`;
}

function companionShareFromCategories(categories) {
  const selected = new Set(categories || []);
  return Object.fromEntries(UI_COMPANION_FIELDS.map((field) => [field, selected.has(field)]));
}

export function mapFeatureProposalToDesk(proposal) {
  if (!proposal || typeof proposal !== "object") {
    throw new TypeError("A typed Feature Clerk proposal is required");
  }

  if (proposal.kind === "migration") {
    return {
      section: "migration",
      values: {
        destination: normalizeFeatureDestination(proposal.destination),
        goal: proposal.goal,
        rationale: proposal.rationale,
      },
    };
  }

  if (proposal.kind === "temporary_visa") {
    const durationMinutes = Number(proposal.duration_minutes);
    return {
      section: "temporary",
      values: {
        purpose: proposal.purpose,
        duration: formatExactDuration(durationMinutes),
        durationMinutes,
        mode: proposal.mode === "reversible_live" ? "Reversible Lab" : "Isolated Lab",
        rationale: proposal.rationale,
      },
    };
  }

  if (proposal.kind === "companion_sync") {
    const durationMinutes = Number(proposal.duration_minutes);
    return {
      section: "companion",
      values: {
        share: companionShareFromCategories(proposal.field_categories),
        blend: {
          mode: COMPANION_STRATEGY_LABELS[proposal.strategy] || "Bridge View",
          weight: Number(proposal.companion_input_percent),
          duration: formatExactDuration(durationMinutes),
          durationMinutes,
        },
        rationale: proposal.rationale,
      },
    };
  }

  throw new TypeError(`Unsupported Feature Clerk proposal kind: ${String(proposal.kind)}`);
}

export function expectedFeatureTools(kind) {
  const submitTool = FEATURE_SUBMISSION_TOOLS[kind];
  return submitTool ? [...FEATURE_TOOL_PREFIX, submitTool] : [...FEATURE_TOOL_PREFIX];
}

export function resolveMigrationCapability(proposal, platforms = []) {
  if (proposal?.kind !== "migration") return null;
  const destination = normalizeFeatureDestination(proposal.destination);
  const record = platforms.find((item) => (
    !String(item?.platform || "").startsWith("twin:")
    && normalizeFeatureDestination(item?.platform) === destination
  ));
  const manifest = record?.manifest;
  const boundCapability = proposal.capability;
  const hasBoundCapability = boundCapability && typeof boundCapability === "object";
  const boundDestination = normalizeFeatureDestination(boundCapability?.destination_id);

  if (hasBoundCapability && boundDestination !== destination) {
    return {
      destination,
      found: false,
      evidenceLevel: "unavailable",
      executeMode: "unavailable",
      boundary: "No execution claim",
      conformance: "not_run",
      conformanceEnvironment: "not_run",
      source: "server_bound_mismatch",
      limitations: ["The server-bound capability does not match the selected destination."],
    };
  }

  if (!hasBoundCapability && (!manifest || typeof manifest !== "object")) {
    return {
      destination,
      found: false,
      evidenceLevel: "unavailable",
      executeMode: "unavailable",
      boundary: "No execution claim",
      conformance: "not_run",
      conformanceEnvironment: "not_run",
      source: "unavailable",
      limitations: ["The local service did not return a matching capability record."],
    };
  }

  const evidenceLevel = String(
    hasBoundCapability ? boundCapability.evidence_level : manifest.evidence_level || "unavailable",
  );
  const executeMode = String(
    hasBoundCapability ? boundCapability.execute_mode : manifest.operations?.execute || "unavailable",
  );
  let boundary = "No execution claim";
  if (evidenceLevel === "lab" && executeMode === "lab") {
    boundary = "Curate Lab practice only";
  } else if (evidenceLevel === "guided" || executeMode === "guided") {
    boundary = "Guided external handoff only";
  } else if (["executable", "closed_loop"].includes(evidenceLevel) && executeMode === "authorized") {
    boundary = "Separately authorized execution surface";
  }

  return {
    destination,
    found: true,
    evidenceLevel,
    executeMode,
    boundary,
    conformance: String(manifest?.conformance?.result || "not_run"),
    conformanceEnvironment: String(manifest?.conformance?.environment || "not_run"),
    conformanceReceipt: manifest?.conformance?.receipt_ref || null,
    source: hasBoundCapability ? "server_bound_proposal" : "legacy_platform_manifest",
    limitations: Array.isArray(hasBoundCapability ? boundCapability.limitations : manifest.limitations)
      ? [...(hasBoundCapability ? boundCapability.limitations : manifest.limitations)].slice(0, 3)
      : [],
  };
}
