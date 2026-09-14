import assert from "node:assert/strict";
import test from "node:test";

import {
  DEMO_RUNTIME_BOUNDS_MS,
  demoChapters,
  exactTopicTarget,
  managedAwsProof,
  platformFeedStory,
  practiceFeedTour,
  tutorialFeatures,
  validateDemoStoryboard,
} from "../scripts/demo-storyboard.mjs";

test("the silent demo is a complete two-to-four minute tutorial", () => {
  assert.equal(validateDemoStoryboard(), true);
  assert.deepEqual(DEMO_RUNTIME_BOUNDS_MS, { minimum: 120_000, maximum: 240_000 });
  assert.deepEqual(demoChapters.map(({ id }) => id), [
    "starting-feeds", "describe", "agent", "results", "copy", "incognito", "blend",
  ]);
  assert.deepEqual(tutorialFeatures.map(({ id }) => id), ["tune", "copy", "incognito", "blend"]);
});

test("every platform state pans and explains visible content", () => {
  for (const platform of ["youtube", "bluesky"]) {
    for (const phase of ["before", "after"]) {
      const scene = platformFeedStory[platform][phase];
      assert.ok(Math.abs(scene.scrollTo - scene.scrollFrom) >= 900);
      assert.ok(scene.zoomPercent >= 140);
      assert.ok(scene.labels.length >= 2);
      assert.ok(scene.labels.every(({ text, tone, x, y }) => text && tone && x >= 0 && x <= 100 && y >= 0 && y <= 100));
      assert.ok(scene.summary.length > 20);
    }
  }
});

test("the exact target and practice feed tour cannot pass without an obvious change", () => {
  assert.equal(exactTopicTarget.length, 7);
  assert.equal(exactTopicTarget.reduce((sum, [, percent]) => sum + percent, 0), 100);
  assert.equal(new Set(exactTopicTarget.map(([topic]) => topic)).size, 7);
  assert.ok(practiceFeedTour.minimumScrollPixels >= 900);
  assert.ok(practiceFeedTour.minimumCardsPerPhase >= 6);
  assert.equal(practiceFeedTour.expectedRagebaitDropPoints, 100);
});

test("the AWS replay is detailed, redacted, and non-executing", () => {
  assert.equal(managedAwsProof.stamp, "Recorded managed run");
  assert.deepEqual(managedAwsProof.events.at(-1), { kind: "result", label: "Proposal ready", detail: "no account changes" });
  assert.ok(managedAwsProof.events.some(({ label }) => label === "inspect_selected_passport"));
  assert.ok(managedAwsProof.events.some(({ label }) => label === "submit_feed_goal_proposal"));
  const rendered = JSON.stringify(managedAwsProof);
  assert.doesNotMatch(rendered, /arn:|gateway|account id|token|credential|https?:\/\//i);
});
