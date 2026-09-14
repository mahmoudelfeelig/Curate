import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import {
  DEMO_RUNTIME_BOUNDS_MS,
  autonomousRunStory,
  demoChapters,
  demoPersonas,
  demoVideo,
  exactTopicTarget,
  managedAwsProof,
  platformFeedStory,
  agentOutcomeGate,
  tutorialFeatures,
  validateDemoStoryboard,
} from "../scripts/demo-storyboard.mjs";

const projectRoot = path.resolve(import.meta.dirname, "..");

test("the silent demo is a complete two-to-four minute tutorial", () => {
  assert.equal(validateDemoStoryboard(), true);
  assert.deepEqual(DEMO_RUNTIME_BOUNDS_MS, { minimum: 120_000, maximum: 240_000 });
  assert.deepEqual(demoChapters.map(({ id }) => id), [
    "starting-feeds", "describe", "agent", "results", "copy", "incognito", "blend",
  ]);
  assert.deepEqual(tutorialFeatures.map(({ id }) => id), ["tune", "copy", "incognito", "blend"]);
});

test("every platform state annotates multiple recorded sequence frames", () => {
  let totalLabels = 0;
  for (const platform of ["youtube", "bluesky"]) {
    for (const phase of ["before", "after"]) {
      const scene = platformFeedStory[platform][phase];
      const minimumFrames = phase === "before" ? 2 : 4;
      assert.ok(scene.frameIndexes.length >= minimumFrames);
      assert.equal(new Set(scene.frameIndexes).size, scene.frameIndexes.length);
      assert.ok(scene.labels.length >= scene.frameIndexes.length);
      assert.ok(scene.labels.every(({ text, tone, x, y, frameIndex }) => text && tone && x >= 0 && x <= 100 && y >= 0 && y <= 100 && Number.isSafeInteger(frameIndex) && frameIndex >= 0));
      const counts = new Map();
      for (const label of scene.labels) counts.set(label.frameIndex, (counts.get(label.frameIndex) || 0) + 1);
      assert.ok(scene.frameIndexes.every((frameIndex) => counts.has(frameIndex)));
      assert.ok(scene.frameIndexes.every((frameIndex) => scene.frameHolds[frameIndex] >= demoVideo.minimumLabeledFrameHoldMs));
      assert.ok(scene.summary.length > 20);
      totalLabels += scene.labels.length;
    }
  }
  assert.ok(totalLabels >= 24);
});

test("the final cut stays native 1080p and demonstrates five distinct personalities", () => {
  assert.deepEqual(demoVideo, {
    width: 1920,
    height: 1080,
    bitrate: 20_000_000,
    minimumLabeledFrameHoldMs: 2_500,
    defaultFrameHoldMs: 1_000,
  });
  assert.ok(demoVideo.bitrate >= 18_000_000 && demoVideo.bitrate <= 24_000_000);
  assert.equal(demoPersonas.length, 5);
  assert.equal(new Set(demoPersonas.map(({ id }) => id)).size, 5);
  assert.equal(new Set(demoPersonas.map(({ prompt }) => prompt)).size, 5);
  assert.deepEqual(demoPersonas.map(({ feature }) => feature), ["tune", "tune", "copy", "incognito", "blend"]);
  assert.ok(demoPersonas.every(({ prompt }) => prompt.length >= 40));
  assert.deepEqual(platformFeedStory.youtube.before.frameIndexes, [0, 8]);
  assert.deepEqual(platformFeedStory.bluesky.before.frameIndexes, [1, 7]);
  assert.ok(platformFeedStory.youtube.after.frameIndexes.length > platformFeedStory.youtube.before.frameIndexes.length);
  assert.ok(platformFeedStory.bluesky.after.frameIndexes.length > platformFeedStory.bluesky.before.frameIndexes.length);
  assert.ok(platformFeedStory.bluesky.after.labels.every(({ tone }) => tone === "up"));
  assert.ok(Object.values(platformFeedStory).every(({ comparison }) => (
    comparison.before.labels.length >= 1 && comparison.after.labels.length >= 2
  )));
});

test("the recorder uses compact overlays instead of explanatory slides", async () => {
  const source = await fs.readFile(path.join(projectRoot, "scripts", "record-silent-demo.mjs"), "utf8");
  assert.doesNotMatch(source, /function showChapter|function showGuide|function showPlatformComparison|function showAutonomousRunProof/);
  assert.match(source, /function showBeat/);
  assert.match(source, /function showPlatformWipe/);
  assert.match(source, /FEED_PASSPORT_AWS_SCREENSHOT/);
  assert.match(source, /class="capture"><img/);
  assert.match(source, /object-fit: contain/);
  assert.match(source, /\.temporary-visa\.revoked"\)\.last\(\)\.waitFor/);
  assert.match(source, /more research, independent creators, thoughtful design, and local culture/);
  assert.doesNotMatch(source, /https:[^"\n]+\s\|/);
});

test("the exact target and agent outcome cannot pass without an obvious change", () => {
  assert.equal(exactTopicTarget.length, 7);
  assert.equal(exactTopicTarget.reduce((sum, [, percent]) => sum + percent, 0), 100);
  assert.equal(new Set(exactTopicTarget.map(([topic]) => topic)).size, 7);
  assert.ok(agentOutcomeGate.minimumUnwantedDropPoints >= 10);
  assert.ok(agentOutcomeGate.minimumSourceDropPoints >= 10);
  assert.ok(agentOutcomeGate.minimumSurpriseGainPoints >= 10);
});

test("the AWS replay is detailed, redacted, and non-executing", () => {
  assert.equal(managedAwsProof.stamp, "Recorded managed run");
  assert.deepEqual(managedAwsProof.events.at(-1), { kind: "result", label: "Proposal ready", detail: "no account changes" });
  assert.ok(managedAwsProof.events.some(({ label }) => label === "inspect_selected_passport"));
  assert.ok(managedAwsProof.events.some(({ label }) => label === "submit_feed_goal_proposal"));
  assert.equal(managedAwsProof.infrastructure.region, "eu-north-1");
  assert.equal(managedAwsProof.infrastructure.stack, "UPDATE_COMPLETE");
  assert.equal(managedAwsProof.infrastructure.runtime, "READY");
  assert.match(managedAwsProof.infrastructure.logGroup, /^\/aws\/bedrock-agentcore\/runtimes\//);
  assert.equal(managedAwsProof.infrastructure.logRetentionDays, 7);
  assert.deepEqual(managedAwsProof.cloudWatchEvents.map(({ operation }) => operation), ["health", "plan_feed"]);
  const rendered = JSON.stringify(managedAwsProof);
  assert.doesNotMatch(rendered, /arn:|gateway|account id|token|credential|https?:\/\//i);
});

test("the autonomous run is shown as a complete agent loop", () => {
  assert.equal(autonomousRunStory.steps.length, 4);
  assert.deepEqual(autonomousRunStory.steps.map(({ label }) => label), ["Choose", "Apply", "Measure", "Stop"]);
  assert.match(autonomousRunStory.title, /agent/i);
});
