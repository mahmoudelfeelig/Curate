import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
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

test("the Visa authorization form owns its Bluesky handle state", async () => {
  const source = await readFile(
    new URL("../src/features/passport/PassportSpreads.jsx", import.meta.url),
    "utf8",
  );
  const visaStart = source.indexOf("export function VisaSpread(");
  const visaBody = source.slice(visaStart);

  assert.notEqual(visaStart, -1);
  assert.match(visaBody, /const \[blueskyHandle, setBlueskyHandle\] = useState\(""\);/);
  assert.doesNotMatch(source.slice(0, visaStart), /\bblueskyHandle\b/);
});

test("the Visa authorization notice stays scoped to its provider", async () => {
  const source = await readFile(
    new URL("../src/features/passport/PassportSpreads.jsx", import.meta.url),
    "utf8",
  );
  const visaStart = source.indexOf("export function VisaSpread(");
  const visaBody = source.slice(visaStart);

  assert.notEqual(visaStart, -1);
  assert.match(visaBody, /connectionNotice\?\.platform === destination\.id/);
  assert.match(visaBody, /connectionNotice\.message/);
});
