import assert from 'node:assert/strict'
import { generateKeyPairSync } from 'node:crypto'
import test from 'node:test'

import { createOfficialOAuthClient } from '../src/official-client.mjs'
import { InMemoryOwnerStateStore } from '../src/stores.mjs'
import { assertPublicJwks } from '../src/validation.mjs'

function memoryStore() {
  const values = new Map()
  return {
    async set(key, value) {
      values.set(key, value)
    },
    async get(key) {
      return values.get(key)
    },
    async del(key) {
      values.delete(key)
    },
  }
}

test('pinned official packages construct the confidential DPoP client without network access', async () => {
  const { privateKey } = generateKeyPairSync('ec', {
    namedCurve: 'P-256',
    privateKeyEncoding: { type: 'pkcs8', format: 'pem' },
    publicKeyEncoding: { type: 'spki', format: 'pem' },
  })
  let lockCalls = 0
  const requestLock = async (_name, work) => {
    lockCalls += 1
    return work()
  }
  const stateStore = memoryStore()
  const ownerStates = new InMemoryOwnerStateStore()
  const client = await createOfficialOAuthClient({
    client: {
      clientId: 'https://passport.example/oauth/atproto/client-metadata.json',
      metadataUri: 'https://passport.example/oauth/atproto/client-metadata.json',
      clientUri: 'https://passport.example/',
      jwksUri: 'https://passport.example/oauth/atproto/jwks.json',
      redirectUri: 'https://passport.example/oauth/atproto/callback',
      name: 'Feed Passport',
      signingAlgorithm: 'ES256',
      scope: 'atproto transition:generic',
    },
    stateStore,
    sessionStore: memoryStore(),
    requestLock,
    ownerStates,
    privateKeys: [{ kid: 'ephemeral-test-key', importable: privateKey }],
    fetch: async () => {
      throw new Error('client construction must not make a network request')
    },
  })

  assert.equal(client.clientMetadata.client_id, 'https://passport.example/oauth/atproto/client-metadata.json')
  assert.equal(client.clientMetadata.token_endpoint_auth_method, 'private_key_jwt')
  assert.equal(client.clientMetadata.token_endpoint_auth_signing_alg, 'ES256')
  const publicJwks = assertPublicJwks(client.jwks)
  assert.equal(publicJwks.keys.length, 1)
  assert.equal(publicJwks.keys[0].kid, 'ephemeral-test-key')
  assert.equal('d' in publicJwks.keys[0], false)
  assert.equal(typeof client.authorize, 'function')
  assert.equal(typeof client.callback, 'function')
  assert.equal(typeof client.restore, 'function')
  assert.equal(typeof client.revokeWithProof, 'function')
  assert.equal(typeof client.discardSession, 'function')
  assert.equal(lockCalls, 0)

  const appState = 'a'.repeat(43)
  const protocolState = 'p'.repeat(43)
  await ownerStates.create(appState, {
    ownerId: 'user:owner-a',
    expiresAt: Date.now() + 60_000,
  })
  client.oauthResolver.resolve = async () => ({
    identityInfo: undefined,
    metadata: {
      issuer: 'https://auth.example/',
      authorization_endpoint: 'https://auth.example/oauth/authorize',
      token_endpoint: 'https://auth.example/oauth/token',
      revocation_endpoint: 'https://auth.example/oauth/revoke',
      response_types_supported: ['code'],
      grant_types_supported: ['authorization_code', 'refresh_token'],
      code_challenge_methods_supported: ['S256'],
      token_endpoint_auth_methods_supported: ['private_key_jwt'],
      token_endpoint_auth_signing_alg_values_supported: ['ES256'],
      dpop_signing_alg_values_supported: ['ES256'],
      authorization_response_iss_parameter_supported: false,
      scopes_supported: ['atproto', 'transition:generic'],
    },
  })
  client.runtime.generateNonce = async () => protocolState
  const authorization = await client.authorize('alice.bsky.social', { state: appState })
  assert.equal(authorization.searchParams.get('state'), protocolState)
  assert.notEqual(authorization.searchParams.get('state'), appState)
  assert.equal((await stateStore.get(protocolState)).appState, appState)
  assert.equal(
    (await ownerStates.bound(appState, {
      ownerId: 'user:owner-a',
      now: Date.now(),
    })).protocolState,
    protocolState,
  )
})
