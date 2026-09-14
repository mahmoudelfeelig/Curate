import assert from "node:assert/strict";
import test from "node:test";

import { parseEvidenceLines, topicRows } from "./feedEvidence.js";
import { serverConstitution } from "../../api/clientProjections.js";

test("parseEvidenceLines preserves owner notes separately from URLs", () => {
  assert.deepEqual(
    parseEvidenceLines("https://bsky.app/profile/pets.example/post/3abc | calm pet science"),
    [{ url: "https://bsky.app/profile/pets.example/post/3abc", note: "calm pet science" }],
  );
});

test("parseEvidenceLines accepts bare links without descriptions", () => {
  assert.deepEqual(
    parseEvidenceLines("https://www.youtube.com/watch?v=abc123DEF45\nhttps://bsky.app/profile/science.example/post/3bare"),
    [
      { url: "https://www.youtube.com/watch?v=abc123DEF45" },
      { url: "https://bsky.app/profile/science.example/post/3bare" },
    ],
  );
});

test("parseEvidenceLines rejects non-HTTPS input", () => {
  assert.throws(() => parseEvidenceLines("http://example.com/post"), /must use HTTPS/);
});

test("topicRows presents strongest target first", () => {
  assert.deepEqual(topicRows({ cute_drawing: 0.2, pet_science: 0.6, exploration: 0.2 }), [
    { topic: "pet_science", percent: 60 },
    { topic: "cute_drawing", percent: 20 },
    { topic: "exploration", percent: 20 },
  ]);
});

test("server projection preserves new agent-proposed topics", () => {
  const projected = serverConstitution({
    version: 4,
    topic_targets: { pet_science: 0.6, cute_drawing: 0.2, exploration: 0.2 },
  });
  assert.deepEqual(
    projected.topics.map(({ id, percent }) => ({ id, percent })),
    [
      { id: "pet_science", percent: 60 },
      { id: "cute_drawing", percent: 20 },
      { id: "exploration", percent: 20 },
    ],
  );
});
