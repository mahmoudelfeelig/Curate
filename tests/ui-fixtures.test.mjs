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
    assert.match(creator.evidence, /reviewed creator directory/i);
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
    "../src/features/workflows/WorkflowSpreads.jsx",
  ];
  const sources = await Promise.all(paths.map((path) => readFile(new URL(path, import.meta.url), "utf8")));
  const combined = sources.join("\n");

  assert.doesNotMatch(combined, /\bapprovalChecked\b|\bsetApprovalChecked\b|\bfeedEvidenceApproved\b/);
  assert.doesNotMatch(combined, /className="consent-check"|className="instagram-import-consent"/);
  assert.doesNotMatch(combined, /I approve|CONSENT REQUIRED|APPROVE AND APPLY|APPROVE LAB CORRECTION/);
  assert.match(combined, /START PRACTICE RUN/);
  assert.match(combined, /APPLY THESE CHANGES ONCE/);
  assert.match(combined, /ADD SELECTED TO PASSPORT/);
  assert.doesNotMatch(combined, /second-person-confirm|Use the second local test persona/);
});

test("direct run buttons retain server-issued one-time execution tokens", async () => {
  const source = await readFile(new URL("../src/apiClient.js", import.meta.url), "utf8");
  assert.match(source, /live-commissions\/\$\{encodeURIComponent\(commissionId\)\}\/approval/);
  assert.match(source, /missions\/\$\{encodeURIComponent\(missionId\)\}\/approval/);
  assert.match(source, /approval_token: approval\.approval_token \|\| approval\.token/);
});

test("Curate uses a concise animated waiting state", async () => {
  const [source, styles] = await Promise.all([
    readFile(new URL("../src/features/agent/AgentSpread.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/styles/agent-mission.css", import.meta.url), "utf8"),
  ]);

  assert.match(source, /Curate is making a practice plan/);
  assert.match(source, /Curate is making the route/);
  assert.doesNotMatch(source, /Read sealed inputs|Run local inference|Collect typed proposal|Validate policy envelope/);
  assert.match(styles, /curate-agent-wait/);
  assert.match(styles, /@media \(prefers-reduced-motion: reduce\)/);
});
