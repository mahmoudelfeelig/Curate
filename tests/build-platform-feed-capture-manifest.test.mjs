import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { deflateSync } from "node:zlib";

import { buildPlatformFeedCaptureManifest } from "../scripts/build-platform-feed-capture-manifest.mjs";
import { loadPlatformFeedCaptureManifest } from "../scripts/platform-feed-capture-evidence.mjs";

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBytes = Buffer.from(type, "ascii");
  const chunk = Buffer.alloc(12 + data.length);
  chunk.writeUInt32BE(data.length, 0);
  typeBytes.copy(chunk, 4);
  data.copy(chunk, 8);
  chunk.writeUInt32BE(crc32(Buffer.concat([typeBytes, data])), 8 + data.length);
  return chunk;
}

function screenshotPng(seed, width = 800, height = 450) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header.set([8, 0, 0, 0, 0], 8);
  const pixels = Buffer.alloc((width + 1) * height);
  for (let row = 0; row < height; row += 1) {
    pixels[row * (width + 1)] = 0;
    pixels.fill((seed + row) % 256, row * (width + 1) + 1, (row + 1) * (width + 1));
  }
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    pngChunk("IHDR", header),
    pngChunk("IDAT", deflateSync(pixels)),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

async function fixture({ framesPerSequence = 6 } = {}) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "curate-manifest-builder-"));
  let seed = 1;
  for (const [platform, phase, minute] of [
    ["youtube", "before", 0],
    ["bluesky", "before", 1],
    ["youtube", "after", 10],
    ["bluesky", "after", 11],
  ]) {
    for (let index = 1; index <= framesPerSequence; index += 1) {
      const filename = `${platform}-${phase}-${String(index).padStart(2, "0")}.png`;
      const filePath = path.join(directory, filename);
      await fs.writeFile(filePath, screenshotPng(seed));
      seed += 1;
      const timestamp = new Date(`2026-09-14T10:${String(minute).padStart(2, "0")}:${String(index).padStart(2, "0")}.000Z`);
      await fs.utimes(filePath, timestamp, timestamp);
    }
  }
  return directory;
}

function options(directory, overrides = {}) {
  return {
    captureDirectory: directory,
    outputPath: path.join(directory, "manifest.json"),
    accountPairIds: {
      youtube: "dummy-youtube-public-demo-a1",
      bluesky: "dummy-bluesky-public-demo-b1",
    },
    reviewedUniqueFeedItems: {
      youtube: { before: 23, after: 27 },
      bluesky: { before: 24, after: 29 },
    },
    interventions: {
      youtube: {
        appliedAt: "2026-09-14T10:05:00.000Z",
        evidenceClass: "receipt_bound",
        kind: "reversible subscription curation",
      },
      bluesky: {
        appliedAt: "2026-09-14T10:06:00.000Z",
        evidenceClass: "owner_attested",
        kind: "reversible follow curation",
      },
    },
    ...overrides,
  };
}

test("builds a validator-compatible v2 manifest from four ordered frame sequences", async (t) => {
  const directory = await fixture();
  t.after(() => fs.rm(directory, { recursive: true, force: true }));

  const { manifest, outputPath } = await buildPlatformFeedCaptureManifest(options(directory));
  assert.equal(outputPath, path.join(directory, "manifest.json"));
  assert.equal(manifest.schema, "curate/platform-feed-capture-manifest/v2");
  assert.equal(manifest.account_class, "dummy");
  assert.equal(manifest.public_demo_reviewed, true);
  assert.deepEqual(manifest.captures.map(({ platform, phase }) => `${platform}:${phase}`), [
    "youtube:before",
    "bluesky:before",
    "youtube:after",
    "bluesky:after",
  ]);

  const youtubeBefore = manifest.captures[0];
  assert.equal(youtubeBefore.frames.length, 6);
  assert.deepEqual(youtubeBefore.frames.map(({ scroll_y: scrollY }) => scrollY), [0, 500, 1000, 1500, 2000, 2500]);
  assert.equal(youtubeBefore.started_at, youtubeBefore.frames[0].captured_at);
  assert.equal(youtubeBefore.ended_at, youtubeBefore.frames.at(-1).captured_at);
  assert.equal(youtubeBefore.account_pair_id, "dummy-youtube-public-demo-a1");
  assert.equal(youtubeBefore.reviewed_unique_feed_items, 23);
  const firstBytes = await fs.readFile(path.join(directory, "youtube-before-01.png"));
  assert.equal(youtubeBefore.frames[0].sha256, createHash("sha256").update(firstBytes).digest("hex"));
  assert.ok(youtubeBefore.frames.every((frame, index, frames) => index === 0
    || Date.parse(frame.captured_at) > Date.parse(frames[index - 1].captured_at)));

  const evidence = await loadPlatformFeedCaptureManifest(outputPath);
  assert.equal(evidence.sequence_depth_sufficient, true);
  assert.equal(evidence.captures.length, 4);
  assert.equal(evidence.interventions[0].claim_scope, "visible_sample_only");
  assert.equal(evidence.captures[3].reviewed_unique_feed_items, 29);
});

test("refuses incomplete or non-contiguous frame sequences", async (t) => {
  const incomplete = await fixture({ framesPerSequence: 5 });
  const nonContiguous = await fixture();
  await fs.rename(
    path.join(nonContiguous, "youtube-before-06.png"),
    path.join(nonContiguous, "youtube-before-07.png"),
  );
  t.after(() => Promise.all([incomplete, nonContiguous].map((directory) => fs.rm(directory, { recursive: true, force: true }))));

  await assert.rejects(buildPlatformFeedCaptureManifest(options(incomplete)), /at least 6 frame files/);
  await assert.rejects(buildPlatformFeedCaptureManifest(options(nonContiguous)), /contiguous and begin at 1/);
});

test("requires reviewed counts and opaque platform-specific dummy account IDs", async (t) => {
  const directory = await fixture();
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const lowReview = options(directory);
  lowReview.reviewedUniqueFeedItems.youtube.before = 19;
  const identifyingPair = options(directory);
  identifyingPair.accountPairIds.bluesky = "someone@example.com";

  await assert.rejects(buildPlatformFeedCaptureManifest(lowReview), /at least 20/);
  await assert.rejects(buildPlatformFeedCaptureManifest(identifyingPair), /opaque dummy ID/);
});

test("requires an explicit intervention between each before and after sequence", async (t) => {
  const directory = await fixture();
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const late = options(directory);
  late.interventions.youtube.appliedAt = "2026-09-14T10:12:00.000Z";
  const invalid = options(directory);
  invalid.interventions.bluesky.appliedAt = "not-a-time";

  await assert.rejects(buildPlatformFeedCaptureManifest(late), /between the before capture end and after capture start/);
  await assert.rejects(buildPlatformFeedCaptureManifest(invalid), /must be an ISO timestamp/);
});

test("keeps the manifest beside its evidence and does not overwrite by default", async (t) => {
  const directory = await fixture();
  const outside = await fs.mkdtemp(path.join(os.tmpdir(), "curate-manifest-output-"));
  t.after(() => Promise.all([directory, outside].map((entry) => fs.rm(entry, { recursive: true, force: true }))));

  await assert.rejects(
    buildPlatformFeedCaptureManifest(options(directory, { outputPath: path.join(outside, "manifest.json") })),
    /directly inside captureDirectory/,
  );
  await buildPlatformFeedCaptureManifest(options(directory));
  await assert.rejects(buildPlatformFeedCaptureManifest(options(directory)), /EEXIST/);
  const result = await buildPlatformFeedCaptureManifest(options(directory, { overwrite: true }));
  assert.equal(result.manifest.captures.length, 4);
});
