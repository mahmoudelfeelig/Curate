# Feed Passport AT Protocol OAuth sidecar

This service keeps AT Protocol OAuth tokens, refresh tokens, DPoP keys, and OAuth
client private keys outside the Python application and outside every agent/model
context. It uses the official Node OAuth implementation for PAR, PKCE, DPoP,
token refresh, issuer/DID validation, and client authentication. App passwords are
not accepted anywhere in this package.

The sidecar is intentionally fail-closed. Constructing the production client
requires explicit state storage, session storage, a refresh lock, a confidential
client keyset, owner-binding stores, and an authenticated internal caller. There
is no ambient filesystem credential discovery and no fallback to an anonymous or
password flow.

## Pinned official packages

The package manifest pins exact versions so a breaking OAuth change cannot arrive
during a demo build:

- `@atproto/oauth-client-node` `0.5.4`
- `@atproto/jwk-jose` `0.2.4`
- `@atproto/api` `0.20.42`

The implementation follows the official
[`@atproto/oauth-client-node` backend configuration](https://github.com/bluesky-social/atproto/tree/main/packages/oauth/oauth-client-node).
`NodeOAuthClient` owns authorization, callback validation, restore, refresh,
revocation, and DPoP state. `JoseKey.fromImportable` loads externally supplied
confidential-client keys. `Agent` performs the bounded authenticated operations,
so the sidecar does not hand-roll DPoP proofs.

Node.js 22 or newer is required by the upstream reference implementation.

## Boundary and request flow

The browser never talks directly to the private sidecar endpoints. Feed Passport
authenticates the signed-in user, then calls the sidecar over a private interface
with an internal service credential and the canonical owner ID.

During connection, Feed Passport creates an application state and passes it as
`appState` to `NodeOAuthClient.authorize`. The official client independently
generates the OAuth protocol state. Its injected state-store seam atomically
persists the official PKCE/DPoP state and moves the owner binding from the
application-state digest to the protocol-state digest. Both raw state values are
inside the encrypted snapshot. Owner-binding records use digest indexes; the
official state store uses its required raw protocol-state key, still inside that
encrypted snapshot. This works for both direct authorization URLs and PAR URLs,
where the protocol state is not present in the browser URL. The public callback
page forwards the callback query
to the authenticated Feed Passport backend, which claims the raw protocol state
for the same owner before the official callback and then compares the official
callback's returned `appState`. Connection binding, token-free callback receipt
creation, and state deletion are one durable commit, so a lost HTTP response can
be replayed without exchanging the authorization code twice.

After callback, the API receives only an opaque connection reference and the
account DID. To perform work, Python restores a short-lived opaque lease and asks
the sidecar to execute an allowlisted operation. The official session and its
DPoP material remain in Node for the full request. This is the intended adapter
for the Python `CredentialProvider` boundary: a sidecar-backed transport uses the
opaque `connection_ref` and `lease_ref`; it must not construct an
`OAuthCredentialLease` containing an AT Protocol access token.

The currently allowed bridge operations are:

- observe follows and mutes;
- follow or remove a follow record owned by the connected DID;
- mute or unmute an actor;
- observe only the muted-word preference needed by this product;
- merge a muted-word preference into the full provider-side preference list only
  when the caller supplies the digest from a fresh observation, preserving
  unrelated preferences without moving them across the credential boundary.

The official redirect URI remains
`https://<public-sidecar>/oauth/atproto/callback`. That public GET endpoint
validates one allowlisted OAuth result, rejects duplicates, controls, unknown
keys, and oversized queries, then returns a `no-store`, `no-referrer` 302 to the
configured Feed Passport app `/oauth/callback`. The app forwards the canonical
query to the authenticated Python callback route. The relay target is fixed at
startup and cannot be supplied by a request.

Posting, liking, reposting, commenting, messaging, blocking, account management,
arbitrary XRPC, arbitrary URLs, and raw HTTP proxying are unavailable by design.

## HTTP contract

Public endpoints (health, metadata, and JWKS are cacheable; the callback is
`no-store` and `no-referrer`):

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Sidecar process readiness only |
| `GET` | `/oauth/atproto/client-metadata.json` | OAuth client metadata; this URL is the `client_id` |
| `GET` | `/oauth/atproto/jwks.json` | Public client JSON Web Key Set |
| `GET` | `/oauth/atproto/callback` | Strict OAuth-result relay to the configured app callback |

Authenticated, `no-store` JSON endpoints:

| Method | Path | Request |
| --- | --- | --- |
| `POST` | `/v1/oauth/atproto/start` | `{ "handle": "name.example" }` |
| `POST` | `/v1/oauth/atproto/callback` | `{ "query": "code=...&state=...&iss=..." }` |
| `POST` | `/v1/oauth/atproto/sessions/restore` | `{ "connection_ref": "..." }` |
| `POST` | `/v1/oauth/atproto/sessions/execute` | `{ "lease_ref": "...", "operation": "...", "input": {} }` |
| `POST` | `/v1/oauth/atproto/sessions/revoke` | `{ "connection_ref": "..." }` |

The included shared-secret authenticator expects an internal bearer credential in
`Authorization` and the canonical owner in `X-Feed-Passport-Owner`. It is suitable
only behind a private network boundary. A deployment can inject an mTLS or
workload-identity authenticator instead. The service secret is not an AT Protocol
credential and must still be stored in a secret manager, never logged or sent to
a model.

All private requests must be `application/json` and are capped at 64 KiB. Incoming
JSON containing access-token, refresh-token, DPoP, authorization, cookie,
client-secret, or private-key fields is rejected. Each operation has a separate,
bounded output projector instead of a generic external-response pass-through;
enumerable credential values known to the restored session are also rejected if
a provider echoes them under an otherwise valid field. Muted words and pagination
cursors are necessarily provider-controlled user data, so callers must still
treat them as untrusted content rather than as proof of credential
non-interference from a malicious PDS. JWKS are refused if any private RSA, EC,
or symmetric key parameter is present.

## Construction contract

`createOfficialOAuthClient` requires:

```js
const oauthClient = await createOfficialOAuthClient({
  client: {
    clientId: 'https://app.example/oauth/atproto/client-metadata.json',
    metadataUri: 'https://app.example/oauth/atproto/client-metadata.json',
    clientUri: 'https://app.example/',
    jwksUri: 'https://app.example/oauth/atproto/jwks.json',
    redirectUri: 'https://app.example/oauth/atproto/callback',
    name: 'Feed Passport',
    signingAlgorithm: 'ES256',
    scope: 'atproto transition:generic',
  },
  stateStore,
  sessionStore,
  ownerStates,
  requestLock,
  privateKeys: [{ kid: 'oauth-2026-09', importable: privateKeyFromSecretManager }],
})
```

`createAtprotoSidecarRuntime` composes that official client with the owner-state,
connection, lease, operation, authentication, Fetch handler, and Node request
listener boundaries. It accepts every dependency explicitly and has no default
credential or storage lookup. The returned `nodeRequestListener` can be passed to
`node:http.createServer`; TLS or a private reverse proxy remains the deployment
owner's responsibility.

`stateStore` and `sessionStore` implement the upstream asynchronous `set`, `get`,
and `del` contract. The runnable server uses the included AES-256-GCM encrypted,
atomic, restart-safe file store for official OAuth state/session data, owner
state, callback receipts, opaque connection lifecycle state, and lease metadata.
Expired transient records are pruned, individual records and the total snapshot
are bounded, abandoned official OAuth state expires after 15 minutes, each owner
may have at most eight pending starts, and a connection has at most one active
lease. Its encryption key is supplied externally and is never written beside the
ciphertext. The session store contains refresh tokens and DPoP private keys, so
the store path must be a persistent volume and its key must be backed up
separately.

The durable store intentionally supports one sidecar process per store path. It
holds an exclusive process lock, serializes stale-lock recovery, performs atomic
replacement, recovers stale or interrupted lock markers, and restores official
sessions lazily after restart. It is not a
multi-replica database; horizontal deployment requires a shared encrypted store
and distributed refresh lock that implement the same interfaces.

`InMemorySecretStore`, `InMemoryOwnerStateStore`,
`InMemoryOwnerConnectionStore`, and `InMemorySessionLeaseStore` are provided for
isolated tests only. They are not restart-safe and are not used by `npm start`.

The confidential key must be generated outside the repository and supplied from
a secret manager or read-only mounted secret. Never place PEM, JWK private
parameters, internal service credentials, OAuth state/session data, or account
tokens in environment examples, source files, CI output, screenshots, or issue
attachments.

## Local verification

The test suite uses both isolated in-memory helpers and the encrypted restart-safe
store with a mocked `NodeOAuthClient`-compatible object and operation executor. It
covers callback response-loss replay, interrupted processing, lifecycle races,
lock recovery, bounded storage, and exact public operation schemas. It makes no
network or account calls and needs no platform credentials:

```sh
cd services/atproto-oauth
npm test
npm run check
```

## Runnable local server

`npm start` loads configuration only from explicit environment values, opens the
durable encrypted store, initializes the official OAuth client, listens on the
configured host/port, and releases the HTTP server and store lock on SIGINT or
SIGTERM. Required values are:

- `FEED_PASSPORT_ATPROTO_PUBLIC_ORIGIN` (HTTPS origin);
- `FEED_PASSPORT_ATPROTO_APP_CALLBACK_URI` (HTTPS, or loopback HTTP for local
  development, ending exactly `/oauth/callback`);
- `FEED_PASSPORT_ATPROTO_STORE_PATH` (absolute persistent path);
- `FEED_PASSPORT_ATPROTO_STORE_KEY_B64` (URL-safe base64 for exactly 32 bytes);
- `FEED_PASSPORT_ATPROTO_SIDECAR_INTERNAL_SECRET` (32–4096 characters);
- `FEED_PASSPORT_ATPROTO_PRIVATE_KEY_FILE` (absolute mounted key path);
- `FEED_PASSPORT_ATPROTO_PRIVATE_KEY_ID`.

Host, port, store key ID, client URI/name, signing algorithm, scope, policy URI,
and terms URI have strict optional environment overrides in `src/config.mjs`.
The Python service uses the same internal secret plus
`FEED_PASSPORT_ATPROTO_SIDECAR_URL` and
`FEED_PASSPORT_ATPROTO_APP_CALLBACK_URI`; it stores only the opaque connection
reference in its encrypted connection registry.

The Dockerfile and installation command intentionally require a registry-derived
`package-lock.json`. Until that lock is generated with registry access, the
packaging gate is incomplete; do not invent integrity hashes. Once the real lock
is committed, the deterministic sequence is:

```sh
npm ci
npm run check
npm test
npm start
```

Performing a live OAuth callback remains a separate explicit gate. It requires a
public HTTPS metadata/JWKS/callback origin, the externally generated client key,
an authorized dummy account, and approval to contact that account's PDS. None of
the local tests make network or account calls.

There are two explicit crash boundaries. If the process dies after the official
callback persists a session but before the atomic connection/receipt commit, the
transaction remains `processing` and a fresh authorization is required. During
revocation, the sidecar calls the pinned official server request primitive because
the official convenience `revoke` method intentionally suppresses provider
errors. A successful provider response is durably recorded on the owner-bound
connection before the local session is deleted. A crash after that receipt is
recovered by local cleanup and terminalization without another provider claim.
A crash or network failure before the receipt leaves the connection `revoking`
and operations blocked; a missing official session is never treated as provider
proof, so an unknown outcome remains conservative and may require dummy-account
inspection.
