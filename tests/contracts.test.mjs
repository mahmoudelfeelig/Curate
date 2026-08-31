import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const contractsRoot = path.join(repoRoot, "contracts");
const draft202012 = "https://json-schema.org/draft/2020-12/schema";
const schemaCache = new Map();

const expectedSchemas = [
  "common.schema.json",
  "feed-passport.schema.json",
  "passport-overlay.schema.json",
  "shareable-passport-slice.schema.json",
  "companion-blend.schema.json",
  "platform-capability-manifest.schema.json",
  "proposed-action.schema.json",
  "action-envelope.schema.json",
  "account-observation.schema.json",
  "feed-sample.schema.json",
  "action-receipt.schema.json",
  "rollback-receipt.schema.json",
  "translation-loss-report.schema.json",
  "feed-evaluation.schema.json",
  "event-envelope.schema.json",
];

async function loadJson(file) {
  return JSON.parse(await readFile(file, "utf8"));
}

async function loadSchema(file) {
  const resolved = path.resolve(file);
  if (!schemaCache.has(resolved)) {
    schemaCache.set(resolved, await loadJson(resolved));
  }
  return schemaCache.get(resolved);
}

function pointerValue(document, fragment) {
  if (!fragment || fragment === "/") return document;
  assert.ok(fragment.startsWith("/"), `Unsupported JSON pointer #${fragment}`);
  return fragment
    .slice(1)
    .split("/")
    .map((part) => part.replaceAll("~1", "/").replaceAll("~0", "~"))
    .reduce((value, key) => {
      assert.ok(value && Object.hasOwn(value, key), `Unresolved JSON pointer #${fragment}`);
      return value[key];
    }, document);
}

async function resolveRef(ref, context) {
  const [filePart, fragment = ""] = ref.split("#", 2);
  const schemaFile = filePart
    ? path.resolve(path.dirname(context.schemaFile), filePart)
    : context.schemaFile;
  const rootSchema = await loadSchema(schemaFile);
  return {
    schema: pointerValue(rootSchema, fragment),
    context: { schemaFile, rootSchema },
  };
}

function sameJson(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function matchesType(type, value) {
  if (type === "null") return value === null;
  if (type === "array") return Array.isArray(value);
  if (type === "object") return value !== null && typeof value === "object" && !Array.isArray(value);
  if (type === "integer") return Number.isInteger(value);
  if (type === "number") return typeof value === "number" && Number.isFinite(value);
  return typeof value === type;
}

function validDateTime(value) {
  return (
    typeof value === "string"
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    && Number.isFinite(Date.parse(value))
  );
}

function validUri(value) {
  try {
    const parsed = new URL(value);
    return Boolean(parsed.protocol);
  } catch {
    return false;
  }
}

async function validate(schema, value, context, instancePath = "$") {
  if (schema === true) return [];
  if (schema === false) return [`${instancePath}: false schema`];

  const errors = [];
  if (schema.$ref) {
    const resolved = await resolveRef(schema.$ref, context);
    errors.push(...await validate(resolved.schema, value, resolved.context, instancePath));
  }

  if (schema.allOf) {
    for (const branch of schema.allOf) {
      errors.push(...await validate(branch, value, context, instancePath));
    }
  }

  if (schema.anyOf) {
    const results = await Promise.all(
      schema.anyOf.map((branch) => validate(branch, value, context, instancePath)),
    );
    if (!results.some((result) => result.length === 0)) {
      errors.push(`${instancePath}: no anyOf branch matched`);
    }
  }

  if (schema.oneOf) {
    const results = await Promise.all(
      schema.oneOf.map((branch) => validate(branch, value, context, instancePath)),
    );
    if (results.filter((result) => result.length === 0).length !== 1) {
      errors.push(`${instancePath}: expected exactly one matching oneOf branch`);
    }
  }

  if (schema.not) {
    const result = await validate(schema.not, value, context, instancePath);
    if (result.length === 0) errors.push(`${instancePath}: matched forbidden schema`);
  }

  if (schema.if) {
    const condition = await validate(schema.if, value, context, instancePath);
    if (condition.length === 0 && schema.then) {
      errors.push(...await validate(schema.then, value, context, instancePath));
    } else if (condition.length > 0 && schema.else) {
      errors.push(...await validate(schema.else, value, context, instancePath));
    }
  }

  if (schema.const !== undefined && !sameJson(value, schema.const)) {
    errors.push(`${instancePath}: expected const ${JSON.stringify(schema.const)}`);
  }
  if (schema.enum && !schema.enum.some((candidate) => sameJson(value, candidate))) {
    errors.push(`${instancePath}: value is outside enum`);
  }

  if (schema.type) {
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    if (!types.some((type) => matchesType(type, value))) {
      errors.push(`${instancePath}: expected type ${types.join("|")}`);
      return errors;
    }
  }

  if (typeof value === "string") {
    if (schema.minLength !== undefined && value.length < schema.minLength) {
      errors.push(`${instancePath}: shorter than minLength`);
    }
    if (schema.maxLength !== undefined && value.length > schema.maxLength) {
      errors.push(`${instancePath}: longer than maxLength`);
    }
    if (schema.pattern && !(new RegExp(schema.pattern, "u")).test(value)) {
      errors.push(`${instancePath}: does not match pattern`);
    }
    if (schema.format === "date-time" && !validDateTime(value)) {
      errors.push(`${instancePath}: invalid date-time`);
    }
    if (schema.format === "uri" && !validUri(value)) {
      errors.push(`${instancePath}: invalid URI`);
    }
  }

  if (typeof value === "number" && Number.isFinite(value)) {
    if (schema.minimum !== undefined && value < schema.minimum) {
      errors.push(`${instancePath}: below minimum`);
    }
    if (schema.maximum !== undefined && value > schema.maximum) {
      errors.push(`${instancePath}: above maximum`);
    }
    if (schema.exclusiveMinimum !== undefined && value <= schema.exclusiveMinimum) {
      errors.push(`${instancePath}: not above exclusiveMinimum`);
    }
    if (schema.exclusiveMaximum !== undefined && value >= schema.exclusiveMaximum) {
      errors.push(`${instancePath}: not below exclusiveMaximum`);
    }
    if (schema.multipleOf !== undefined) {
      const quotient = value / schema.multipleOf;
      if (Math.abs(quotient - Math.round(quotient)) > 1e-9) {
        errors.push(`${instancePath}: not a multipleOf ${schema.multipleOf}`);
      }
    }
  }

  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) {
      errors.push(`${instancePath}: fewer than minItems`);
    }
    if (schema.maxItems !== undefined && value.length > schema.maxItems) {
      errors.push(`${instancePath}: more than maxItems`);
    }
    if (schema.uniqueItems) {
      const encoded = value.map((item) => JSON.stringify(item));
      if (new Set(encoded).size !== encoded.length) {
        errors.push(`${instancePath}: items are not unique`);
      }
    }
    if (schema.items) {
      for (const [index, item] of value.entries()) {
        errors.push(...await validate(schema.items, item, context, `${instancePath}[${index}]`));
      }
    }
  }

  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    const keys = Object.keys(value);
    if (schema.minProperties !== undefined && keys.length < schema.minProperties) {
      errors.push(`${instancePath}: fewer than minProperties`);
    }
    if (schema.maxProperties !== undefined && keys.length > schema.maxProperties) {
      errors.push(`${instancePath}: more than maxProperties`);
    }
    for (const required of schema.required ?? []) {
      if (!Object.hasOwn(value, required)) {
        errors.push(`${instancePath}: missing required property ${required}`);
      }
    }
    for (const [key, propertySchema] of Object.entries(schema.properties ?? {})) {
      if (Object.hasOwn(value, key)) {
        errors.push(...await validate(propertySchema, value[key], context, `${instancePath}.${key}`));
      }
    }
    if (schema.propertyNames) {
      for (const key of keys) {
        errors.push(...await validate(schema.propertyNames, key, context, `${instancePath}.{${key}}`));
      }
    }
    const known = new Set(Object.keys(schema.properties ?? {}));
    for (const key of keys.filter((candidate) => !known.has(candidate))) {
      if (schema.additionalProperties === false) {
        errors.push(`${instancePath}: unexpected property ${key}`);
      } else if (schema.additionalProperties && typeof schema.additionalProperties === "object") {
        errors.push(...await validate(
          schema.additionalProperties,
          value[key],
          context,
          `${instancePath}.${key}`,
        ));
      }
    }
    for (const [key, dependencies] of Object.entries(schema.dependentRequired ?? {})) {
      if (Object.hasOwn(value, key)) {
        for (const required of dependencies) {
          if (!Object.hasOwn(value, required)) {
            errors.push(`${instancePath}: ${key} requires ${required}`);
          }
        }
      }
    }
  }

  return errors;
}

function sum(items, field) {
  return items.reduce((total, item) => total + item[field], 0);
}

function uniqueBy(items, field) {
  return new Set(items.map((item) => item[field])).size === items.length;
}

function dateOrder(errors, earlier, later, label) {
  if (Date.parse(earlier) >= Date.parse(later)) errors.push(`${label}: timestamps are not increasing`);
}

function distributionErrors(items, field, label) {
  const errors = [];
  if (Math.abs(sum(items, field) - 100) > 1e-9) errors.push(`${label}: composition must total 100`);
  if (!uniqueBy(items, "topic_id")) errors.push(`${label}: topic IDs must be unique`);
  return errors;
}

function invariantErrors(schemaName, value) {
  const errors = [];
  if (schemaName === "feed-passport.schema.json") {
    errors.push(...distributionErrors(value.topics, "target_percent", "passport topics"));
    const reserved = new Set(["serendipity", "outrage"]);
    if (value.topics.some((topic) => reserved.has(topic.topic_id))) {
      errors.push("passport topics: serendipity and outrage are cross-cutting constraints");
    }
    if (value.formats && !uniqueBy(value.formats, "format")) errors.push("passport formats must be unique");
    if (value.hard_exclusions && !uniqueBy(value.hard_exclusions, "exclusion_id")) {
      errors.push("passport exclusions must be unique");
    }
    if (value.creator_preferences && !uniqueBy(value.creator_preferences, "creator_ref")) {
      errors.push("passport creator preferences must be unique");
    }
    dateOrder(errors, value.created_at, value.updated_at, "passport lifecycle");
  }
  if (schemaName === "passport-overlay.schema.json") {
    if (Date.parse(value.created_at) > Date.parse(value.starts_at)) {
      errors.push("overlay lifetime: creation follows activation");
    }
    dateOrder(errors, value.starts_at, value.expires_at, "overlay lifetime");
    if (value.topic_targets) {
      errors.push(...distributionErrors(value.topic_targets, "target_percent", "overlay topics"));
    }
  }
  if (schemaName === "shareable-passport-slice.schema.json") {
    dateOrder(errors, value.created_at, value.expires_at, "slice lifetime");
    if (value.included_topics && !uniqueBy(value.included_topics, "topic_id")) {
      errors.push("slice topics: topic IDs must be unique");
    }
  }
  if (schemaName === "companion-blend.schema.json") {
    if (Date.parse(value.created_at) > Date.parse(value.starts_at)) {
      errors.push("blend lifetime: creation follows activation");
    }
    dateOrder(errors, value.starts_at, value.expires_at, "blend lifetime");
    if (Math.abs(sum(value.participants, "weight_percent") - 100) > 1e-9) {
      errors.push("blend weights must total 100");
    }
    if (!uniqueBy(value.participants, "slice_id") || !uniqueBy(value.participants, "owner_ref")) {
      errors.push("blend participants must reference unique slices and owners");
    }
  }
  if (schemaName === "platform-capability-manifest.schema.json") {
    if (new Set(value.allowed_action_kinds).size !== value.allowed_action_kinds.length) {
      errors.push("capability action kinds must be unique");
    }
  }
  if (schemaName === "action-envelope.schema.json") {
    dateOrder(errors, value.requested_at, value.expires_at, "action envelope lifetime");
    if (value.actions.length > value.max_total_actions) errors.push("action envelope exceeds total budget");
    const allowed = new Set(value.allowed_action_kinds);
    const actionIds = new Set();
    const counts = new Map();
    if (!uniqueBy(value.per_kind_limits, "action_kind")) {
      errors.push("action envelope per-kind limits must be unique");
    }
    for (const action of value.actions) {
      if (!allowed.has(action.action_kind)) errors.push("action envelope includes a disallowed action kind");
      if (action.platform !== value.destination.platform || action.account_ref !== value.destination.account_ref) {
        errors.push("action envelope includes an action for a different destination");
      }
      if (actionIds.has(action.action_id)) errors.push("action envelope action IDs must be unique");
      actionIds.add(action.action_id);
      counts.set(action.action_kind, (counts.get(action.action_kind) ?? 0) + 1);
    }
    for (const limit of value.per_kind_limits) {
      if (!allowed.has(limit.action_kind)) {
        errors.push("action envelope includes a limit for a disallowed action kind");
      }
      if ((counts.get(limit.action_kind) ?? 0) > limit.max_actions) {
        errors.push(`action envelope exceeds ${limit.action_kind} budget`);
      }
    }
    if (value.approval && value.approval.approved_action_count !== value.actions.length) {
      errors.push("action envelope approval count does not match actions");
    }
  }
  if (schemaName === "account-observation.schema.json") {
    errors.push(...distributionErrors(value.topic_distribution, "observed_percent", "observation topics"));
  }
  if (schemaName === "feed-sample.schema.json") {
    errors.push(...distributionErrors(value.topic_distribution, "observed_percent", "sample topics"));
    if (value.item_count !== value.items.length) errors.push("feed sample item count does not match items");
    const ranks = value.items.map((item) => item.rank).sort((a, b) => a - b);
    if (!ranks.every((rank, index) => rank === index + 1)) errors.push("feed sample ranks must be contiguous");
  }
  if (schemaName === "action-receipt.schema.json") {
    dateOrder(errors, value.started_at, value.completed_at, "action receipt lifecycle");
  }
  if (schemaName === "rollback-receipt.schema.json") {
    dateOrder(errors, value.started_at, value.completed_at, "rollback lifecycle");
    if (value.summary.requested !== value.results.length) errors.push("rollback requested count mismatch");
    const restored = value.results.filter((result) => result.outcome === "restored").length;
    const notReversible = value.results.filter((result) => result.outcome === "not_reversible").length;
    const alreadyRestored = value.results.filter((result) => result.outcome === "already_restored").length;
    const failed = value.results.filter((result) => result.outcome === "failed").length;
    if (
      value.summary.restored !== restored
      || value.summary.not_reversible !== notReversible
      || value.summary.already_restored !== alreadyRestored
      || value.summary.failed !== failed
    ) {
      errors.push("rollback summary does not match results");
    }
  }
  if (schemaName === "translation-loss-report.schema.json") {
    const informational = value.losses.filter((loss) => loss.severity === "informational").length;
    const partial = value.losses.filter((loss) => loss.severity === "partial").length;
    const blocking = value.losses.filter((loss) => loss.severity === "blocking").length;
    if (
      value.summary.total_losses !== value.losses.length
      || value.summary.informational !== informational
      || value.summary.partial !== partial
      || value.summary.blocking !== blocking
    ) {
      errors.push("translation loss summary does not match entries");
    }
  }
  if (schemaName === "feed-evaluation.schema.json") {
    errors.push(...distributionErrors(value.target_topics, "target_percent", "evaluation target"));
    errors.push(...distributionErrors(value.observed_topics, "observed_percent", "evaluation observed"));
    if ((value.decision === "success") !== value.passed) {
      errors.push("evaluation success decision and passed flag disagree");
    }
    if (value.metrics && value.thresholds) {
      const thresholdsSatisfied = (
        value.metrics.topic_distance <= value.thresholds.maximum_topic_distance
        && value.metrics.hard_exclusion_percent <= value.thresholds.maximum_hard_exclusion_percent
        && value.metrics.outrage_percent <= value.thresholds.maximum_outrage_percent
        && value.metrics.source_diversity >= value.thresholds.minimum_source_diversity
      );
      if (value.passed !== thresholdsSatisfied) {
        errors.push("evaluation passed flag disagrees with deterministic thresholds");
      }
    }
  }
  if (schemaName === "event-envelope.schema.json") {
    const forbidden = /token|secret|password|credential|cookie|oauth|raw[_-]?(?:feed|content|history)/i;
    if (Object.keys(value.payload.fields).some((key) => forbidden.test(key))) {
      errors.push("event public payload contains a forbidden key");
    }
  }
  return errors;
}

async function collectRefs(value, context, seen = new Set()) {
  if (!value || typeof value !== "object") return;
  if (typeof value.$ref === "string") {
    const identity = `${context.schemaFile}::${value.$ref}`;
    if (!seen.has(identity)) {
      seen.add(identity);
      const resolved = await resolveRef(value.$ref, context);
      await collectRefs(resolved.schema, resolved.context, seen);
    }
  }
  for (const nested of Object.values(value)) {
    await collectRefs(nested, context, seen);
  }
}

test("all public contracts are strict Draft 2020-12 schemas with resolvable references", async () => {
  const manifest = await loadJson(path.join(contractsRoot, "fixtures", "manifest.json"));
  assert.deepEqual(manifest.schemas, expectedSchemas.filter((name) => name !== "common.schema.json"));

  for (const schemaName of expectedSchemas) {
    const schemaFile = path.join(contractsRoot, schemaName);
    const schema = await loadSchema(schemaFile);
    assert.equal(schema.$schema, draft202012, schemaName);
    assert.match(schema.$id, /^https:\/\/feedpassport\.dev\/contracts\//, schemaName);
    assert.equal(schema.type, "object", schemaName);
    assert.equal(schema.additionalProperties, false, schemaName);
    await collectRefs(schema, { schemaFile, rootSchema: schema });
  }
});

test("valid fixtures satisfy their schemas and cross-document invariants", async () => {
  const manifest = await loadJson(path.join(contractsRoot, "fixtures", "manifest.json"));
  for (const fixture of manifest.valid) {
    const schemaFile = path.join(contractsRoot, fixture.schema);
    const schema = await loadSchema(schemaFile);
    const value = await loadJson(path.join(contractsRoot, "fixtures", fixture.instance));
    const errors = [
      ...await validate(schema, value, { schemaFile, rootSchema: schema }),
      ...invariantErrors(fixture.schema, value),
    ];
    assert.deepEqual(errors, [], `${fixture.instance}:\n${errors.join("\n")}`);
  }
});

test("invalid fixtures are rejected for their intended contract violation", async () => {
  const manifest = await loadJson(path.join(contractsRoot, "fixtures", "manifest.json"));
  for (const fixture of manifest.invalid) {
    const schemaFile = path.join(contractsRoot, fixture.schema);
    const schema = await loadSchema(schemaFile);
    const value = await loadJson(path.join(contractsRoot, "fixtures", fixture.instance));
    const errors = [
      ...await validate(schema, value, { schemaFile, rootSchema: schema }),
      ...invariantErrors(fixture.schema, value),
    ];
    assert.ok(errors.length > 0, `${fixture.instance} unexpectedly passed`);
    assert.ok(
      errors.some((error) => error.includes(fixture.expected_error)),
      `${fixture.instance}: expected ${fixture.expected_error}; got\n${errors.join("\n")}`,
    );
  }
});

test("the shared action vocabulary cannot automate public engagement", async () => {
  const common = await loadSchema(path.join(contractsRoot, "common.schema.json"));
  const actions = common.$defs.ActionKind.enum;
  for (const forbidden of ["like", "comment", "post", "repost", "direct_message", "message"]) {
    assert.equal(actions.includes(forbidden), false, forbidden);
  }
  assert.equal(common.$defs.TrustBoundary.properties.credentials_in_model_context.const, false);
  assert.equal(common.$defs.TrustBoundary.properties.public_engagement_automation.const, false);
  assert.equal(common.$defs.TrustBoundary.properties.raw_private_history_transfer.const, false);
});
