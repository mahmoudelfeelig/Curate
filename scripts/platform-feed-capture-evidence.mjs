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

const MIN_CAPTURE_WIDTH = 800;
const MIN_CAPTURE_HEIGHT = 450;
const MIN_SEQUENCE_FRAMES = 6;
const MIN_REVIEWED_UNIQUE_ITEMS = 20;
const MIN_SCROLL_SPAN = 2500;
const V1_SCHEMA = "curate/platform-feed-capture-manifest/v1";
const V2_SCHEMA = "curate/platform-feed-capture-manifest/v2";

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

function jpegDimensions(bytes, label) {
  let offset = 2;
  while (offset + 8 < bytes.length) {
    if (bytes[offset] !== 0xff) {
      offset += 1;
      continue;
    }
    while (bytes[offset] === 0xff) offset += 1;
    const marker = bytes[offset];
    offset += 1;
    if (marker === 0xd8 || marker === 0xd9 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
    if (offset + 2 > bytes.length) break;
    const segmentLength = bytes.readUInt16BE(offset);
    if (segmentLength < 2 || offset + segmentLength > bytes.length) break;
    if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker)) {
      if (segmentLength < 7) break;
      return { width: bytes.readUInt16BE(offset + 5), height: bytes.readUInt16BE(offset + 3) };
    }
    offset += segmentLength;
  }
  throw new Error(`${label} JPEG dimensions could not be read.`);
}

function webpDimensions(bytes, label) {
  const chunk = bytes.subarray(12, 16).toString("ascii");
  if (chunk === "VP8X" && bytes.length >= 30) {
    return { width: bytes.readUIntLE(24, 3) + 1, height: bytes.readUIntLE(27, 3) + 1 };
  }
  if (chunk === "VP8L" && bytes.length >= 25 && bytes[20] === 0x2f) {
    const bits = bytes.readUInt32LE(21);
    return { width: (bits & 0x3fff) + 1, height: ((bits >>> 14) & 0x3fff) + 1 };
  }
  if (chunk === "VP8 " && bytes.length >= 30 && bytes.subarray(23, 26).equals(Buffer.from([0x9d, 0x01, 0x2a]))) {
    return { width: bytes.readUInt16LE(26) & 0x3fff, height: bytes.readUInt16LE(28) & 0x3fff };
  }
  throw new Error(`${label} WebP dimensions could not be read.`);
}

function imageDimensions(bytes, mimeType, label) {
  if (mimeType === "image/png") return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
  if (mimeType === "image/jpeg") return jpegDimensions(bytes, label);
  return webpDimensions(bytes, label);
}

async function loadV1Manifest(manifestPath) {
  if (!manifestPath) {
    throw new Error(
      "FEED_PASSPORT_PLATFORM_CAPTURE_MANIFEST must point to four reviewed dummy-account feed captures.",
    );
  }
  const absoluteManifestPath = path.resolve(manifestPath);
  const manifestDirectory = path.dirname(absoluteManifestPath);
  const document = JSON.parse(await fs.readFile(absoluteManifestPath, "utf8"));
  assertExactKeys(document, ["schema", "account_class", "public_demo_reviewed", "captures"], "Capture manifest");
  if (document.schema !== V1_SCHEMA) {
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
    const dimensions = imageDimensions(bytes, mimeType, label);
    if (dimensions.width < MIN_CAPTURE_WIDTH || dimensions.height < MIN_CAPTURE_HEIGHT) {
      throw new Error(`${label} must be at least ${MIN_CAPTURE_WIDTH} by ${MIN_CAPTURE_HEIGHT} pixels so the platform feed is legible.`);
    }
    const digest = createHash("sha256").update(bytes).digest("hex");
    if (digest !== capture.sha256) throw new Error(`${label} image hash does not match the reviewed manifest.`);
    const frame = {
      captured_at: capture.captured_at,
      captured_at_ms: capturedAtMs,
      file: capture.file,
      sha256: digest,
      scroll_y: null,
      mime_type: mimeType,
      ...dimensions,
      bytes,
    };
    captures.push({
      platform: capture.platform,
      phase: capture.phase,
      captured_at: capture.captured_at,
      captured_at_ms: capturedAtMs,
      file: capture.file,
      page_kind: capture.page_kind,
      source_origin: capture.source_origin,
      sha256: digest,
      mime_type: mimeType,
      ...dimensions,
      bytes,
      frames: [frame],
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
    if (after.sha256 === before.sha256) {
      throw new Error(`${platform} before and after captures must be visibly distinct files.`);
    }
  }

  return {
    schema: document.schema,
    account_class: document.account_class,
    public_demo_reviewed: document.public_demo_reviewed,
    sequence_depth_sufficient: false,
    interventions: [],
    captures: REQUIRED_CAPTURES.map(([platform, phase]) => captures.find((capture) => (
      capture.platform === platform && capture.phase === phase
    ))),
  };
}

function assertSafeRelativePath(manifestDirectory, file, label) {
  if (typeof file !== "string" || path.isAbsolute(file) || !file.trim()) {
    throw new Error(`${label} file must be a relative image path beside the manifest.`);
  }
  const imagePath = path.resolve(manifestDirectory, file);
  const relativeImagePath = path.relative(manifestDirectory, imagePath);
  if (relativeImagePath.startsWith("..") || path.isAbsolute(relativeImagePath)) {
    throw new Error(`${label} file must stay inside the capture directory.`);
  }
  return imagePath;
}

async function loadV2Frame(manifestDirectory, realManifestDirectory, frame, label) {
  if (!frame || typeof frame !== "object" || Array.isArray(frame)) throw new Error(`${label} must be an object.`);
  assertExactKeys(frame, ["captured_at", "file", "sha256", "scroll_y"], label);
  const capturedAtMs = parseTimestamp(frame.captured_at, `${label} captured_at`);
  if (!Number.isSafeInteger(frame.scroll_y) || frame.scroll_y < 0) {
    throw new Error(`${label} scroll_y must be a nonnegative integer.`);
  }
  const imagePath = assertSafeRelativePath(manifestDirectory, frame.file, label);
  const realImagePath = await fs.realpath(imagePath);
  const realRelativePath = path.relative(realManifestDirectory, realImagePath);
  if (realRelativePath.startsWith("..") || path.isAbsolute(realRelativePath)) {
    throw new Error(`${label} file must stay inside the capture directory.`);
  }
  const extension = path.extname(imagePath).toLowerCase();
  const mimeType = IMAGE_TYPES[extension];
  if (!mimeType) throw new Error(`${label} must be a PNG, JPEG, or WebP image.`);
  if (!/^[a-f0-9]{64}$/.test(frame.sha256 || "")) throw new Error(`${label} sha256 must be lowercase hexadecimal.`);
  const bytes = await fs.readFile(realImagePath);
  if (!bytes.length || bytes.length > 12 * 1024 * 1024) throw new Error(`${label} image must be between 1 byte and 12 MiB.`);
  assertImageSignature(bytes, mimeType, label);
  const dimensions = imageDimensions(bytes, mimeType, label);
  if (dimensions.width < MIN_CAPTURE_WIDTH || dimensions.height < MIN_CAPTURE_HEIGHT) {
    throw new Error(`${label} must be at least ${MIN_CAPTURE_WIDTH} by ${MIN_CAPTURE_HEIGHT} pixels so the platform feed is legible.`);
  }
  const digest = createHash("sha256").update(bytes).digest("hex");
  if (digest !== frame.sha256) throw new Error(`${label} image hash does not match the reviewed manifest.`);
  return {
    captured_at: frame.captured_at,
    captured_at_ms: capturedAtMs,
    file: frame.file,
    sha256: digest,
    scroll_y: frame.scroll_y,
    mime_type: mimeType,
    ...dimensions,
    bytes,
  };
}

async function loadV2Manifest(manifestPath) {
  const absoluteManifestPath = path.resolve(manifestPath);
  const manifestDirectory = path.dirname(absoluteManifestPath);
  const realManifestDirectory = await fs.realpath(manifestDirectory);
  const document = JSON.parse(await fs.readFile(absoluteManifestPath, "utf8"));
  assertExactKeys(document, ["schema", "account_class", "public_demo_reviewed", "captures", "interventions"], "Capture manifest");
  if (document.schema !== V2_SCHEMA) throw new Error("Capture manifest has an unsupported schema.");
  if (document.account_class !== "dummy") {
    throw new Error("Platform feed captures must come from dummy accounts, never personal accounts.");
  }
  if (document.public_demo_reviewed !== true) {
    throw new Error("Platform feed captures must be reviewed for public-demo identifiers before recording.");
  }
  if (!Array.isArray(document.captures) || document.captures.length !== REQUIRED_CAPTURES.length) {
    throw new Error("Capture manifest must contain exactly one before and after home feed for YouTube and Bluesky.");
  }

  const seenCaptures = new Set();
  const seenFiles = new Set();
  const seenHashes = new Set();
  let expectedDimensions;
  const captures = [];
  for (const [captureIndex, capture] of document.captures.entries()) {
    const label = `Capture ${captureIndex + 1}`;
    if (!capture || typeof capture !== "object" || Array.isArray(capture)) throw new Error(`${label} must be an object.`);
    assertExactKeys(capture, [
      "platform",
      "phase",
      "started_at",
      "ended_at",
      "account_pair_id",
      "capture_method",
      "reviewed_unique_feed_items",
      "page_kind",
      "source_origin",
      "frames",
    ], label);
    const key = `${capture.platform}:${capture.phase}`;
    if (!REQUIRED_CAPTURES.some(([platform, phase]) => key === `${platform}:${phase}`)) {
      throw new Error(`${label} must identify a YouTube or Bluesky before/after capture.`);
    }
    if (seenCaptures.has(key)) throw new Error(`Capture manifest contains duplicate ${key} evidence.`);
    seenCaptures.add(key);
    if (capture.page_kind !== "home_feed") throw new Error(`${label} must show the platform home feed.`);
    if (capture.source_origin !== PLATFORM_ORIGINS[capture.platform]) {
      throw new Error(`${label} must use the canonical ${capture.platform} web origin.`);
    }
    if (typeof capture.account_pair_id !== "string" || !capture.account_pair_id.trim()) {
      throw new Error(`${label} account_pair_id must be nonempty.`);
    }
    if (capture.capture_method !== "owner_recorded_live_scroll") {
      throw new Error(`${label} capture_method must be owner_recorded_live_scroll.`);
    }
    if (!Number.isSafeInteger(capture.reviewed_unique_feed_items)
      || capture.reviewed_unique_feed_items < MIN_REVIEWED_UNIQUE_ITEMS) {
      throw new Error(`${label} reviewed_unique_feed_items must be at least ${MIN_REVIEWED_UNIQUE_ITEMS}.`);
    }
    if (!Array.isArray(capture.frames) || capture.frames.length < MIN_SEQUENCE_FRAMES) {
      throw new Error(`${label} must contain at least ${MIN_SEQUENCE_FRAMES} frames.`);
    }
    const startedAtMs = parseTimestamp(capture.started_at, `${label} started_at`);
    const endedAtMs = parseTimestamp(capture.ended_at, `${label} ended_at`);
    if (endedAtMs <= startedAtMs) throw new Error(`${label} ended_at must be newer than started_at.`);

    const frames = [];
    for (const [frameIndex, frameDocument] of capture.frames.entries()) {
      const frameLabel = `${label} frame ${frameIndex + 1}`;
      if (seenFiles.has(frameDocument?.file)) throw new Error(`${frameLabel} must use a unique file.`);
      if (seenHashes.has(frameDocument?.sha256)) throw new Error(`${frameLabel} must use a unique sha256.`);
      const frame = await loadV2Frame(manifestDirectory, realManifestDirectory, frameDocument, frameLabel);
      seenFiles.add(frame.file);
      seenHashes.add(frame.sha256);
      if (frame.captured_at_ms < startedAtMs || frame.captured_at_ms > endedAtMs) {
        throw new Error(`${frameLabel} captured_at must fall between the capture started_at and ended_at.`);
      }
      const previousFrame = frames.at(-1);
      if (previousFrame && frame.captured_at_ms <= previousFrame.captured_at_ms) {
        throw new Error(`${label} frames must have strictly increasing captured_at timestamps.`);
      }
      if (previousFrame && frame.scroll_y <= previousFrame.scroll_y) {
        throw new Error(`${label} frames must have strictly increasing scroll_y positions.`);
      }
      if (!expectedDimensions) expectedDimensions = { width: frame.width, height: frame.height };
      if (frame.width !== expectedDimensions.width || frame.height !== expectedDimensions.height) {
        throw new Error("All capture frames must use the same dimensions.");
      }
      frames.push(frame);
    }
    if (frames.at(-1).scroll_y - frames[0].scroll_y < MIN_SCROLL_SPAN) {
      throw new Error(`${label} scroll span must be at least ${MIN_SCROLL_SPAN} pixels.`);
    }
    captures.push({
      platform: capture.platform,
      phase: capture.phase,
      started_at: capture.started_at,
      started_at_ms: startedAtMs,
      ended_at: capture.ended_at,
      ended_at_ms: endedAtMs,
      account_pair_id: capture.account_pair_id,
      capture_method: capture.capture_method,
      reviewed_unique_feed_items: capture.reviewed_unique_feed_items,
      page_kind: capture.page_kind,
      source_origin: capture.source_origin,
      frames,
    });
  }

  for (const [platform, phase] of REQUIRED_CAPTURES) {
    if (!seenCaptures.has(`${platform}:${phase}`)) throw new Error(`Capture manifest is missing ${platform}:${phase}.`);
  }

  const captureByKey = new Map(captures.map((capture) => [`${capture.platform}:${capture.phase}`, capture]));
  for (const platform of Object.keys(PLATFORM_ORIGINS)) {
    const before = captureByKey.get(`${platform}:before`);
    const after = captureByKey.get(`${platform}:after`);
    if (before.account_pair_id !== after.account_pair_id) {
      throw new Error(`${platform} before and after captures must use the same account_pair_id.`);
    }
  }

  if (!Array.isArray(document.interventions) || document.interventions.length !== Object.keys(PLATFORM_ORIGINS).length) {
    throw new Error("Capture manifest must contain exactly one intervention for YouTube and Bluesky.");
  }
  const seenInterventions = new Set();
  const interventions = document.interventions.map((intervention, interventionIndex) => {
    const label = `Intervention ${interventionIndex + 1}`;
    if (!intervention || typeof intervention !== "object" || Array.isArray(intervention)) throw new Error(`${label} must be an object.`);
    assertExactKeys(intervention, ["platform", "applied_at", "evidence_class", "claim_scope", "kind"], label);
    if (!(intervention.platform in PLATFORM_ORIGINS)) throw new Error(`${label} must identify YouTube or Bluesky.`);
    if (seenInterventions.has(intervention.platform)) throw new Error(`Capture manifest contains duplicate ${intervention.platform} intervention evidence.`);
    seenInterventions.add(intervention.platform);
    if (!["owner_attested", "receipt_bound"].includes(intervention.evidence_class)) {
      throw new Error(`${label} evidence_class must be owner_attested or receipt_bound.`);
    }
    if (intervention.claim_scope !== "visible_sample_only") {
      throw new Error(`${label} claim_scope must be visible_sample_only.`);
    }
    if (typeof intervention.kind !== "string" || !intervention.kind.trim()) {
      throw new Error(`${label} must have a nonempty kind.`);
    }
    const appliedAtMs = parseTimestamp(intervention.applied_at, `${label} applied_at`);
    const before = captureByKey.get(`${intervention.platform}:before`);
    const after = captureByKey.get(`${intervention.platform}:after`);
    if (appliedAtMs <= before.ended_at_ms || appliedAtMs >= after.started_at_ms) {
      throw new Error(`${label} applied_at must be between the before capture end and after capture start.`);
    }
    return { ...intervention, applied_at_ms: appliedAtMs };
  });
  for (const platform of Object.keys(PLATFORM_ORIGINS)) {
    if (!seenInterventions.has(platform)) throw new Error(`Capture manifest is missing the ${platform} intervention.`);
  }

  return {
    schema: document.schema,
    account_class: document.account_class,
    public_demo_reviewed: document.public_demo_reviewed,
    sequence_depth_sufficient: true,
    interventions,
    captures: REQUIRED_CAPTURES.map(([platform, phase]) => captureByKey.get(`${platform}:${phase}`)),
  };
}

export async function loadPlatformFeedCaptureManifest(manifestPath) {
  if (!manifestPath) {
    throw new Error(
      "FEED_PASSPORT_PLATFORM_CAPTURE_MANIFEST must point to four reviewed dummy-account feed captures.",
    );
  }
  const document = JSON.parse(await fs.readFile(path.resolve(manifestPath), "utf8"));
  if (document.schema === V1_SCHEMA) return loadV1Manifest(manifestPath);
  if (document.schema === V2_SCHEMA) return loadV2Manifest(manifestPath);
  throw new Error("Capture manifest has an unsupported schema.");
}

export function publicCaptureAttestation(evidence) {
  const publicFrame = ({ bytes, captured_at_ms: capturedAtMs, ...frame }) => ({
    ...frame,
    byte_length: bytes.length,
  });
  return {
    schema: evidence.schema,
    account_class: evidence.account_class,
    public_demo_reviewed: evidence.public_demo_reviewed,
    sequence_depth_sufficient: evidence.sequence_depth_sufficient,
    interventions: evidence.interventions.map(({ applied_at_ms: appliedAtMs, ...intervention }) => intervention),
    captures: evidence.captures.map((capture) => {
      const {
        bytes,
        captured_at_ms: capturedAtMs,
        started_at_ms: startedAtMs,
        ended_at_ms: endedAtMs,
        frames,
        ...publicCapture
      } = capture;
      return {
        ...publicCapture,
        ...(bytes ? { byte_length: bytes.length } : {}),
        frames: frames.map(publicFrame),
      };
    }),
  };
}
