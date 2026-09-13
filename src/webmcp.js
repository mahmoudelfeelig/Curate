import { DESTINATIONS } from "./data.js";

export const MIGRATION_DESTINATION_IDS = Object.freeze(DESTINATIONS.map(({ id }) => id));
export const TEMPORARY_VISA_DURATIONS = Object.freeze(["6 hours", "48 hours", "7 days"]);

function normalizeMigrationDestination(value, role) {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (!MIGRATION_DESTINATION_IDS.includes(normalized)) {
    throw new TypeError(`Unsupported migration ${role}: ${value ?? "missing"}`);
  }
  return normalized;
}

export function validateMigrationPreviewInput(input = {}) {
  const source = normalizeMigrationDestination(input.source, "source");
  const destination = normalizeMigrationDestination(input.destination, "destination");
  if (source === destination) {
    throw new TypeError("Migration source and destination must be different.");
  }
  return { source, destination };
}

export function validateTemporaryVisaInput(input = {}) {
  const purpose = String(input.purpose ?? "Temporary focused feed").trim();
  const duration = String(input.duration ?? "48 hours").trim();
  if (!purpose || purpose.length > 240) {
    throw new TypeError("Temporary visa purpose must contain between one and 240 characters.");
  }
  if (!TEMPORARY_VISA_DURATIONS.includes(duration)) {
    throw new TypeError(`Unsupported temporary visa duration: ${duration || "missing"}`);
  }
  return { purpose, duration };
}

export function validateFeedEvidenceInput(input = {}) {
  const goal = String(input.goal || "").trim();
  const links = Array.isArray(input.links) ? input.links : [];
  if (!goal || goal.length > 1200) {
    throw new TypeError("A feed evidence goal must contain between one and 1200 characters.");
  }
  if (!links.length || links.length > 12) {
    throw new TypeError("Feed evidence requires between one and 12 selected links.");
  }
  const normalized = links.map((item) => {
    const url = String(item?.url || "").trim();
    const note = String(item?.note || "").trim();
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" || note.length > 600) {
      throw new TypeError("Feed evidence links must use HTTPS and notes must not exceed 600 characters.");
    }
    return { url, note };
  });
  return { goal, links: normalized };
}

function bindSourcePassport(sourcePassport, routeSource) {
  const id = String(sourcePassport?.id ?? "").trim();
  const version = Number(sourcePassport?.version);
  const source = sourcePassport?.source == null
    ? ""
    : normalizeMigrationDestination(sourcePassport.source, "source Passport platform");
  if (!id || !Number.isInteger(version) || version < 1) {
    throw new TypeError("An active source Passport id and version are required for a migration preview.");
  }
  if (!source || source !== routeSource) {
    throw new TypeError(`Requested migration source ${routeSource} is not bound to active Passport ${id}; capture that source first.`);
  }
  return { id, version, source };
}

export async function prepareWebMcpMigrationPreview({ api, input, sourcePassport }) {
  const route = validateMigrationPreviewInput(input);
  const boundPassport = bindSourcePassport(sourcePassport, route.source);
  if (!api || typeof api.previewMigration !== "function") {
    throw new TypeError("A migration preview client is required.");
  }

  const result = await api.previewMigration(route);
  const data = result?.data;
  const previewId = String(data?.previewId ?? "").trim();
  if (!previewId || !Array.isArray(data?.actions) || !Array.isArray(data?.losses)) {
    throw new TypeError("The migration preview service returned an incomplete action-and-loss manifest.");
  }

  const actions = data.actions.map((action) => ({ ...action }));
  const losses = data.losses.map((loss) => ({ ...loss }));
  const preview = {
    ...data,
    previewId,
    actions,
    losses,
    route,
    sourcePassport: boundPassport,
  };

  return {
    source: result.source,
    preview,
    response: {
      opened: "migration",
      route,
      sourcePassport: boundPassport,
      preview: { previewId, actions, losses },
      approvalRequired: true,
      consentGranted: false,
      executionPerformed: false,
      mutationPerformed: false,
    },
  };
}

const TOOL_DEFINITIONS = [
  {
    name: "feed_passport.inspect",
    description:
      "Read the active Curate intent, version, destination capabilities, and trust boundaries from this site.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    handler: "inspect",
  },
  {
    name: "feed_passport.preview_migration",
    description:
      "Prepare a non-mutating migration preview between two selected destinations, including translation loss.",
    inputSchema: {
      type: "object",
      required: ["source", "destination"],
      properties: {
        source: { type: "string", enum: MIGRATION_DESTINATION_IDS },
        destination: { type: "string", enum: MIGRATION_DESTINATION_IDS },
      },
      additionalProperties: false,
    },
    handler: "previewMigration",
    validate: validateMigrationPreviewInput,
  },
  {
    name: "feed_passport.issue_temporary_visa",
    description:
      "Open the temporary visa desk with a proposed purpose and duration. The user still approves issuance in the interface.",
    inputSchema: {
      type: "object",
      properties: {
        purpose: { type: "string" },
        duration: { type: "string", enum: TEMPORARY_VISA_DURATIONS },
      },
      additionalProperties: false,
    },
    handler: "prepareTemporaryVisa",
    validate: validateTemporaryVisaInput,
  },
  {
    name: "feed_passport.open_rollback",
    description:
      "Open the receipt and rollback desk. No rollback is executed until the user confirms it in the interface.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    handler: "openRollback",
  },
  {
    name: "feed_passport.preview_local_agent_mission",
    description:
      "Observe a seeded local platform control twin, compile a bounded mission, and open its one-time run checkpoint. This never accesses a social account or starts a run.",
    inputSchema: {
      type: "object",
      required: ["goal", "platform"],
      properties: {
        goal: { type: "string", minLength: 1, maxLength: 1200 },
        platform: { type: "string", enum: ["bluesky", "x", "youtube", "reddit", "instagram", "facebook", "threads", "tiktok", "linkedin", "snapchat"] },
        maxTotalActions: { type: "integer", minimum: 1, maximum: 20 },
        maxIterations: { type: "integer", minimum: 1, maximum: 5 },
      },
      additionalProperties: false,
    },
    handler: "previewAgentMission",
  },
  {
    name: "feed_passport.inspect_local_agent_mission",
    description:
      "Inspect the currently open local agent mission, including its trace, budgets, measurements, stop reason, and receipts. This tool cannot approve or execute it.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    handler: "inspectAgentMission",
  },
  {
    name: "feed_passport.preview_feed_evidence",
    description:
      "Inspect up to 12 owner-selected YouTube, Bluesky, or Instagram links and open a Passport-only proposal. This cannot approve or change a social account.",
    inputSchema: {
      type: "object",
      required: ["goal", "links"],
      properties: {
        goal: { type: "string", minLength: 1, maxLength: 1200 },
        links: {
          type: "array",
          minItems: 1,
          maxItems: 12,
          items: {
            type: "object",
            required: ["url"],
            properties: {
              url: { type: "string", minLength: 12, maxLength: 2048 },
              note: { type: "string", maxLength: 600 },
            },
            additionalProperties: false,
          },
        },
      },
      additionalProperties: false,
    },
    handler: "previewFeedEvidence",
    validate: validateFeedEvidenceInput,
  },
];

function findRegistry() {
  if (typeof globalThis === "undefined") return null;
  return (
    globalThis.document?.modelContext ||
    globalThis.navigator?.modelContext ||
    globalThis.navigator?.webmcp ||
    globalThis.webmcp ||
    globalThis.webMCP ||
    null
  );
}

function registerOne(registry, tool) {
  const definition = {
    name: tool.name,
    description: tool.description,
    inputSchema: tool.inputSchema,
    execute: async (input = {}) => tool.execute(tool.validate ? tool.validate(input) : input),
  };

  try {
    return registry.registerTool(definition);
  } catch {
    return registry.registerTool(tool.name, definition);
  }
}

export function registerFeedPassportTools(handlers) {
  const registry = findRegistry();
  if (!registry || typeof registry.registerTool !== "function") {
    return { supported: false, registered: 0, cleanup: () => {} };
  }

  const registrations = [];
  for (const tool of TOOL_DEFINITIONS) {
    const handler = handlers[tool.handler];
    if (typeof handler !== "function") continue;

    try {
      const registration = registerOne(registry, { ...tool, execute: handler });
      registrations.push({ name: tool.name, registration });
    } catch {
      // A draft WebMCP host may reject one tool shape. The site remains fully usable.
    }
  }

  return {
    supported: true,
    registered: registrations.length,
    cleanup() {
      registrations.forEach(({ name, registration }) => {
        try {
          if (typeof registration === "function") registration();
          else if (registration && typeof registration.unregister === "function") registration.unregister();
          else if (typeof registry.unregisterTool === "function") registry.unregisterTool(name);
        } catch {
          // A stale host registration should never break page teardown.
        }
      });
    },
  };
}
