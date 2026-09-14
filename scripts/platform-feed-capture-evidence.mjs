import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

const REQUIRED_CAPTURES = [
  ["youtube", "before"],
  ["bluesky", "before"],
  ["youtube", "after"],
  ["bluesky", "after"],
];

const PLATFORM_ORIGINS = {
  youtube: "https://www.youtube.com",
  bluesky: "https://bsky.app",
};

const IMAGE_TYPES = {
  ".jpeg": "image/jpeg",
  ".jpg": "image/jpeg",
  ".png": "image/png",
  ".webp": "image/webp",
};

function assertExactKeys(value, allowed, label) {
  const unexpected = Object.keys(value).filter((key) => !allowed.includes(key));
  if (unexpected.length) throw new Error(`${label} has unexpected field(s): ${unexpected.join(", ")}`);
}

function parseTimestamp(value, label) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T/.test(value)) {
    throw new Error(`${label} must be an ISO timestamp.`);
  }
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) throw new Error(`${label} must be an ISO timestamp.`);
  return timestamp;
}

function assertImageSignature(bytes, mimeType, label) {
  const png = bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  const jpeg = bytes[0] === 0xff && bytes[1] === 0xd8 && bytes.at(-2) === 0xff && bytes.at(-1) === 0xd9;
  const webp = bytes.subarray(0, 4).toString("ascii") === "RIFF" && bytes.subarray(8, 12).toString("ascii") === "WEBP";
  if ((mimeType === "image/png" && !png) || (mimeType === "image/jpeg" && !jpeg) || (mimeType === "image/webp" && !webp)) {
    throw new Error(`${label} does not match its image extension.`);
  }
}

export async function loadPlatformFeedCaptureManifest(manifestPath) {
  if (!manifestPath) {
    throw new Error(
      "FEED_PASSPORT_PLATFORM_CAPTURE_MANIFEST must point to four reviewed dummy-account feed captures.",
    );
  }
  const absoluteManifestPath = path.resolve(manifestPath);
  const manifestDirectory = path.dirname(absoluteManifestPath);
  const document = JSON.parse(await fs.readFile(absoluteManifestPath, "utf8"));
  assertExactKeys(document, ["schema", "account_class", "public_demo_reviewed", "captures"], "Capture manifest");
  if (document.schema !== "curate/platform-feed-capture-manifest/v1") {
    throw new Error("Capture manifest has an unsupported schema.");
  }
  if (document.account_class !== "dummy") {
    throw new Error("Platform feed captures must come from dummy accounts, never personal accounts.");
  }
  if (document.public_demo_reviewed !== true) {
    throw new Error("Platform feed captures must be reviewed for public-demo identifiers before recording.");
  }
  if (!Array.isArray(document.captures) || document.captures.length !== REQUIRED_CAPTURES.length) {
    throw new Error("Capture manifest must contain exactly one before and after home feed for YouTube and Bluesky.");
  }

  const seen = new Set();
  const captures = [];
  for (const [index, capture] of document.captures.entries()) {
    const label = `Capture ${index + 1}`;
    if (!capture || typeof capture !== "object" || Array.isArray(capture)) throw new Error(`${label} must be an object.`);
    assertExactKeys(capture, ["platform", "phase", "captured_at", "file", "sha256", "page_kind", "source_origin"], label);
    const key = `${capture.platform}:${capture.phase}`;
    if (!REQUIRED_CAPTURES.some(([platform, phase]) => key === `${platform}:${phase}`)) {
      throw new Error(`${label} must identify a YouTube or Bluesky before/after capture.`);
    }
    if (seen.has(key)) throw new Error(`Capture manifest contains duplicate ${key} evidence.`);
    seen.add(key);
    if (capture.page_kind !== "home_feed") throw new Error(`${label} must show the platform home feed.`);
    if (capture.source_origin !== PLATFORM_ORIGINS[capture.platform]) {
      throw new Error(`${label} must use the canonical ${capture.platform} web origin.`);
    }
    const capturedAtMs = parseTimestamp(capture.captured_at, `${label} captured_at`);
    if (typeof capture.file !== "string" || path.isAbsolute(capture.file) || !capture.file.trim()) {
      throw new Error(`${label} file must be a relative image path beside the manifest.`);
    }
    const imagePath = path.resolve(manifestDirectory, capture.file);
    const relativeImagePath = path.relative(manifestDirectory, imagePath);
    if (relativeImagePath.startsWith("..") || path.isAbsolute(relativeImagePath)) {
      throw new Error(`${label} file must stay inside the capture directory.`);
    }
    const extension = path.extname(imagePath).toLowerCase();
    const mimeType = IMAGE_TYPES[extension];
    if (!mimeType) throw new Error(`${label} must be a PNG, JPEG, or WebP image.`);
    if (!/^[a-f0-9]{64}$/.test(capture.sha256 || "")) throw new Error(`${label} sha256 must be lowercase hexadecimal.`);
    const bytes = await fs.readFile(imagePath);
    if (!bytes.length || bytes.length > 12 * 1024 * 1024) throw new Error(`${label} image must be between 1 byte and 12 MiB.`);
    assertImageSignature(bytes, mimeType, label);
    const digest = createHash("sha256").update(bytes).digest("hex");
    if (digest !== capture.sha256) throw new Error(`${label} image hash does not match the reviewed manifest.`);
    captures.push({
      platform: capture.platform,
      phase: capture.phase,
      captured_at: capture.captured_at,
      captured_at_ms: capturedAtMs,
      page_kind: capture.page_kind,
      source_origin: capture.source_origin,
      sha256: digest,
      mime_type: mimeType,
      bytes,
    });
  }

  for (const [platform, phase] of REQUIRED_CAPTURES) {
    if (!seen.has(`${platform}:${phase}`)) throw new Error(`Capture manifest is missing ${platform}:${phase}.`);
  }
  for (const platform of Object.keys(PLATFORM_ORIGINS)) {
    const before = captures.find((capture) => capture.platform === platform && capture.phase === "before");
    const after = captures.find((capture) => capture.platform === platform && capture.phase === "after");
    if (after.captured_at_ms <= before.captured_at_ms) {
      throw new Error(`${platform} after capture must be newer than its before capture.`);
    }
  }

  return {
    schema: document.schema,
    account_class: document.account_class,
    public_demo_reviewed: document.public_demo_reviewed,
    captures: REQUIRED_CAPTURES.map(([platform, phase]) => captures.find((capture) => (
      capture.platform === platform && capture.phase === phase
    ))),
  };
}

export function publicCaptureAttestation(evidence) {
  return {
    schema: evidence.schema,
    account_class: evidence.account_class,
    public_demo_reviewed: evidence.public_demo_reviewed,
    captures: evidence.captures.map(({ bytes, captured_at_ms: capturedAtMs, ...capture }) => ({
      ...capture,
      bytes: bytes.length,
    })),
  };
}
