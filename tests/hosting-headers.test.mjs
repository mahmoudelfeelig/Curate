import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const headers = fs.readFileSync(new URL("../public/_headers", import.meta.url), "utf8");

test("Cloudflare Pages locks Curate to its required browser origins", () => {
  assert.match(headers, /default-src 'self'/);
  assert.match(headers, /frame-ancestors 'none'/);
  assert.match(headers, /object-src 'none'/);
  assert.match(headers, /https:\/\/curate-api\.elfeel\.me/);
  assert.doesNotMatch(headers, /https:\/\/api\.curate\.elfeel\.me/);
  assert.match(headers, /feed-passport-87e9c75ecda1\.auth\.eu-north-1\.amazoncognito\.com/);
  assert.match(headers, /feed-passport-gateway-mtwxhzeqqa\.gateway\.bedrock-agentcore\.eu-north-1\.amazonaws\.com/);
  assert.doesNotMatch(headers, /connect-src[^\n]*\*/);
});
