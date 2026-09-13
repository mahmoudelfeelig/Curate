const TRANSACTION_KEY = "feed-passport-oidc-transaction";
const CALLBACK_PATH = "/auth/callback";
const CLOCK_SKEW_MS = 30_000;

function base64Url(bytes) {
  let binary = "";
  for (const value of bytes) binary += String.fromCharCode(value);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function decodeJwtPayload(token) {
  const segments = String(token || "").split(".");
  if (segments.length !== 3 || segments.some((segment) => !/^[A-Za-z0-9_-]+$/.test(segment))) {
    throw new Error("The identity provider returned an invalid access token");
  }
  try {
    const encoded = segments[1].replaceAll("-", "+").replaceAll("_", "/");
    const padded = encoded.padEnd(Math.ceil(encoded.length / 4) * 4, "=");
    const bytes = Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
    const claims = JSON.parse(new TextDecoder().decode(bytes));
    if (!claims || typeof claims !== "object" || Array.isArray(claims)) throw new Error();
    return claims;
  } catch {
    throw new Error("The identity provider returned an invalid access token");
  }
}

function safeUrl(value, label, { allowLoopbackHttp = false } = {}) {
  let url;
  try {
    url = new URL(String(value));
  } catch {
    throw new Error(`${label} must be an absolute URL`);
  }
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (url.protocol !== "https:" && !(allowLoopbackHttp && loopback && url.protocol === "http:")) {
    throw new Error(`${label} must use HTTPS${allowLoopbackHttp ? " or loopback HTTP" : ""}`);
  }
  if (url.username || url.password || url.hash) {
    throw new Error(`${label} cannot contain credentials or a fragment`);
  }
  return url;
}

function exactCallbackUrl(value, location) {
  const fallback = `${location.origin}${CALLBACK_PATH}`;
  const url = safeUrl(value || fallback, "OIDC redirect URI", { allowLoopbackHttp: true });
  if (url.search) throw new Error("OIDC redirect URI cannot contain a query");
  return url.toString();
}

export function oidcConfigFromEnv(environment = {}, location = globalThis.location) {
  const clientId = String(
    environment.VITE_CURATE_OIDC_CLIENT_ID
      || environment.VITE_FEED_PASSPORT_OIDC_CLIENT_ID
      || "",
  ).trim();
  const hostedUi = String(
    environment.VITE_CURATE_OIDC_HOSTED_UI_URL
      || environment.VITE_FEED_PASSPORT_OIDC_HOSTED_UI_URL
      || "",
  ).trim();
  const issuer = String(
    environment.VITE_CURATE_OIDC_ISSUER
      || environment.VITE_FEED_PASSPORT_OIDC_ISSUER
      || "",
  ).trim();
  const anyConfigured = Boolean(clientId || hostedUi || issuer);
  if (!anyConfigured) return Object.freeze({ required: false });
  if (!clientId || !hostedUi || !issuer) {
    throw new Error("OIDC client ID, hosted UI URL, and issuer must be configured together");
  }
  if (!/^[A-Za-z0-9._-]{3,128}$/.test(clientId)) throw new Error("OIDC client ID is invalid");
  const hosted = safeUrl(hostedUi, "OIDC hosted UI URL");
  hosted.pathname = hosted.pathname.replace(/\/$/, "");
  hosted.search = "";
  const issuerUrl = safeUrl(issuer, "OIDC issuer URL");
  issuerUrl.pathname = issuerUrl.pathname.replace(/\/$/, "");
  issuerUrl.search = "";
  const redirectUri = exactCallbackUrl(
    environment.VITE_CURATE_OIDC_REDIRECT_URI
      || environment.VITE_FEED_PASSPORT_OIDC_REDIRECT_URI,
    location,
  );
  const logoutUri = safeUrl(
    environment.VITE_CURATE_OIDC_LOGOUT_URI
      || environment.VITE_FEED_PASSPORT_OIDC_LOGOUT_URI
      || `${location.origin}/`,
    "OIDC logout URI",
    { allowLoopbackHttp: true },
  ).toString();
  const scopes = String(
    environment.VITE_CURATE_OIDC_SCOPES
      || environment.VITE_FEED_PASSPORT_OIDC_SCOPES
      || "openid feed-passport/invoke",
  )
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (!scopes.includes("openid")) throw new Error("OIDC scopes must include openid");
  return Object.freeze({
    required: true,
    clientId,
    issuer: issuerUrl.toString(),
    authorizationEndpoint: new URL("/oauth2/authorize", hosted).toString(),
    tokenEndpoint: new URL("/oauth2/token", hosted).toString(),
    logoutEndpoint: new URL("/logout", hosted).toString(),
    redirectUri,
    logoutUri,
    scope: scopes.join(" "),
  });
}

export class BrowserOidcSession {
  constructor({
    environment = import.meta.env || {},
    location = globalThis.location,
    history = globalThis.history,
    storage = globalThis.sessionStorage,
    fetchImpl = globalThis.fetch?.bind(globalThis),
    cryptoImpl = globalThis.crypto,
    now = () => Date.now(),
    navigate = (url) => location.assign(url),
  } = {}) {
    this.location = location;
    this.history = history;
    this.storage = storage;
    this.fetch = fetchImpl;
    this.crypto = cryptoImpl;
    this.now = now;
    this.navigate = navigate;
    this.listeners = new Set();
    this.token = "";
    this.expiresAt = 0;
    this.subject = "";
    this.revision = 0;
    this.error = "";
    try {
      this.config = oidcConfigFromEnv(environment, location);
    } catch (error) {
      this.config = Object.freeze({ required: true, invalid: true });
      this.error = error.message;
    }
  }

  snapshot() {
    return Object.freeze({
      required: this.config.required,
      configured: this.config.required && !this.config.invalid,
      authenticated: Boolean(this.getAccessToken({ notify: false })),
      subject: this.getSubject({ notify: false }) || null,
      issuer: this.config.issuer || null,
      error: this.error || null,
      revision: this.revision,
    });
  }

  subscribe(listener) {
    if (typeof listener !== "function") throw new TypeError("OIDC listener must be a function");
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  async initialize() {
    if (!this.config.required || this.config.invalid) {
      this.#notify();
      return this.snapshot();
    }
    const path = String(this.location.pathname || "").replace(/\/$/, "");
    if (path.endsWith(CALLBACK_PATH)) {
      try {
        await this.#completeCallback();
      } catch {
        this.token = "";
        this.expiresAt = 0;
        this.subject = "";
        this.error = "The browser could not complete the sign-in callback safely. Start again.";
      }
    }
    this.#notify();
    return this.snapshot();
  }

  getAccessToken({ notify = true } = {}) {
    if (!this.token) return "";
    if (this.expiresAt <= this.now() + CLOCK_SKEW_MS) {
      this.token = "";
      this.expiresAt = 0;
      this.subject = "";
      this.error = "Your sign-in session expired. Sign in again to continue.";
      if (notify) this.#notify();
      return "";
    }
    return this.token;
  }

  getSubject({ notify = true } = {}) {
    return this.getAccessToken({ notify }) ? this.subject : "";
  }

  async signIn({ returnTo } = {}) {
    this.#requireReady();
    if (!this.crypto?.getRandomValues || !this.crypto?.subtle?.digest) {
      throw new Error("Secure browser cryptography is required for OIDC PKCE");
    }
    if (!this.storage) throw new Error("Session storage is required for the one-time OIDC transaction");
    const state = base64Url(this.crypto.getRandomValues(new Uint8Array(32)));
    const verifier = base64Url(this.crypto.getRandomValues(new Uint8Array(64)));
    const digest = await this.crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
    const challenge = base64Url(new Uint8Array(digest));
    const safeReturn = String(returnTo || this.location.hash || "#overview");
    const returnHash = /^#[a-z][a-z0-9-]{0,40}$/i.test(safeReturn) ? safeReturn : "#overview";
    this.storage.setItem(TRANSACTION_KEY, JSON.stringify({
      state,
      verifier,
      returnHash,
      createdAt: this.now(),
    }));
    const authorization = new URL(this.config.authorizationEndpoint);
    authorization.search = new URLSearchParams({
      response_type: "code",
      client_id: this.config.clientId,
      redirect_uri: this.config.redirectUri,
      scope: this.config.scope,
      state,
      code_challenge: challenge,
      code_challenge_method: "S256",
    }).toString();
    this.navigate(authorization.toString());
  }

  signOut() {
    if (!this.config.required || this.config.invalid) return;
    this.token = "";
    this.expiresAt = 0;
    this.subject = "";
    this.error = "";
    this.storage?.removeItem(TRANSACTION_KEY);
    this.#notify();
    const logout = new URL(this.config.logoutEndpoint);
    logout.search = new URLSearchParams({
      client_id: this.config.clientId,
      logout_uri: this.config.logoutUri,
    }).toString();
    this.navigate(logout.toString());
  }

  #requireReady() {
    if (!this.config.required) throw new Error("Browser OIDC is not configured");
    if (this.config.invalid) throw new Error(this.error || "Browser OIDC configuration is invalid");
  }

  async #completeCallback() {
    let transaction;
    try {
      transaction = JSON.parse(this.storage?.getItem(TRANSACTION_KEY) || "null");
    } catch {
      transaction = null;
    }
    this.storage?.removeItem(TRANSACTION_KEY);
    const query = new URLSearchParams(this.location.search || "");
    const returnedState = query.get("state") || "";
    const errorCode = query.get("error") || "";
    const code = query.get("code") || "";
    const transactionFresh = Number.isFinite(transaction?.createdAt)
      && this.now() - transaction.createdAt >= 0
      && this.now() - transaction.createdAt <= 10 * 60_000;
    if (
      !transactionFresh
      || typeof transaction?.state !== "string"
      || transaction.state.length < 43
      || transaction.state !== returnedState
    ) {
      this.error = "The sign-in callback did not match a fresh browser transaction. Start again.";
      this.#clearCallbackUrl("#overview");
      return;
    }
    if (errorCode) {
      this.error = "Sign-in was declined or rejected by the identity provider.";
      this.#clearCallbackUrl(transaction.returnHash);
      return;
    }
    if (!code || typeof transaction.verifier !== "string" || transaction.verifier.length < 43) {
      this.error = "The sign-in callback was incomplete. Start again.";
      this.#clearCallbackUrl(transaction.returnHash);
      return;
    }
    if (!this.fetch) {
      this.error = "The browser cannot reach the configured identity token endpoint.";
      this.#clearCallbackUrl(transaction.returnHash);
      return;
    }
    try {
      const response = await this.fetch(this.config.tokenEndpoint, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "authorization_code",
          client_id: this.config.clientId,
          redirect_uri: this.config.redirectUri,
          code,
          code_verifier: transaction.verifier,
        }),
        credentials: "omit",
      });
      const payload = await response.json().catch(() => ({}));
      const expiresIn = Number(payload.expires_in);
      const claims = decodeJwtPayload(payload.access_token);
      const scopes = typeof claims.scope === "string" ? claims.scope.split(/\s+/) : [];
      const expectedScopes = this.config.scope.split(/\s+/).filter((scope) => scope !== "openid");
      const subject = typeof claims.sub === "string" ? claims.sub.trim() : "";
      const jwtExpiresAt = Number(claims.exp) * 1000;
      if (
        !response.ok
        || payload.token_type !== "Bearer"
        || typeof payload.access_token !== "string"
        || payload.access_token.length < 20
        || !Number.isFinite(expiresIn)
        || expiresIn <= 0
        || expiresIn > 86_400
        || claims.iss !== this.config.issuer.replace(/\/$/, "")
        || claims.client_id !== this.config.clientId
        || claims.token_use !== "access"
        || expectedScopes.some((scope) => !scopes.includes(scope))
        || !subject
        || subject.length > 160
        || subject.includes("\0")
        || !Number.isFinite(jwtExpiresAt)
        || jwtExpiresAt <= this.now() + CLOCK_SKEW_MS
      ) {
        throw new Error("The identity provider did not return a valid short-lived bearer token");
      }
      this.token = payload.access_token;
      this.expiresAt = Math.min(this.now() + expiresIn * 1000, jwtExpiresAt);
      this.subject = subject;
      this.error = "";
    } catch (error) {
      this.token = "";
      this.expiresAt = 0;
      this.subject = "";
      this.error = `Sign-in could not be completed: ${error.message}`;
    } finally {
      this.#clearCallbackUrl(transaction.returnHash);
    }
  }

  #clearCallbackUrl(returnHash) {
    const callback = new URL(this.config.redirectUri);
    const rootPath = callback.pathname.replace(/auth\/callback\/?$/, "");
    this.history?.replaceState(
      { feedPassportSection: String(returnHash || "#overview").slice(1) },
      "",
      `${rootPath || "/"}${returnHash || "#overview"}`,
    );
  }

  #notify() {
    this.revision += 1;
    const value = this.snapshot();
    for (const listener of this.listeners) listener(value);
  }
}

export const browserOidcSession = new BrowserOidcSession();
