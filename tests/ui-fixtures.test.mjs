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

test("single-owner actions do not require redundant confirmation checkboxes", async () => {
  const paths = [
    "../src/App.jsx",
    "../src/features/passport/PassportSpreads.jsx",
    "../src/features/passport/InstagramImportDesk.jsx",
    "../src/features/agent/AgentSpread.jsx",
    "../src/features/agent/ConnectedAgentDesk.jsx",
    "../src/features/agent/FeedEvidenceDesk.jsx",
  ];
  const sources = await Promise.all(paths.map((path) => readFile(new URL(path, import.meta.url), "utf8")));
  const combined = sources.join("\n");

  assert.doesNotMatch(combined, /\bapprovalChecked\b|\bsetApprovalChecked\b|\bfeedEvidenceApproved\b/);
  assert.doesNotMatch(combined, /className="consent-check"|className="instagram-import-consent"/);
  assert.doesNotMatch(combined, /I approve|CONSENT REQUIRED|APPROVE AND APPLY|APPROVE LAB CORRECTION/);
  assert.match(combined, /RUN BOUNDED MISSION/);
  assert.match(combined, /RUN EXACT PLAN ONCE/);
  assert.match(combined, /ADD SELECTED TO PASSPORT/);
});

test("direct run buttons retain server-issued one-time execution tokens", async () => {
  const source = await readFile(new URL("../src/apiClient.js", import.meta.url), "utf8");
  assert.match(source, /live-commissions\/\$\{encodeURIComponent\(commissionId\)\}\/approval/);
  assert.match(source, /missions\/\$\{encodeURIComponent\(missionId\)\}\/approval/);
  assert.match(source, /approval_token: approval\.approval_token \|\| approval\.token/);
});

test("local model waiting state explains progress without implying execution", async () => {
  const [source, styles] = await Promise.all([
    readFile(new URL("../src/features/agent/AgentSpread.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/styles/agent-mission.css", import.meta.url), "utf8"),
  ]);

  assert.match(source, /Local model proposal in progress/);
  assert.match(source, /Read sealed inputs/);
  assert.match(source, /Run local inference/);
  assert.match(source, /Collect typed proposal/);
  assert.match(source, /Validate policy envelope/);
  assert.match(source, /No social account, public network, or control twin can be changed/);
  assert.match(styles, /@keyframes mission-working-stage/);
  assert.match(styles, /@media \(prefers-reduced-motion: reduce\)/);
});
