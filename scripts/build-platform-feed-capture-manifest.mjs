import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SCHEMA = "curate/platform-feed-capture-manifest/v2";
const MIN_FRAMES = 6;
const MIN_REVIEWED_ITEMS = 20;
const MIN_SCROLL_SPAN = 2500;
const PLATFORM_CONFIG = {
  youtube: { origin: "https://www.youtube.com" },
  bluesky: { origin: "https://bsky.app" },
};
const CAPTURE_ORDER = [
  ["youtube", "before"],
  ["bluesky", "before"],
  ["youtube", "after"],
  ["bluesky", "after"],
];
const FRAME_NAME = /^(youtube|bluesky)-(before|after)-(\d+)\.(png|jpe?g|webp)$/i;

function assertPlainObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object.`);
  }
}

function parseIsoTimestamp(value, label) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T/.test(value)) {
    throw new Error(`${label} must be an ISO timestamp.`);
  }
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) throw new Error(`${label} must be an ISO timestamp.`);
  return timestamp;
}

function assertReviewedItems(value, label) {
  if (!Number.isSafeInteger(value) || value < MIN_REVIEWED_ITEMS) {
    throw new Error(`${label} must be an integer of at least ${MIN_REVIEWED_ITEMS}.`);
  }
}

function assertDummyAccountPairId(value, platform) {
  if (typeof value !== "string" || !new RegExp(`^dummy-${platform}-[a-z0-9][a-z0-9-]*$`).test(value)) {
    throw new Error(
      `${platform} account pair ID must be an opaque dummy ID beginning with dummy-${platform}-.`,
    );
  }
}

function assertIntervention(value, platform) {
  assertPlainObject(value, `${platform} intervention`);
  parseIsoTimestamp(value.appliedAt, `${platform} intervention appliedAt`);
  if (!["owner_attested", "receipt_bound"].includes(value.evidenceClass)) {
    throw new Error(`${platform} intervention evidenceClass must be owner_attested or receipt_bound.`);
  }
  if (typeof value.kind !== "string" || !value.kind.trim()) {
    throw new Error(`${platform} intervention kind must be nonempty.`);
  }
}

function captureKey(platform, phase) {
  return `${platform}:${phase}`;
}

function scrollPositions(frameCount) {
  const step = Math.ceil(MIN_SCROLL_SPAN / (frameCount - 1));
  return Array.from({ length: frameCount }, (_, index) => index * step);
}

async function resolveCaptureDirectory(directory) {
  if (typeof directory !== "string" || !directory.trim()) {
    throw new Error("captureDirectory must be a nonempty path.");
  }
  const absolute = path.resolve(directory);
  const real = await fs.realpath(absolute);
  const stat = await fs.stat(real);
  if (!stat.isDirectory()) throw new Error("captureDirectory must be a directory.");
  return real;
}

function resolveOutputPath(captureDirectory, outputPath) {
  const absolute = path.resolve(outputPath || path.join(captureDirectory, "platform-feed-capture-manifest.v2.json"));
  if (path.dirname(absolute) !== captureDirectory) {
    throw new Error("outputPath must be a file directly inside captureDirectory.");
  }
  if (path.extname(absolute).toLowerCase() !== ".json") {
    throw new Error("outputPath must be a JSON file.");
  }
  return absolute;
}

async function discoverFrames(captureDirectory) {
  const sequences = new Map(CAPTURE_ORDER.map(([platform, phase]) => [captureKey(platform, phase), []]));
  const entries = await fs.readdir(captureDirectory, { withFileTypes: true });
  for (const entry of entries) {
    const match = FRAME_NAME.exec(entry.name);
    if (!match) continue;
    if (!entry.isFile()) throw new Error(`${entry.name} must be a regular image file.`);
    const platform = match[1].toLowerCase();
    const phase = match[2].toLowerCase();
    const ordinal = Number.parseInt(match[3], 10);
    if (!Number.isSafeInteger(ordinal) || ordinal < 1) {
      throw new Error(`${entry.name} must use a positive frame number.`);
    }
    sequences.get(captureKey(platform, phase)).push({ file: entry.name, ordinal });
  }

  for (const [key, frames] of sequences) {
    frames.sort((left, right) => left.ordinal - right.ordinal || left.file.localeCompare(right.file));
    if (frames.length < MIN_FRAMES) throw new Error(`${key} must contain at least ${MIN_FRAMES} frame files.`);
    for (const [index, frame] of frames.entries()) {
      if (frame.ordinal !== index + 1) {
        throw new Error(`${key} frame numbers must be contiguous and begin at 1.`);
      }
      if (index > 0 && frame.ordinal === frames[index - 1].ordinal) {
        throw new Error(`${key} must not contain duplicate frame numbers.`);
      }
    }
  }
  return sequences;
}

async function buildFrames(captureDirectory, discoveredFrames) {
  const positions = scrollPositions(discoveredFrames.length);
  let previousTimestamp = Number.NEGATIVE_INFINITY;
  const frames = [];
  for (const [index, discovered] of discoveredFrames.entries()) {
    const imagePath = path.join(captureDirectory, discovered.file);
    const realImagePath = await fs.realpath(imagePath);
    if (path.dirname(realImagePath) !== captureDirectory) {
      throw new Error(`${discovered.file} must stay inside captureDirectory.`);
    }
    const [bytes, stat] = await Promise.all([fs.readFile(realImagePath), fs.stat(realImagePath)]);
    const timestamp = Math.max(Math.trunc(stat.mtimeMs), previousTimestamp + 1);
    previousTimestamp = timestamp;
    frames.push({
      captured_at: new Date(timestamp).toISOString(),
      file: discovered.file,
      sha256: createHash("sha256").update(bytes).digest("hex"),
      scroll_y: positions[index],
    });
  }
  return frames;
}

export async function buildPlatformFeedCaptureManifest({
  captureDirectory,
  outputPath,
  accountPairIds,
  reviewedUniqueFeedItems,
  interventions,
  overwrite = false,
}) {
  assertPlainObject(accountPairIds, "accountPairIds");
  assertPlainObject(reviewedUniqueFeedItems, "reviewedUniqueFeedItems");
  assertPlainObject(interventions, "interventions");
  for (const platform of Object.keys(PLATFORM_CONFIG)) {
    assertDummyAccountPairId(accountPairIds[platform], platform);
    assertPlainObject(reviewedUniqueFeedItems[platform], `${platform} reviewedUniqueFeedItems`);
    assertReviewedItems(reviewedUniqueFeedItems[platform].before, `${platform} before reviewed item count`);
    assertReviewedItems(reviewedUniqueFeedItems[platform].after, `${platform} after reviewed item count`);
    assertIntervention(interventions[platform], platform);
  }

  const realCaptureDirectory = await resolveCaptureDirectory(captureDirectory);
  const absoluteOutputPath = resolveOutputPath(realCaptureDirectory, outputPath);
  const discovered = await discoverFrames(realCaptureDirectory);
  const captures = [];
  for (const [platform, phase] of CAPTURE_ORDER) {
    const frames = await buildFrames(realCaptureDirectory, discovered.get(captureKey(platform, phase)));
    captures.push({
      platform,
      phase,
      started_at: frames[0].captured_at,
      ended_at: frames.at(-1).captured_at,
      account_pair_id: accountPairIds[platform],
      capture_method: "owner_recorded_live_scroll",
      reviewed_unique_feed_items: reviewedUniqueFeedItems[platform][phase],
      page_kind: "home_feed",
      source_origin: PLATFORM_CONFIG[platform].origin,
      frames,
    });
  }

  const captureByKey = new Map(captures.map((capture) => [captureKey(capture.platform, capture.phase), capture]));
  const interventionDocuments = Object.keys(PLATFORM_CONFIG).map((platform) => {
    const intervention = interventions[platform];
    const appliedAtMs = parseIsoTimestamp(intervention.appliedAt, `${platform} intervention appliedAt`);
    const beforeEndedAtMs = Date.parse(captureByKey.get(captureKey(platform, "before")).ended_at);
    const afterStartedAtMs = Date.parse(captureByKey.get(captureKey(platform, "after")).started_at);
    if (appliedAtMs <= beforeEndedAtMs || appliedAtMs >= afterStartedAtMs) {
      throw new Error(`${platform} intervention appliedAt must be between the before capture end and after capture start.`);
    }
    return {
      platform,
      applied_at: new Date(appliedAtMs).toISOString(),
      evidence_class: intervention.evidenceClass,
      claim_scope: "visible_sample_only",
      kind: intervention.kind.trim(),
    };
  });

  const manifest = {
    schema: SCHEMA,
    account_class: "dummy",
    public_demo_reviewed: true,
    captures,
    interventions: interventionDocuments,
  };
  await fs.writeFile(absoluteOutputPath, `${JSON.stringify(manifest, null, 2)}\n`, {
    encoding: "utf8",
    flag: overwrite ? "w" : "wx",
  });
  return { manifest, outputPath: absoluteOutputPath };
}

function parseCliArguments(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--force") {
      values.force = true;
      continue;
    }
    if (!argument.startsWith("--")) throw new Error(`Unexpected argument: ${argument}`);
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`${argument} requires a value.`);
    values[argument.slice(2)] = value;
    index += 1;
  }
  return values;
}

function required(values, name) {
  if (!values[name]) throw new Error(`--${name} is required.`);
  return values[name];
}

function reviewedCount(values, platform, phase) {
  const flag = `${platform}-${phase}-reviewed-items`;
  const parsed = Number(required(values, flag));
  return parsed;
}

async function main(argv) {
  const values = parseCliArguments(argv);
  const result = await buildPlatformFeedCaptureManifest({
    captureDirectory: required(values, "capture-dir"),
    outputPath: values.output,
    overwrite: values.force === true,
    accountPairIds: {
      youtube: required(values, "youtube-account-pair-id"),
      bluesky: required(values, "bluesky-account-pair-id"),
    },
    reviewedUniqueFeedItems: {
      youtube: {
        before: reviewedCount(values, "youtube", "before"),
        after: reviewedCount(values, "youtube", "after"),
      },
      bluesky: {
        before: reviewedCount(values, "bluesky", "before"),
        after: reviewedCount(values, "bluesky", "after"),
      },
    },
    interventions: {
      youtube: {
        appliedAt: required(values, "youtube-intervention-at"),
        evidenceClass: values["youtube-evidence-class"] || "owner_attested",
        kind: required(values, "youtube-intervention-kind"),
      },
      bluesky: {
        appliedAt: required(values, "bluesky-intervention-at"),
        evidenceClass: values["bluesky-evidence-class"] || "owner_attested",
        kind: required(values, "bluesky-intervention-kind"),
      },
    },
  });
  process.stdout.write(`${result.outputPath}\n`);
}

const isDirectRun = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isDirectRun) {
  main(process.argv.slice(2)).catch((error) => {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  });
}
