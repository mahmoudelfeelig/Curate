import assert from "node:assert/strict";
import test from "node:test";

import { platformGuidance, supportedGuidancePlatforms } from "./platformGuidance.js";

const expected = ["bluesky", "facebook", "instagram", "linkedin", "reddit", "snapchat", "threads", "tiktok", "x", "youtube"];

test("every supported social platform gets safe actionable guidance", () => {
  assert.deepEqual([...supportedGuidancePlatforms()].sort(), expected);
  for (const platform of expected) {
    const result = platformGuidance(platform, "less ragebait, more marine biology and calm drawing");
    assert.equal(result.platform, platform);
    assert.match(result.queryText, /marine biology/);
    assert.doesNotMatch(result.queryText, /ragebait/i);
    assert.equal(result.links.length, 3);
    assert.equal(result.actions.length, 2);
    for (const link of result.links) {
      const url = new URL(link.url);
      assert.equal(url.protocol, "https:");
      assert.equal(url.username, "");
      assert.equal(url.password, "");
    }
  }
});

test("queries are encoded instead of interpolated as URL syntax", () => {
  const result = platformGuidance("youtube", "C++ & art #calm");
  const url = new URL(result.links[0].url);
  assert.match(url.searchParams.get("search_query"), /C\+\+/);
  assert.doesNotMatch(result.links[0].url, / & /);
});

test("vague reductions keep the positive request after a conjunction", () => {
  assert.equal(
    platformGuidance("youtube", "i want less ragebait and more science-based pages").queryText,
    "science-based",
  );
  assert.equal(
    platformGuidance("youtube", "reduce celebrity drama but add astrophysics and sketching").queryText,
    "astrophysics sketching",
  );
});

test("unknown destinations do not invent a route", () => {
  assert.equal(platformGuidance("lab", "science"), null);
  assert.equal(platformGuidance("unknown", "science"), null);
});
