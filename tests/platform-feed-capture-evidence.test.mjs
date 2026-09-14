import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { deflateSync } from "node:zlib";

import {
  loadPlatformFeedCaptureManifest,
  publicCaptureAttestation,
} from "../scripts/platform-feed-capture-evidence.mjs";

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

function screenshotPng(seed) {
  const width = 800;
  const height = 450;
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

async function fixture(overrides = {}) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "curate-platform-captures-"));
  const captures = [];
  for (const [platform, origin] of [["youtube", "https://www.youtube.com"], ["bluesky", "https://bsky.app"]]) {
    for (const [phase, capturedAt] of [["before", "2026-09-14T10:00:00.000Z"], ["after", "2026-09-14T10:10:00.000Z"]]) {
      const file = `${platform}-${phase}.png`;
      const bytes = screenshotPng(captures.length + 1);
      const digest = createHash("sha256").update(bytes).digest("hex");
      await fs.writeFile(path.join(directory, file), bytes);
      captures.push({ platform, phase, captured_at: capturedAt, file, sha256: digest, page_kind: "home_feed", source_origin: origin });
    }
  }
  const document = {
    schema: "curate/platform-feed-capture-manifest/v1",
    account_class: "dummy",
    public_demo_reviewed: true,
    captures,
    ...overrides,
  };
  const manifestPath = path.join(directory, "manifest.json");
  await fs.writeFile(manifestPath, JSON.stringify(document), "utf8");
  return { directory, document, manifestPath };
}

test("loads complete, reviewed YouTube and Bluesky feed pairs in presentation order", async (t) => {
  const input = await fixture();
  t.after(() => fs.rm(input.directory, { recursive: true, force: true }));
  const evidence = await loadPlatformFeedCaptureManifest(input.manifestPath);
  assert.deepEqual(evidence.captures.map(({ platform, phase }) => `${platform}:${phase}`), [
    "youtube:before",
    "bluesky:before",
    "youtube:after",
    "bluesky:after",
  ]);
  const publicEvidence = publicCaptureAttestation(evidence);
  assert.equal(publicEvidence.account_class, "dummy");
  assert.equal(publicEvidence.captures.length, 4);
  assert.equal("captured_at_ms" in publicEvidence.captures[0], false);
  assert.equal(publicEvidence.captures[0].width, 800);
  assert.equal(publicEvidence.captures[0].height, 450);
  assert.ok(publicEvidence.captures[0].bytes > 100);
});

test("rejects personal, incomplete, stale, and tampered capture evidence", async (t) => {
  const personal = await fixture({ account_class: "personal" });
  const incomplete = await fixture();
  incomplete.document.captures.pop();
  await fs.writeFile(incomplete.manifestPath, JSON.stringify(incomplete.document), "utf8");
  const stale = await fixture();
  stale.document.captures.find((capture) => capture.platform === "youtube" && capture.phase === "after").captured_at = "2026-09-14T09:00:00.000Z";
  await fs.writeFile(stale.manifestPath, JSON.stringify(stale.document), "utf8");
  const tampered = await fixture();
  tampered.document.captures[0].sha256 = "0".repeat(64);
  await fs.writeFile(tampered.manifestPath, JSON.stringify(tampered.document), "utf8");
  t.after(() => Promise.all([personal, incomplete, stale, tampered].map(({ directory }) => fs.rm(directory, { recursive: true, force: true }))));

  await assert.rejects(loadPlatformFeedCaptureManifest(personal.manifestPath), /dummy accounts/);
  await assert.rejects(loadPlatformFeedCaptureManifest(incomplete.manifestPath), /exactly one before and after/);
  await assert.rejects(loadPlatformFeedCaptureManifest(stale.manifestPath), /after capture must be newer/);
  await assert.rejects(loadPlatformFeedCaptureManifest(tampered.manifestPath), /hash does not match/);
});

test("rejects captures that are not canonical home-feed images", async (t) => {
  const wrongPage = await fixture();
  wrongPage.document.captures[0].page_kind = "channel";
  await fs.writeFile(wrongPage.manifestPath, JSON.stringify(wrongPage.document), "utf8");
  const wrongOrigin = await fixture();
  wrongOrigin.document.captures[0].source_origin = "https://youtube.example";
  await fs.writeFile(wrongOrigin.manifestPath, JSON.stringify(wrongOrigin.document), "utf8");
  t.after(() => Promise.all([wrongPage, wrongOrigin].map(({ directory }) => fs.rm(directory, { recursive: true, force: true }))));

  await assert.rejects(loadPlatformFeedCaptureManifest(wrongPage.manifestPath), /home feed/);
  await assert.rejects(loadPlatformFeedCaptureManifest(wrongOrigin.manifestPath), /canonical youtube web origin/);
});

test("rejects tiny and identical before/after captures", async (t) => {
  const tiny = await fixture();
  const tinyBytes = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZQmcAAAAASUVORK5CYII=",
    "base64",
  );
  await fs.writeFile(path.join(tiny.directory, tiny.document.captures[0].file), tinyBytes);
  tiny.document.captures[0].sha256 = createHash("sha256").update(tinyBytes).digest("hex");
  await fs.writeFile(tiny.manifestPath, JSON.stringify(tiny.document), "utf8");

  const identical = await fixture();
  const before = identical.document.captures.find((capture) => capture.platform === "youtube" && capture.phase === "before");
  const after = identical.document.captures.find((capture) => capture.platform === "youtube" && capture.phase === "after");
  const beforeBytes = await fs.readFile(path.join(identical.directory, before.file));
  await fs.writeFile(path.join(identical.directory, after.file), beforeBytes);
  after.sha256 = before.sha256;
  await fs.writeFile(identical.manifestPath, JSON.stringify(identical.document), "utf8");

  t.after(() => Promise.all([tiny, identical].map(({ directory }) => fs.rm(directory, { recursive: true, force: true }))));
  await assert.rejects(loadPlatformFeedCaptureManifest(tiny.manifestPath), /at least 800 by 450/);
  await assert.rejects(loadPlatformFeedCaptureManifest(identical.manifestPath), /visibly distinct files/);
});
