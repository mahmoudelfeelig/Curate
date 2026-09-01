import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { test } from 'node:test'

import { buildClientMetadata, createOfficialOAuthClient } from '../src/official-client.mjs'
import { createOfficialAgentExecutor } from '../src/official-executor.mjs'
import { InMemoryOwnerStateStore, InMemorySecretStore } from '../src/stores.mjs'
import { sanitizeOperationResult } from '../src/validation.mjs'

test('client metadata is an exact confidential DPoP web-client contract', () => {
  const metadataUri = 'https://feed.example/oauth/atproto/client-metadata.json'
  const metadata = buildClientMetadata({
    clientId: metadataUri,
    metadataUri,
    clientUri: 'https://feed.example/',
    jwksUri: 'https://feed.example/oauth/atproto/jwks.json',
    redirectUri: 'https://feed.example/oauth/atproto/callback',
    name: 'Feed Passport',
    signingAlgorithm: 'ES256',
  })
  assert.equal(metadata.client_id, metadataUri)
  assert.equal(metadata.token_endpoint_auth_method, 'private_key_jwt')
  assert.equal(metadata.token_endpoint_auth_signing_alg, 'ES256')
  assert.equal(metadata.dpop_bound_access_tokens, true)
  assert.deepEqual(metadata.grant_types, ['authorization_code', 'refresh_token'])
  assert.equal(JSON.stringify(metadata).includes('private'), true)
  assert.equal(Object.hasOwn(metadata, 'client_secret'), false)
})

test('official client factory fails before imports when durable boundaries are absent', async () => {
  await assert.rejects(
    createOfficialOAuthClient({}),
    (error) => error.code === 'sidecar_storage_unconfigured' && error.status === 503,
  )
  const store = new InMemorySecretStore()
  await assert.rejects(
    createOfficialOAuthClient({ stateStore: store, sessionStore: store }),
    (error) => error.code === 'sidecar_lock_unconfigured' && error.status === 503,
  )
})

test('secret stores redact JSON and owner state remains one-time', async () => {
  const secrets = new InMemorySecretStore()
  await secrets.set('did:plc:alice', {
    accessToken: 'secret-access',
    refreshToken: 'secret-refresh',
  })
  assert.deepEqual(JSON.parse(JSON.stringify(secrets)), {
    type: 'InMemorySecretStore',
    contents: '<redacted>',
  })
  assert.equal(JSON.stringify(secrets).includes('secret-access'), false)

  const states = new InMemoryOwnerStateStore()
  await states.create('state-value', { ownerId: 'user:one', expiresAt: 2_000 })
  await assert.rejects(
    states.consume('state-value', { ownerId: 'user:two', now: 1_000 }),
    (error) => error.code === 'oauth_owner_mismatch',
  )
  assert.deepEqual(
    await states.consume('state-value', { ownerId: 'user:one', now: 1_000 }),
    { ownerId: 'user:one', expiresAt: 2_000 },
  )
  await assert.rejects(
    states.consume('state-value', { ownerId: 'user:one', now: 1_000 }),
    (error) => error.code === 'oauth_state_unknown',
  )
})

test('official Agent bridge uses raw generated APIs and guards preference replacement', async () => {
  const rawPreferences = [
    { $type: 'app.bsky.actor.defs#mutedWordsPref', items: [] },
    { $type: 'app.bsky.actor.defs#interestsPref', tags: ['technology'] },
  ]
  const calls = []
  class FakeAgent {
    constructor(session) {
      assert.deepEqual(session, { did: 'did:plc:alice' })
      this.app = {
        bsky: {
          graph: {
            getFollows: async (input) => ({ data: { input, follows: [] } }),
            getMutes: async (input) => ({ data: { input, mutes: [] } }),
          },
          actor: {
            getPreferences: async (input) => {
              calls.push({ operation: 'getPreferences', input })
              return { data: { preferences: structuredClone(rawPreferences) } }
            },
            putPreferences: async (input) => {
              calls.push({ operation: 'putPreferences', input })
            },
          },
        },
      }
    }
  }
  const executor = await createOfficialAgentExecutor({ AgentClass: FakeAgent })
  const observed = await executor.execute({
    session: { did: 'did:plc:alice' },
    did: 'did:plc:alice',
    operation: 'actor.get_preferences',
    input: {},
  })
  const expectedDigest = createHash('sha256')
    .update(stableJson(rawPreferences))
    .digest('hex')
  assert.equal(observed.observed_sha256, expectedDigest)
  assert.deepEqual(observed.preferences, [rawPreferences[0]])

  const replacement = [{ $type: 'app.bsky.actor.defs#mutedWordsPref', items: [] }]
  await executor.execute({
    session: { did: 'did:plc:alice' },
    did: 'did:plc:alice',
    operation: 'actor.put_preferences',
    input: { preferences: replacement, expected_sha256: expectedDigest },
  })
  assert.deepEqual(calls.at(-1), {
    operation: 'putPreferences',
    input: { preferences: [replacement[0], rawPreferences[1]] },
  })

  await assert.rejects(
    executor.execute({
      session: { did: 'did:plc:alice' },
      did: 'did:plc:alice',
      operation: 'actor.put_preferences',
      input: { preferences: replacement, expected_sha256: '0'.repeat(64) },
    }),
    (error) => error.code === 'preferences_changed' && error.status === 409,
  )
})

test('operation responses are projected through exact public schemas', () => {
  const did = 'did:plc:alice'
  assert.deepEqual(
    sanitizeOperationResult(
      'graph.get_follows',
      {
        subject: { did, displayName: 'must-not-cross' },
        follows: [
          {
            did: 'did:plc:bob',
            displayName: 'must-not-cross',
            viewer: {
              following: `at://${did}/app.bsky.graph.follow/one`,
              muted: true,
              followedBy: 'at://did:plc:bob/app.bsky.graph.follow/two',
            },
          },
        ],
        cursor: 'next-page',
      },
      { ownerDid: did },
    ),
    {
      follows: [
        {
          did: 'did:plc:bob',
          viewer: { following: `at://${did}/app.bsky.graph.follow/one` },
        },
      ],
      cursor: 'next-page',
    },
  )
  assert.deepEqual(
    sanitizeOperationResult(
      'graph.get_mutes',
      { mutes: [{ did: 'did:plc:bob', access_token: 'drop-me' }] },
      { ownerDid: did },
    ),
    { mutes: [{ did: 'did:plc:bob' }] },
  )
  assert.deepEqual(
    sanitizeOperationResult(
      'actor.get_preferences',
      {
        preferences: [
          { $type: 'app.bsky.actor.defs#personalDetailsPref', birthDate: '2000-01-01' },
          { $type: 'app.bsky.actor.defs#mutedWordsPref', items: [] },
        ],
        observed_sha256: 'a'.repeat(64),
      },
      { ownerDid: did },
    ),
    {
      preferences: [{ $type: 'app.bsky.actor.defs#mutedWordsPref', items: [] }],
      observed_sha256: 'a'.repeat(64),
    },
  )
  assert.throws(
    () => sanitizeOperationResult('graph.follow', { message: 'not-an-owned-record' }, { ownerDid: did }),
    (error) => error.code === 'bridge_response_invalid',
  )
})

function stableJson(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`
  return `{${Object.keys(value)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`)
    .join(',')}}`
}
