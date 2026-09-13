const TARGET_NAME = "curator-runtime";
const REQUIRED_SCOPE = "feed-passport/invoke";
const DEFAULT_TIMEOUT_MS = 90_000;

export class AgentCoreGatewayError extends Error {
  constructor(status, message, payload = null) {
    super(message);
    this.name = "AgentCoreGatewayError";
    this.status = status;
    this.payload = payload;
  }
}

function gatewayUrl(value) {
  let url;
  try {
    url = new URL(String(value));
  } catch {
    throw new Error("Curate AgentCore Gateway URL must be an absolute URL");
  }
  if (url.protocol !== "https:") {
    throw new Error("Curate AgentCore Gateway URL must use HTTPS");
  }
  if (
    url.username
    || url.password
    || url.search
    || url.hash
    || !/^[a-z0-9-]+\.gateway\.bedrock-agentcore\.eu-north-1\.amazonaws\.com$/i.test(url.hostname)
  ) {
    throw new Error("Curate AgentCore Gateway URL must be the eu-north-1 Gateway origin");
  }
  if (url.pathname !== "/" && url.pathname !== "") {
    throw new Error("Curate AgentCore Gateway URL cannot contain a path");
  }
  return url.origin;
}

export function agentCoreConfigFromEnv(environment = {}) {
  const value = String(environment.VITE_CURATE_AGENTCORE_GATEWAY_URL || "").trim();
  if (!value) return Object.freeze({ configured: false });
  const baseUrl = gatewayUrl(value);
  return Object.freeze({
    configured: true,
    endpoint: `${baseUrl}/${TARGET_NAME}/invocations`,
  });
}

function decodeAccessToken(token, now) {
  const value = String(token || "").trim();
  if (!value || /[\r\n]/.test(value)) {
    throw new AgentCoreGatewayError(401, "Sign in to use the Curate cloud planner");
  }
  const segments = value.split(".");
  if (segments.length !== 3 || segments.some((segment) => !/^[A-Za-z0-9_-]+$/.test(segment))) {
    throw new AgentCoreGatewayError(401, "Your Curate sign-in session is invalid");
  }
  let claims;
  try {
    const encoded = segments[1].replaceAll("-", "+").replaceAll("_", "/");
    const padded = encoded.padEnd(Math.ceil(encoded.length / 4) * 4, "=");
    const bytes = Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
    claims = JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    throw new AgentCoreGatewayError(401, "Your Curate sign-in session is invalid");
  }
  const scopes = typeof claims?.scope === "string" ? claims.scope.split(/\s+/) : [];
  const subject = typeof claims?.sub === "string" ? claims.sub.trim() : "";
  if (
    claims?.token_use !== "access"
    || !scopes.includes(REQUIRED_SCOPE)
    || !subject
    || subject.length > 160
    || subject.includes("\0")
    || !Number.isFinite(Number(claims.exp))
    || Number(claims.exp) * 1000 <= now()
  ) {
    throw new AgentCoreGatewayError(401, "Your Curate sign-in session cannot use the cloud planner");
  }
  return { token: value, subject };
}

function cleanText(value, label, maximum) {
  const text = String(value || "").trim();
  if (!text || text.length > maximum || text.includes("\0")) {
    throw new AgentCoreGatewayError(422, `${label} must contain between one and ${maximum} characters`);
  }
  return text;
}

function numberMap(value, label, { required = false } = {}) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    if (!required) return {};
    throw new AgentCoreGatewayError(422, `${label} is required`);
  }
  const entries = Object.entries(value);
  if (required && entries.length === 0) {
    throw new AgentCoreGatewayError(422, `${label} is required`);
  }
  return Object.fromEntries(entries.map(([key, raw]) => {
    const name = cleanText(key, `${label} key`, 120);
    const number = Number(raw);
    if (!Number.isFinite(number)) {
      throw new AgentCoreGatewayError(422, `${label} values must be finite numbers`);
    }
    return [name, number];
  }));
}

function stringList(value, label, maximum) {
  if (!Array.isArray(value) || value.length === 0 || value.length > maximum) {
    throw new AgentCoreGatewayError(422, `${label} must contain between one and ${maximum} values`);
  }
  const values = value.map((item) => cleanText(item, `${label} value`, 80));
  if (new Set(values).size !== values.length) {
    throw new AgentCoreGatewayError(422, `${label} cannot contain duplicate values`);
  }
  return values;
}

function passportSnapshot(passport, subject) {
  if (!passport || typeof passport !== "object" || Array.isArray(passport)) {
    throw new AgentCoreGatewayError(422, "A Curate Passport is required");
  }
  const version = Number(passport.version);
  if (!Number.isInteger(version) || version < 1) {
    throw new AgentCoreGatewayError(422, "Passport version must be a positive integer");
  }
  const bounded = (raw, fallback, { positive = false } = {}) => {
    const number = Number(raw ?? fallback);
    if (!Number.isFinite(number) || number < 0 || number > 1 || (positive && number === 0)) {
      throw new AgentCoreGatewayError(422, "Passport preference limits must be between zero and one");
    }
    return number;
  };
  const topicTargets = numberMap(passport.topic_targets, "Passport topic targets", { required: true });
  if (
    Object.values(topicTargets).some((value) => value <= 0 || value > 1)
    || Math.abs(Object.values(topicTargets).reduce((sum, value) => sum + value, 0) - 1) > 0.001
  ) {
    throw new AgentCoreGatewayError(422, "Passport topic targets must be positive and total 100%");
  }
  const creatorPreferences = numberMap(passport.creator_preferences || {}, "Passport creator preferences");
  const formatPreferences = numberMap(passport.format_preferences || {}, "Passport format preferences");
  if ([...Object.values(creatorPreferences), ...Object.values(formatPreferences)].some((value) => value < -1 || value > 1)) {
    throw new AgentCoreGatewayError(422, "Passport preference values must be between minus one and one");
  }
  return {
    id: cleanText(passport.id, "Passport ID", 160),
    owner_id: subject,
    name: cleanText(passport.name, "Passport name", 160),
    version,
    intent: cleanText(passport.intent, "Passport intent", 1200),
    topic_targets: topicTargets,
    creator_preferences: creatorPreferences,
    format_preferences: formatPreferences,
    languages: stringList(passport.languages || ["en"], "Passport languages", 12),
    hard_exclusions: Array.isArray(passport.hard_exclusions)
      ? [...new Set(passport.hard_exclusions.map((item) => cleanText(item, "Passport exclusion", 80)))]
      : [],
    serendipity: bounded(passport.serendipity, 0.2),
    max_outrage: bounded(passport.max_outrage, 0.05),
    max_source_share: bounded(passport.max_source_share, 0.25, { positive: true }),
  };
}

function evidenceList(evidence) {
  if (!Array.isArray(evidence) || evidence.length < 1 || evidence.length > 12) {
    throw new AgentCoreGatewayError(422, "Feed evidence must contain between one and 12 items");
  }
  return evidence.map((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      throw new AgentCoreGatewayError(422, "Every feed evidence item must be an object");
    }
    const platform = cleanText(item.platform, "Evidence platform", 20);
    const metadataSource = cleanText(item.metadata_source, "Evidence source", 80);
    const confidence = Number(item.confidence);
    if (!new Set(["youtube", "bluesky", "instagram"]).has(platform)) {
      throw new AgentCoreGatewayError(422, "Evidence platform is unsupported");
    }
    if (!new Set(["youtube_data_api_v3", "bluesky_public_appview", "user_selected_link_only", "unavailable_without_owner_oauth"]).has(metadataSource)) {
      throw new AgentCoreGatewayError(422, "Evidence source is unsupported");
    }
    if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
      throw new AgentCoreGatewayError(422, "Evidence confidence must be between zero and one");
    }
    const title = String(item.title || "").trim().slice(0, 300);
    const description = String(item.description || "").trim().slice(0, 1200);
    if (/(?:https?:\/\/|www\.)/i.test(title) || /(?:https?:\/\/|www\.)/i.test(description) || title.includes("\0") || description.includes("\0")) {
      throw new AgentCoreGatewayError(422, "Evidence text cannot contain links or control characters");
    }
    const inferredTopics = Array.isArray(item.inferred_topics)
      ? [...new Set(item.inferred_topics.map((topic) => cleanText(topic, "Evidence topic", 64)))].slice(0, 12)
      : [];
    if (inferredTopics.some((topic) => !/^[a-z0-9][a-z0-9_]{0,63}$/.test(topic))) {
      throw new AgentCoreGatewayError(422, "Evidence topics must use lowercase slugs");
    }
    return {
      platform,
      metadata_source: metadataSource,
      metadata_verified: Boolean(item.metadata_verified),
      title,
      description,
      inferred_topics: inferredTopics,
      ragebait_signal: Boolean(item.ragebait_signal),
      confidence,
    };
  });
}

export function createAgentCoreGatewayClient({
  environment = import.meta.env || {},
  getAccessToken,
  fetchImpl = globalThis.fetch?.bind(globalThis),
  now = () => Date.now(),
} = {}) {
  const config = agentCoreConfigFromEnv(environment);
  return Object.freeze({
    status: () => Object.freeze({ configured: config.configured }),
    async planFeed({ passport, request, evidence, timeoutMs = DEFAULT_TIMEOUT_MS }) {
      if (!config.configured) {
        throw new AgentCoreGatewayError(503, "The Curate cloud planner is not configured");
      }
      if (typeof getAccessToken !== "function") {
        throw new AgentCoreGatewayError(401, "Sign in to use the Curate cloud planner");
      }
      if (typeof fetchImpl !== "function") {
        throw new AgentCoreGatewayError(0, "This browser cannot reach the Curate cloud planner");
      }
      const identity = decodeAccessToken(await getAccessToken(), now);
      const body = {
        kind: "plan_feed",
        passport: passportSnapshot(passport, identity.subject),
        request: cleanText(request, "Feed request", 1200),
        evidence: evidenceList(evidence),
      };
      const controller = new AbortController();
      const timeout = globalThis.setTimeout(() => controller.abort(), timeoutMs);
      try {
        const response = await fetchImpl(config.endpoint, {
          method: "POST",
          credentials: "omit",
          redirect: "error",
          signal: controller.signal,
          headers: {
            Accept: "application/json",
            Authorization: `Bearer ${identity.token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify(body),
        });
        const contentType = response.headers?.get?.("content-type") || "";
        const payload = contentType.includes("application/json")
          ? await response.json()
          : await response.text();
        if (!response.ok) {
          const detail = payload && typeof payload === "object"
            ? payload.detail || payload.message || payload.error
            : "";
          throw new AgentCoreGatewayError(
            response.status,
            String(detail || `Curate cloud planning failed (${response.status})`).slice(0, 500),
            payload,
          );
        }
        if (
          !payload
          || typeof payload !== "object"
          || payload.kind !== "feed_goal_proposal"
          || !payload.proposal
          || !payload.evidence
          || payload.consent_created !== false
          || payload.approved !== false
          || payload.executed !== false
        ) {
          throw new AgentCoreGatewayError(502, "Curate cloud planning returned an unexpected response");
        }
        return payload;
      } catch (error) {
        if (error instanceof AgentCoreGatewayError) throw error;
        if (error?.name === "AbortError") {
          throw new AgentCoreGatewayError(0, "Curate cloud planning took too long; try again");
        }
        throw new AgentCoreGatewayError(0, "The Curate cloud planner could not be reached");
      } finally {
        globalThis.clearTimeout(timeout);
      }
    },
  });
}
