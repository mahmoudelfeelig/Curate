import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import test from "node:test";

import { BrowserOidcSession, oidcConfigFromEnv } from "./browserOidc.js";

const ENVIRONMENT = {
  VITE_FEED_PASSPORT_OIDC_CLIENT_ID: "public-spa-client",
  VITE_FEED_PASSPORT_OIDC_HOSTED_UI_URL: "https://feed-passport.auth.eu-central-1.amazoncognito.com",
  VITE_FEED_PASSPORT_OIDC_ISSUER: "https://cognito-idp.eu-central-1.amazonaws.com/eu-central-1_example",
};

function harness({ now = 1_800_000_000_000 } = {}) {
  const records = new Map();
  const navigations = [];
  const replacements = [];
  const requests = [];
  const location = {
    origin: "http://127.0.0.1:5173",
    pathname: "/",
    search: "",
    hash: "#visas",
    assign: (url) => navigations.push(url),
  };
  const storage = {
    getItem: (key) => records.get(key) ?? null,
    setItem: (key, value) => records.set(key, value),
    removeItem: (key) => records.delete(key),
  };
  const session = new BrowserOidcSession({
    environment: ENVIRONMENT,
    location,
    history: { replaceState: (...values) => replacements.push(values) },
    storage,
    cryptoImpl: webcrypto,
    now: () => now,
    navigate: (url) => navigations.push(url),
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return {
        ok: true,
        json: async () => ({
          token_type: "Bearer",
          access_token: "short-lived-access-token-value",
          expires_in: 3600,
        }),
      };
    },
  });
  return { session, location, records, navigations, replacements, requests };
}

test("OIDC is opt-in and rejects partial configuration", () => {
  assert.deepEqual(oidcConfigFromEnv({}, { origin: "http://127.0.0.1:5173" }), { required: false });
  const invalid = new BrowserOidcSession({
    environment: { VITE_FEED_PASSPORT_OIDC_CLIENT_ID: "only-one-field" },
    location: { origin: "http://127.0.0.1:5173" },
  }).snapshot();
  assert.equal(invalid.required, true);
  assert.equal(invalid.configured, false);
  assert.match(invalid.error, /configured together/i);
});

test("sign-in creates a one-time S256 PKCE transaction without storing tokens", async () => {
  const value = harness();
  await value.session.signIn({ returnTo: "#agent" });
  assert.equal(value.navigations.length, 1);
  const authorization = new URL(value.navigations[0]);
  assert.equal(authorization.pathname, "/oauth2/authorize");
  assert.equal(authorization.searchParams.get("response_type"), "code");
  assert.equal(authorization.searchParams.get("code_challenge_method"), "S256");
  assert.match(authorization.searchParams.get("code_challenge"), /^[A-Za-z0-9_-]{43}$/);
  const stored = JSON.parse([...value.records.values()][0]);
  assert.equal(stored.returnHash, "#agent");
  assert.equal("access_token" in stored, false);
  assert.equal("client_secret" in stored, false);
});

test("callback consumes state, exchanges the code, and retains the access token in memory only", async () => {
  const value = harness();
  await value.session.signIn({ returnTo: "#visas" });
  const authorization = new URL(value.navigations[0]);
  value.location.pathname = "/auth/callback";
  value.location.search = `?code=one-time-code&state=${authorization.searchParams.get("state")}`;
  await value.session.initialize();
  assert.equal(value.requests.length, 1);
  assert.equal(value.requests[0].options.credentials, "omit");
  assert.equal(value.requests[0].options.body.get("code_verifier").length >= 43, true);
  assert.equal(value.session.getAccessToken(), "short-lived-access-token-value");
  assert.equal(value.records.size, 0);
  assert.deepEqual(value.replacements[0].slice(1), ["", "/#visas"]);
});

test("a mismatched callback state fails closed before the token endpoint", async () => {
  const value = harness();
  await value.session.signIn();
  value.location.pathname = "/auth/callback";
  value.location.search = "?code=one-time-code&state=attacker-state";
  await value.session.initialize();
  assert.equal(value.requests.length, 0);
  assert.equal(value.session.getAccessToken(), "");
  assert.match(value.session.snapshot().error, /did not match/i);
  assert.equal(value.records.size, 0);
});

test("sign-out clears the in-memory token and uses the configured allowlisted logout URI", async () => {
  const value = harness();
  await value.session.signIn();
  const authorization = new URL(value.navigations[0]);
  value.location.pathname = "/auth/callback";
  value.location.search = `?code=one-time-code&state=${authorization.searchParams.get("state")}`;
  await value.session.initialize();
  value.session.signOut();
  assert.equal(value.session.getAccessToken(), "");
  const logout = new URL(value.navigations.at(-1));
  assert.equal(logout.pathname, "/logout");
  assert.equal(logout.searchParams.get("logout_uri"), "http://127.0.0.1:5173/");
});
