# Zero-spend readiness and external setup

Feed Passport can be built, tested, and demonstrated account-free. External account control is a separate evidence gate. This page identifies what is complete locally, what only the account owner or platform can create, and what cannot be attempted while the requirement is zero spend.

No passing local test promotes a named platform. An unprefixed platform remains **Guided** until a fresh, signed conformance receipt proves an explicitly authorized dummy-account action against the exact Git revision being run.

## What is available without an account

The browser, Curator API, local Qwen/llama.cpp Strands agents, deterministic Feed Passport Lab, ten restricted platform twins, OAuth transaction tests, live-transport contract tests, recovery journal tests, AT Protocol sidecar tests, and AgentCore/CDK source validation can all run without a social account or AWS call.

The local twins are the complete safe demo for all ten named networks. They test agent planning, policy limits, consent, mutation budgets, re-observation, stop conditions, receipts, and rollback against deterministic control surfaces. They do not test a private recommendation ranker or contact the named network.

Run the local-only readiness inspection from the repository root:

```powershell
.\scripts\check-external-readiness.ps1
```

For machine-readable output:

```powershell
.\scripts\check-external-readiness.ps1 -AsJson
```

The checker reads only local command availability, files, and the current process environment. It does not load an `.env` file, read an OAuth token, open a database, inspect AWS profiles, contact an identity provider, refresh SSO, or call a social platform. It never prints environment values.

| Status | Meaning |
| --- | --- |
| `ready_local` | The local prerequisite was observed directly. |
| `configured_unverified` | Correctly shaped local configuration exists, but no external system was contacted. |
| `action_required` | A human/provider step or local installation is still needed. |
| `invalid` | A partial or malformed configuration would fail closed. |
| `zero_spend_blocked` | Completing this check can incur charges and is deliberately not attempted. |

`-FailOnActionRequired` returns exit code 2 if any action, invalid configuration, or spend-blocked item remains. The default informational run returns zero so missing external accounts do not break the account-free build.

## Secrets and access boundary

Never paste an access token, refresh token, OAuth client secret, AWS access key, SSO token, Cognito token, DPoP key, private JWK/PEM, HMAC key, password, cookie, or `.env` file into chat, a model prompt, an issue, a commit, a screenshot, or a demo recording.

The correct way to grant this project access is for the account owner to complete OAuth in their own browser. Feed Passport stores an encrypted token behind an opaque connection reference. A later operator or coding agent needs only the non-secret platform name, owner subject, connection ID, intended reversible targets, and explicit permission for that one dummy-account conformance run. It never needs the account password or token.

The repository ignores `.env`, private-key extensions, credential JSON, databases, and logs. The root `.env.example` is a names-only contract. Vite reads an untracked root `.env`; the Python API expects the values in its process environment or deployment secret injection. Production secrets belong in a secret manager or a read-only mounted secret.

Use separate random values for consent signing, connection metadata encryption, blind indexing, OAuth vault encryption, live-receipt signing, AT Protocol internal authentication, and any AT Protocol client key. Never reuse one secret for another purpose.

The two connection-registry keys are required for every external connection. The third OAuth-vault key is additionally required for YouTube, X, or Reddit; Bluesky tokens stay in the sidecar and do not use it. Generate independent values directly into the current PowerShell process without printing them:

```powershell
function New-FeedPassportKey {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

$env:FEED_PASSPORT_CONNECTION_KEY_B64 = New-FeedPassportKey
$env:FEED_PASSPORT_CONNECTION_INDEX_KEY_B64 = New-FeedPassportKey
$env:FEED_PASSPORT_OAUTH_VAULT_KEY_B64 = New-FeedPassportKey
```

Closing that shell discards these process values. A persistent deployment must inject them from its secret store; do not save them in the repository.

## Owner authentication

`FEED_PASSPORT_AUTH_MODE=demo` normally supports only the account-free loopback proof. It treats a caller-provided actor as a deterministic test principal and has no real user-identity boundary. As soon as an OAuth provider, connection store, AT Protocol bridge, or signed live adapter is configured, Curator therefore fails startup unless one of the two explicit paths below is selected.

The deployment-capable path is a real OIDC provider. Configure all of:

```text
FEED_PASSPORT_AUTH_MODE=oidc
FEED_PASSPORT_OIDC_ISSUER=https://exact-issuer.example
FEED_PASSPORT_OIDC_AUDIENCE=exact-api-audience-or-client-id
FEED_PASSPORT_OIDC_JWKS_URL=https://exact-issuer.example/path/to/jwks.json
FEED_PASSPORT_OIDC_REQUIRED_SCOPES=feed-passport/invoke
```

The browser includes Authorization Code + PKCE and an identity gate. Configure its public client without a browser secret:

```text
VITE_FEED_PASSPORT_OIDC_CLIENT_ID=exact-public-client-id
VITE_FEED_PASSPORT_OIDC_HOSTED_UI_URL=https://exact-hosted-ui.example
VITE_FEED_PASSPORT_OIDC_ISSUER=https://exact-issuer.example
VITE_FEED_PASSPORT_OIDC_REDIRECT_URI=http://127.0.0.1:5173/auth/callback
VITE_FEED_PASSPORT_OIDC_LOGOUT_URI=http://127.0.0.1:5173/
VITE_FEED_PASSPORT_OIDC_SCOPES=openid feed-passport/invoke
```

The browser derives `/oauth2/authorize`, `/oauth2/token`, and `/logout` from the hosted UI origin, so the provider must expose that Cognito-style endpoint contract. It keeps the short-lived access token in memory only. Session storage contains only the one-time PKCE verifier/state/return hash and is removed during callback; do not put a bearer token in a Vite variable, URL, localStorage, or sessionStorage.

The API accepts only RS256/ES256 bearer tokens with the exact issuer and audience/client ID, derives the owner from `sub`, and rejects conflicting body/query owners. OIDC configuration alone is not proof of a successful sign-in; verify the provider flow and one negative cross-owner request before any social OAuth callback.

On a verified user's first sign-in, the browser loads only that subject's state. If no Passport exists, it calls the authenticated onboarding endpoint; the server derives the owner from the verified `sub` and creates the first Passport without accepting a caller-selected owner. Later sign-ins restore only that same owner's Passport, connections, approvals, and receipts.

The identity callback is `/auth/callback`. The separate social-platform callback is `http://127.0.0.1:5173/oauth/callback`. The value in `VITE_FEED_PASSPORT_OAUTH_REDIRECT_URI`, every URI in `FEED_PASSPORT_OAUTH_REDIRECT_URIS`, and each social provider console entry must match byte for byte. Register the identity callback/logout values separately with the OIDC client. Use HTTPS for a non-loopback deployment.

For one-person dummy-account testing on the same machine, there is a deliberately insecure escape hatch when no OIDC tenant is available yet:

```text
FEED_PASSPORT_AUTH_MODE=demo
FEED_PASSPORT_ALLOW_INSECURE_LOOPBACK_OAUTH=1
FEED_PASSPORT_BIND_HOST=127.0.0.1
FEED_PASSPORT_ALLOWED_ORIGINS=http://127.0.0.1:5173,http://localhost:5173
```

This still requires the account owner's explicit approval for the exact dummy platform and mutation. Startup rejects a non-loopback bind or any non-loopback allowed origin, and request middleware rejects a non-loopback client IP. It does not authenticate separate users: caller-supplied actor IDs remain trusted test labels. Never use this override through a reverse proxy, tunnel, container ingress, LAN binding, shared browser, team demo, hosted environment, or production deployment. Set it back to `0` before enabling OIDC. Merely enabling the flag makes no provider or account request.

### Interrupted connection revocation

Standard OAuth revocation first persists the owner-bound connection as `revoking`, then asks the provider to revoke the credential. Provider confirmation is committed in the vault as an owner-, platform-, connection-, and credential-bound non-secret receipt in the same transaction that deletes the encrypted credential; the registry then records `revoked`. If the process stops after that vault transaction, retry consumes the receipt and completes the registry transition without calling the provider again. If the provider response cannot be confirmed, `revoking` is the safe durable state: it is not restored into any active live adapter and cannot silently resume account mutation.

Do not edit the database, delete the encrypted credential by hand, or describe `revoking` as confirmed provider revocation. Refresh the connection record, retry the same owner-authorized revoke with its current version, and let the provider plus local finalization complete. If a provider cannot confirm an idempotent retry, verify the authorization manually in that dummy account and leave the local record `revoking` until an evidence-backed recovery path is available.

## Google and YouTube

The implemented candidate surface observes subscriptions and can subscribe or unsubscribe from a channel. It does not read or write YouTube Home ranking, watch history, likes, comments, recommendation feedback, or hidden personalization state.

The account owner must complete these provider steps:

- Create a dedicated Google Cloud project and enable **YouTube Data API v3**.
- Configure the OAuth consent screen in testing mode and add only the dummy Google account as a test user.
- Create a **Web application** OAuth client with the exact Feed Passport callback URI.
- Create or select a YouTube channel for the dummy account. Do not use a personal account.
- Place the client ID and secret in the Curator process environment as `FEED_PASSPORT_YOUTUBE_OAUTH_CLIENT_ID` and `FEED_PASSPORT_YOUTUBE_OAUTH_CLIENT_SECRET`. The default client authentication mode is `client_secret_post`.
- Sign in to Feed Passport through OIDC, then complete Google OAuth in the account owner's browser. Do not send the resulting authorization code or token to another person or a model.

The requested scope is `https://www.googleapis.com/auth/youtube`. Google may require app verification for broader/public use; a testing-mode app with an explicitly added dummy test user is the narrow demo path. Provider quota availability and consent-screen status still need live confirmation.

Official references: [Google OAuth for server-side web apps](https://developers.google.com/identity/protocols/oauth2/web-server), [YouTube subscriptions API](https://developers.google.com/youtube/v3/docs/subscriptions), and [Google OAuth production readiness](https://developers.google.com/identity/protocols/oauth2/production-readiness/policy-compliance).

## X

The implemented candidate surface covers follows/unfollows and mutes/unmutes through the documented API scopes. The public Phoenix repository remains an offline ranking-code research reference, not an account-control API or a copy of a live For You algorithm.

X API access is currently pay-per-use. Under the zero-spend requirement:

- leave `FEED_PASSPORT_X_OAUTH_CLIENT_ID` and `FEED_PASSPORT_X_OAUTH_CLIENT_SECRET` blank;
- do not add funds, buy credits, create a billable project, invoke an endpoint, or run live conformance;
- demonstrate X with the account-free `twin:x`, Guided translation, and pinned public-code inspection only.

If the user later changes the spend boundary explicitly, recheck current X pricing and scopes before creating an app. Existing OAuth configuration is not evidence that an API request is free.

Official references: [X API pricing](https://docs.x.com/x-api/getting-started/pricing) and [OAuth 2.0 Authorization Code with PKCE](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code).

## Reddit

The implemented candidate surface observes subscribed communities and can subscribe or unsubscribe. It does not automate posts, comments, votes, messages, or private feed-training state.

Reddit requires a human account and explicit approval for external Data API access. A client ID alone is insufficient. Keep Reddit Guided until the account owner has an approval response/reference covering this exact use case.

After approval, the account owner must create a dedicated OAuth app with the exact callback URI, authorize only a dummy Reddit account, and configure `FEED_PASSPORT_REDDIT_OAUTH_CLIENT_ID` plus `FEED_PASSPORT_REDDIT_OAUTH_CLIENT_SECRET`. The requested scopes are `identity`, `mysubreddits`, and `subscribe`; the default client authentication mode is `client_secret_basic`. The approval reference is passed to the conformance command and is required in the signed receipt.

Reddit also requires a descriptive User-Agent tied to the registered app and operator. Configure `FEED_PASSPORT_REDDIT_OAUTH_USER_AGENT` using Reddit's `platform:app-id:version (by /u/operator)` shape. This is a format example only and contains no real username:

```text
web:feed-passport-approved-demo:v0.1.0 (by /u/dummy_operator)
```

Replace the app ID, version, and dummy operator with the values covered by the approval. Generic values such as `python`, `unknown`, or `feed-passport/0.1` fail closed. The User-Agent is not a secret, but do not falsely name another person or a personal account.

Official references: [Reddit Data API Wiki](https://support.reddithelp.com/hc/en-us/articles/29613840826260-Reddit-Data-API-Wiki) and [Reddit Data API Terms](https://redditinc.com/policies/data-api-terms).

## Bluesky and AT Protocol

Bluesky uses the official AT Protocol OAuth/DPoP Node client in a credential-isolated sidecar. App passwords are not accepted. Python receives only an opaque connection reference and short-lived opaque lease; access tokens, refresh tokens, DPoP keys, and confidential-client keys stay in the sidecar.

The account-free tests need Node.js 22 or newer. A registry-derived `package-lock.json` is mandatory for reproducible installation and the container build. If it is absent, one explicitly authorized networked npm metadata request must create it; do not invent integrity hashes:

```powershell
Push-Location .\services\atproto-oauth
npm install --package-lock-only --ignore-scripts --no-audit --no-fund
Pop-Location
```

Review and commit the generated lock before using `npm ci`. Package installation may download the pinned packages from npm, but it creates no social account and makes no social-platform request:

```powershell
Push-Location .\services\atproto-oauth
npm ci
npm test
npm run check
Pop-Location
```

The checked-in server adds an AES-256-GCM encrypted atomic file store for OAuth state, official OAuth sessions/DPoP material, owner bindings, opaque connections, and leases. It survives a clean process restart and uses a lock file to refuse a second process. It is intentionally a **single-replica** runtime; multi-replica deployment is not supported until the store and refresh lock are replaced with distributed implementations.

Prepare its local private state outside the repository without printing a key:

```powershell
$atprotoData = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'FeedPassport\atproto'
New-Item -ItemType Directory -Force -Path $atprotoData | Out-Null

$env:FEED_PASSPORT_ATPROTO_STORE_PATH = Join-Path $atprotoData 'sidecar.enc'
$env:FEED_PASSPORT_ATPROTO_STORE_KEY_B64 = New-FeedPassportKey
$env:FEED_PASSPORT_ATPROTO_STORE_KEY_ID = 'local-v1'
$env:FEED_PASSPORT_ATPROTO_SIDECAR_INTERNAL_SECRET = New-FeedPassportKey
$env:FEED_PASSPORT_ATPROTO_PRIVATE_KEY_FILE = Join-Path $atprotoData 'oauth-client-p256.pem'
$env:FEED_PASSPORT_ATPROTO_PRIVATE_KEY_ID = 'oauth-local-v1'

node -e "const {generateKeyPairSync}=require('node:crypto'); const {writeFileSync}=require('node:fs'); const {privateKey}=generateKeyPairSync('ec',{namedCurve:'P-256'}); writeFileSync(process.argv[1],privateKey.export({type:'pkcs8',format:'pem'}),{mode:0o600,flag:'wx'});" $env:FEED_PASSPORT_ATPROTO_PRIVATE_KEY_FILE
```

The exclusive-create flag refuses to overwrite an existing client key. Back up the encrypted store key separately from the ciphertext and keep the confidential-client key stable for the demo registration; losing or silently replacing either key requires deliberate recovery and likely reauthorization.

The same internal service secret must be present in the Curator and sidecar processes. Curator also needs the connection encryption keys described above, plus:

```text
FEED_PASSPORT_ATPROTO_SIDECAR_URL=http://127.0.0.1:4310
FEED_PASSPORT_ATPROTO_APP_CALLBACK_URI=http://127.0.0.1:5173/oauth/callback
```

Real AT Protocol OAuth cannot be completed entirely offline or from a localhost-only origin. The authorization server must be able to fetch public client metadata and JWKS and return the browser to a public HTTPS callback. Choose a public HTTPS origin, route only `/health`, `/oauth/atproto/client-metadata.json`, `/oauth/atproto/jwks.json`, and `/oauth/atproto/callback` to the local listener, and keep `/v1/*` on the private authenticated hop. Then configure:

```text
FEED_PASSPORT_ATPROTO_PUBLIC_ORIGIN=https://exact-public-sidecar-origin.example
FEED_PASSPORT_ATPROTO_HOST=127.0.0.1
FEED_PASSPORT_ATPROTO_PORT=4310
FEED_PASSPORT_ATPROTO_SIGNING_ALGORITHM=ES256
FEED_PASSPORT_ATPROTO_SCOPE=atproto transition:generic
FEED_PASSPORT_ATPROTO_CLIENT_NAME=Feed Passport
```

The optional client, policy, and terms URLs must be HTTPS. Leave `FEED_PASSPORT_ATPROTO_CLIENT_URI` unset to default it to the public origin; do not set it to an empty string.

After the variables are present, start the private listener locally:

```powershell
Push-Location .\services\atproto-oauth
npm start
Pop-Location
```

Startup reads the confidential key only from the configured absolute, regular, non-symlink file; it refuses malformed keys, partial configuration, an unreadable encrypted store, or an already active store lock. The process does not print keys or tokens. Its HTTP health response proves only process readiness, not OAuth, PDS reachability, account ownership, restart recovery, or a platform mutation.

Live OAuth additionally requires all of the following:

- an authorized dummy Bluesky account;
- a public HTTPS origin for client metadata, public JWKS, and callback routes;
- the externally generated ES256 confidential-client key supplied from the private file/secret mount;
- the configured encrypted, restart-safe single-process state/session store;
- exactly one sidecar process for this store; a multi-replica design would require a different distributed store and refresh lock;
- a private authenticated hop from Curator to the sidecar;
- `FEED_PASSPORT_ATPROTO_SIDECAR_URL` set to a fixed HTTPS origin, or an exact loopback origin for local development;
- `FEED_PASSPORT_ATPROTO_SIDECAR_INTERNAL_SECRET` set to an independent high-entropy 32–4096 character secret;
- `FEED_PASSPORT_ATPROTO_APP_CALLBACK_URI` set to the exact Feed Passport `/oauth/callback` URI in both processes.

The in-memory constructors remain test helpers only and lose OAuth/DPoP state on restart. The runnable server uses the encrypted store instead. TLS/public routing and private-hop isolation remain deployment-environment responsibilities; the checked-in Node listener itself is HTTP.

Official references: [AT Protocol OAuth specification](https://atproto.com/specs/oauth) and the [official Node OAuth client](https://github.com/bluesky-social/atproto/tree/main/packages/oauth/oauth-client-node).

## Instagram, Facebook, Threads, TikTok, LinkedIn, and Snapchat

These platforms have deterministic local twins and Guided adapters, but no supported general API in this repository that can write a consumer recommendation feed. Dummy accounts do not create API authority that the provider does not offer.

| Platform | Complete test available now | Honest external limit |
| --- | --- | --- |
| Instagram | `twin:instagram` plus Guided native steps | No general consumer recommendation-control write API is claimed. |
| Facebook | `twin:facebook` plus Guided Favorites/snooze/unfollow steps | Consumer feed preferences remain native UI controls. |
| Threads | `twin:threads` plus Guided native controls | Regional/temporary Your Algo behavior is not a supported write API. |
| TikTok | `twin:tiktok` plus Guided Manage Topics/portability steps | Access is region- and approval-gated; no feed-control write surface is claimed. |
| LinkedIn | `twin:linkedin` plus Guided portability/unfollow steps | Open OAuth scopes do not grant consumer feed control. |
| Snapchat | `twin:snapchat` plus Guided native steps | Login Kit is identity-only and grants no feed-control authority. |

For these six, “tested” means the deterministic twin, policy compiler, UI handoff, and translation-loss path pass. It must never be described as a live social-account mutation.

## Dummy-account conformance

Do not create or authorize dummy accounts until the user explicitly approves the specific platform. Use a neutral account with no personal history, no contacts, no private data, no payment method where avoidable, and no link to a personal recovery account if the provider permits that. Follow the platform's account and automation rules.

Prepare reversible targets before the run. For a subscribe/follow test, use an account that does not currently subscribe/follow the target. For an unsubscribe/unfollow test, establish that baseline manually first. For mute/unmute, record the initial state. Never test likes, posts, comments, reposts, votes, or messages.

The live runner is inert until it receives all of: an explicit dummy-account acknowledgement, owner ID, opaque connection ID, platform, exact action/target pairs, exact 40-character Git revision, external HMAC-key environment-variable name, explicit runtime factory, and new JSON output path. Reddit additionally requires its provider approval reference. Run only from a clean committed checkout; a receipt for `HEAD` cannot represent uncommitted source. The runner records the action before dispatch, reconciles unknown outcomes without blind replay, verifies reverse rollback, and emits no certification on failure.

The action vocabulary is deliberately small:

| Platform | Candidate conformance actions |
| --- | --- |
| YouTube | `subscribe_creator`, `unsubscribe_creator` |
| X | `follow_creator`, `unfollow_creator`, `mute_creator`, `unmute_creator` |
| Reddit | `subscribe_creator`, `unsubscribe_creator` |
| Bluesky | `follow_creator`, `unfollow_creator`, `mute_creator`, `unmute_creator`, `mute_keyword`, `unmute_keyword` |

A future authorized run uses this shape; replace every placeholder and keep the HMAC value only in the environment:

```powershell
$revision = git rev-parse HEAD
$env:FEED_PASSPORT_LIVE_CERTIFICATION_HMAC_KEY_B64 = New-FeedPassportKey

.\scripts\live-conformance.ps1 `
  -IAcknowledgeAuthorizedDummyAccountMutations `
  -OwnerId '<verified-oidc-sub>' `
  -ConnectionId '<opaque-owner-bound-connection-id>' `
  -Platform youtube `
  -Action 'subscribe_creator=<public-channel-id>' `
  -GitRevision $revision `
  -HmacKeyEnvironmentVariable FEED_PASSPORT_LIVE_CERTIFICATION_HMAC_KEY_B64 `
  -RuntimeFactory 'feed_passport.runtime.live_conformance_factory:build_builtin_live_conformance_runtime' `
  -OutputPath '.\artifacts\local\live-certifications\youtube.json'
```

The built-in factory requires `FEED_PASSPORT_DB_PATH` to name the existing SQLite database created by the OAuth flow. It restores only the requested owner-scoped active connection and exact scopes using the same external connection/vault keys and key IDs; construction itself makes no network request. Standard OAuth platforms also require their configured client registration and vault key. Bluesky uses the opaque sidecar connection reference instead of the standard vault.

Do not run that command merely because credentials exist. The account owner must approve the exact target and mutation at the time of the run. If it reports an unknown outcome or failure, inspect the dummy account manually before retrying; never assume rollback occurred.

To activate a successful receipt, set `FEED_PASSPORT_LIVE_CERTIFICATIONS_DIR` to its directory, set the same external HMAC key as `FEED_PASSPORT_LIVE_CERTIFICATION_HMAC_KEY_B64`, and set `FEED_PASSPORT_CODE_REVISION` to the exact deployed lowercase 40-character Git SHA. Runtime startup requires all three together and independently verifies the signature, expiry, exact revision match, action subset, dummy-account class, provider approval, and receipt shape. Keep the adapter Guided if any check fails.

## AWS, AgentCore, and Builder ID

The AgentCore implementation is prepared but not deployed. Local source tests prove the proposal-only Strands loop with a scripted no-network model, strict `health`/`plan_feature` commands, JWT-subject ownership handling, and a narrowly scoped synthesized stack. They do not prove managed JWT validation, Gateway routing, Bedrock entitlement, regional service availability, promotional-credit coverage, or billing.

There is no honest zero-dollar deployment guarantee. AgentCore Runtime, Gateway, Bedrock, Cognito, S3 deployment assets, and CloudWatch can be consumption-based. AWS credits and Budgets are not hard spend caps. Under the current zero-spend requirement, do not bootstrap, deploy, create a Cognito user, invoke Runtime health, invoke Bedrock, or tear down resources that were never created.

The safe source and template checks are:

```powershell
Push-Location .\infra\agentcore
.\scripts\local-dry-run.ps1
Pop-Location
```

Docker/package downloads may use network bandwidth but do not call AWS. Use `-IncludeLinuxArm64Package` only when those downloads are authorized. A complete explicit local CDK plan can use non-secret placeholders and still makes no AWS service call; see [the AgentCore runbook](../infra/agentcore/README.md).

Package and full-plan commands intentionally fail while the Git worktree is dirty. The normal path rebuilds from the checkout and embeds the exact clean `HEAD`. A no-AWS plan may use `-SkipPackage`, but its validator can prove only archive shape and that the embedded manifest claims the current commit; it cannot independently prove the archive's source bytes. Apply mode rejects `-SkipPackage` and always rebuilds in the same invocation. The current Python image tag and ranged application dependencies are mutable, so even the normal path provides checkout binding and deterministic ZIP serialization, not byte-for-byte dependency reproducibility.

The cloud Runtime contract is explicit: CDK supplies `FEED_PASSPORT_BEDROCK_MODEL_ID`, `FEED_PASSPORT_BEDROCK_REGION`, and `FEED_PASSPORT_JWT_ISSUER`; `FEED_PASSPORT_BEDROCK_TIMEOUT_SECONDS` is optional and defaults to `60` with a valid range of `1..300`. The stack intentionally omits unused DynamoDB, Secrets Manager, token-vault, workload-identity resources, and their IAM permissions until application code consumes them. The runtime package keeps `boto3` as an explicit dependency even when another package would install it transitively.

The entrant must personally complete the external identity steps:

- create or use an AWS account accepted by the event and review its billing controls;
- create their own AWS Builder ID and add it to the Devpost submission;
- understand that Builder ID is a community/hackathon identity, not an IAM deployment credential;
- obtain a dedicated least-privilege account role through IAM Identity Center or another account-approved CLI mechanism;
- run `aws configure sso --profile feed-passport-demo` and complete browser sign-in locally;
- share only the profile name, expected 12-digit account ID, intended Region, exact model ID/ARN, and unique Cognito domain prefix with the operator—not a token or access key.

A conventional Builder ID alone cannot authenticate the AWS CLI. If the account is enrolled in AWS's limited new-account experience, the available credential path may differ; follow only the account's own IAM guidance and keep using a dedicated least-privilege profile.

An unrelated existing AWS profile must never be reused. After the user has authenticated the dedicated profile and explicitly authorizes a read-only check, the repository's `infra/agentcore/scripts/preflight.ps1 -Mode AwsReadOnly` can confirm caller identity and service/model metadata without invoking Runtime or Bedrock. Deployment and invocation still require separate potential-charge acknowledgements and remain prohibited under zero spend.

AgentCore `plan_feature` is proposal-only. It derives the actor only from the Runtime-validated JWT `sub`, uses one explicitly configured Bedrock model with no fallback, and exposes no execute, approval, rollback, credential, browser-control, or live-platform mutation operation. Even a successful deployment would not by itself prove a social account was changed.

If deployment is later authorized despite the charge risk, use only the gated `infra/agentcore/scripts/deploy.ps1` flow. The current CloudFormation Runtime resource cannot set `metadataConfiguration.requireMMDSV2`; the deployment script must perform a post-deploy `UpdateAgentRuntime`, wait for `READY`, read the Runtime back, and fail unless `requireMMDSV2=true`. A stack creation result without that verified post-deploy update is not deployment-ready evidence.

Official references: [AWS Builder ID versus AWS credentials](https://docs.aws.amazon.com/signin/latest/userguide/differences-builder-id.html), [AWS CLI IAM Identity Center configuration](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html), and [AgentCore Runtime security guidance](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-security-best-practices.html).

## What the user must eventually provide

The user does not need to give anyone a password or secret. The irreducibly personal/provider-owned items are:

- explicit approval to create and use one dummy account per selected platform;
- browser completion of OIDC and each social OAuth consent flow;
- Google Cloud/YouTube OAuth registration;
- Reddit Data API approval and approval reference;
- a Bluesky dummy account and an HTTPS/private-sidecar hosting choice;
- a decision to leave X live testing disabled under zero spend;
- an AWS account, Builder ID, and dedicated local SSO login if the event requires them;
- a later, separate decision accepting potential AWS charges before any deployment or invocation.

Once those exist, the user can provide only non-secret identifiers and explicit scope. The project can then run read-only preflights first, perform one reversible dummy-account action at a time, verify rollback, and promote only the exact capability supported by a signed receipt.
