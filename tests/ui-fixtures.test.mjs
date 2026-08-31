import assert from "node:assert/strict";
import test from "node:test";

import { CREATOR_FIXTURES, INITIAL_RECEIPTS } from "../src/data.js";

test("seeded history never advertises rollback without inverse state", () => {
  assert.equal(INITIAL_RECEIPTS.length > 0, true);
  assert.deepEqual(
    INITIAL_RECEIPTS.filter((receipt) => receipt.reversible).map((receipt) => receipt.id),
    [],
  );
});

test("creator cards identify their synthetic directory evidence honestly", () => {
  assert.equal(CREATOR_FIXTURES.length > 0, true);
  for (const creator of CREATOR_FIXTURES) {
    assert.match(creator.evidence, /reviewed directory fixture/i);
    assert.doesNotMatch(creator.evidence, /verified/i);
  }
});
