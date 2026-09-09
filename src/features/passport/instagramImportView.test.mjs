import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  applyingInstagramImportSession,
  expiredInstagramImportSession,
  formatInstagramImportField,
  formatInstagramImportWarning,
  instagramImportExpiryDelay,
  instagramImportPresentation,
  scheduleInstagramImportExpiry,
  shortInstagramDigest,
} from "./instagramImportView.js";

const SOURCE_SHA256 = "a".repeat(64);
const READY_NOW = Date.parse("2026-09-08T12:05:00+00:00");

function readySession(overrides = {}) {
  return {
    session_id: "igimp_owner_bound_random_session",
    status: "ready",
    created_at: "2026-09-08T12:00:00+00:00",
    expires_at: "2026-09-08T12:15:00+00:00",
    source_sha256: SOURCE_SHA256,
    parser_id: "meta.instagram.relationships_following.v1",
    source_relationship_count: 4,
    accepted_relationship_count: 2,
    duplicate_relationship_count: 1,
    rejected_relationship_count: 1,
    ignored_archive_entry_count: 3,
    warnings: ["ignored_archive_entries:3", "duplicate_relationships:1"],
    observed_fields: ["followed_creators"],
    unobserved_fields: ["topic_distribution", "recommendation_state"],
    followed_handles: ["city_zine", "paper.lab"],
    selection_limit: 17,
    raw_source_retained: false,
    platform_account_accessed: false,
    ...overrides,
  };
}

test("projects the exact ready preview contract without inventing topics", () => {
  const view = instagramImportPresentation(readySession(), READY_NOW);

  assert.equal(view.phase, "ready");
  assert.equal(view.label, "READY");
  assert.equal(view.sessionId, "igimp_owner_bound_random_session");
  assert.equal(view.sourceSha256, SOURCE_SHA256);
  assert.deepEqual(view.followedHandles, ["city_zine", "paper.lab"]);
  assert.deepEqual(view.observedFields, ["followed_creators"]);
  assert.deepEqual(view.unobservedFields, ["topic_distribution", "recommendation_state"]);
  assert.equal(view.selectionLimit, 17);
  assert.equal(view.hasCapacityHint, true);
  assert.equal("topics" in view, false);
});

test("does not accept historical id, digest, or handle aliases", () => {
  const legacy = readySession();
  legacy.id = legacy.session_id;
  legacy.source_digest = legacy.source_sha256;
  legacy.handles = legacy.followed_handles;
  delete legacy.session_id;
  delete legacy.source_sha256;
  delete legacy.followed_handles;

  const view = instagramImportPresentation(legacy, READY_NOW);

  assert.equal(view.phase, "invalid");
  assert.equal(view.sessionId, "");
  assert.equal(view.sourceSha256, "");
  assert.deepEqual(view.followedHandles, []);
});

test("fails closed on malformed or internally inconsistent ready previews", () => {
  assert.equal(instagramImportPresentation(readySession({ followed_handles: ["BadHandle"] }), READY_NOW).phase, "invalid");
  assert.equal(instagramImportPresentation(readySession({ followed_handles: ["paper.lab", "paper.lab"] }), READY_NOW).phase, "invalid");
  assert.equal(instagramImportPresentation(readySession({ accepted_relationship_count: 1 }), READY_NOW).phase, "invalid");
  assert.equal(instagramImportPresentation(readySession({ expires_at: "not-a-date" }), READY_NOW).phase, "invalid");
  assert.equal(instagramImportPresentation(readySession({ selection_limit: 501 }), READY_NOW).phase, "invalid");
});

test("expires a ready preview at the exact server timestamp without projecting private handles", () => {
  const expiresAt = Date.parse("2026-09-08T12:15:00+00:00");
  const before = instagramImportPresentation(readySession(), expiresAt - 1);
  const expired = instagramImportPresentation(readySession(), expiresAt);

  assert.equal(before.phase, "ready");
  assert.deepEqual(before.followedHandles, ["city_zine", "paper.lab"]);
  assert.equal(expired.phase, "expired");
  assert.deepEqual(expired.followedHandles, []);
  assert.equal(expired.sessionId, "");
  assert.equal(expired.sourceSha256, "");
  assert.deepEqual(expiredInstagramImportSession(readySession()), {
    status: "expired",
    expires_at: "2026-09-08T12:15:00+00:00",
  });
});

test("schedules expiry from a deterministic clock and cancels the exact timer", () => {
  let scheduled;
  let cancelled;
  let expired = 0;
  const cleanup = scheduleInstagramImportExpiry({
    expiresAt: "2026-09-08T12:15:00+00:00",
    now: () => Date.parse("2026-09-08T12:14:59.250+00:00"),
    schedule: (callback, delay) => {
      scheduled = { callback, delay };
      return "timer-1";
    },
    cancel: (timerId) => { cancelled = timerId; },
    expire: () => { expired += 1; },
  });

  assert.equal(instagramImportExpiryDelay("2026-09-08T12:15:00+00:00", Date.parse("2026-09-08T12:14:59.999+00:00")), 1);
  assert.equal(instagramImportExpiryDelay("2026-09-08T12:15:00+00:00", Date.parse("2026-09-08T12:15:00+00:00")), 0);
  assert.equal(scheduled.delay, 750);
  assert.equal(expired, 0);
  scheduled.callback();
  assert.equal(expired, 1);
  cleanup();
  assert.equal(cancelled, "timer-1");
});

test("App lifetime wires expiry to a handle-clearing expired terminal", async () => {
  const source = await readFile(new URL("./InstagramImportDesk.jsx", import.meta.url), "utf8");
  const app = await readFile(new URL("../../App.jsx", import.meta.url), "utf8");

  assert.doesNotMatch(source, /scheduleInstagramImportExpiry\(\{/);
  assert.match(source, /view\.phase === "expired"/);
  assert.match(source, /service no longer accepts this session/i);
  assert.match(app, /handleInstagramImportExpire[\s\S]*expiredInstagramImportSession\(current\)[\s\S]*setInstagramImportSelection\(\[\]\)/);
  assert.match(app, /useEffect\(\(\) => \{[\s\S]*scheduleInstagramImportExpiry\(\{[\s\S]*handleInstagramImportExpire\(sessionId, expiresAt\)/);
});

test("redacts handles before apply and represents an interrupted response as outcome pending", async () => {
  const applying = applyingInstagramImportSession(readySession(), 2);
  assert.deepEqual(applying, {
    status: "applying",
    source_sha256: SOURCE_SHA256,
    selected_relationship_count: 2,
  });
  assert.equal(instagramImportPresentation(applying).phase, "applying");
  assert.deepEqual(instagramImportPresentation(applying).followedHandles, []);

  const app = await readFile(new URL("../../App.jsx", import.meta.url), "utf8");
  assert.match(app, /applyingInstagramImportSession\(instagramImport, selectedHandles\.length\)[\s\S]*instagramImportRef\.current = applying[\s\S]*setInstagramImportSelection\(\[\]\)[\s\S]*applyInstagramImport\(sessionId, selectedHandles\)/);
});

test("treats consumed and discarded summaries as redacted terminal states", () => {
  const common = {
    source_sha256: SOURCE_SHA256,
    source_relationship_count: 4,
    accepted_relationship_count: 2,
    selected_relationship_count: 1,
    duplicate_relationship_count: 1,
    rejected_relationship_count: 1,
  };
  const consumed = instagramImportPresentation({ status: "consumed", ...common });
  const discarded = instagramImportPresentation({ status: "discarded", ...common, selected_relationship_count: 0 });

  assert.equal(consumed.phase, "consumed");
  assert.equal(consumed.selectedRelationshipCount, 1);
  assert.deepEqual(consumed.followedHandles, []);
  assert.equal(consumed.sessionId, "");
  assert.equal(discarded.phase, "discarded");
  assert.deepEqual(discarded.followedHandles, []);
  assert.equal(instagramImportPresentation({ status: "applied", ...common }).phase, "invalid");
});

test("formats only redacted aggregate parser evidence", () => {
  assert.equal(shortInstagramDigest(SOURCE_SHA256), "aaaaaaaaaaaa…aaaaaaaa");
  assert.equal(formatInstagramImportWarning("duplicate_relationships:1"), "1 duplicate following record was merged.");
  assert.equal(formatInstagramImportWarning("rejected_relationships:2"), "2 malformed following records were rejected.");
  assert.equal(formatInstagramImportField("recommendation_state"), "recommendation state");
});
