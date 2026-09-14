import assert from "node:assert/strict";
import test from "node:test";

import {
  DEMO_RUNTIME_BOUNDS_MS,
  demoChapters,
  managedAwsProof,
  platformFeedStory,
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
      assert.notEqual(scene.scrollFrom, scene.scrollTo);
      assert.ok(scene.labels.length >= 2);
      assert.ok(scene.labels.every(({ text, tone, x, y }) => text && tone && x >= 0 && x <= 100 && y >= 0 && y <= 100));
      assert.ok(scene.summary.length > 20);
    }
  }
});

test("the AWS interstitial is separate, redacted, and non-executing", () => {
  assert.equal(managedAwsProof.stamp, "Recorded managed run");
  assert.deepEqual(managedAwsProof.rows.at(-1), ["Account changes", "None"]);
  const rendered = JSON.stringify(managedAwsProof);
  assert.doesNotMatch(rendered, /arn:|gateway|account id|token|credential|https?:\/\//i);
});
