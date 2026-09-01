import assert from 'node:assert/strict'
import { test } from 'node:test'

import { AtprotoOAuthService } from '../src/service.mjs'
import {
  InMemoryOwnerConnectionStore,
  InMemoryOwnerStateStore,
  InMemorySessionLeaseStore,
} from '../src/stores.mjs'

const OWNER_A = 'user:owner-a'
const OWNER_B = 'user:owner-b'
const ALICE_DID = 'did:plc:alice123'

test('OAuth callback is owner-bound and response-loss replay returns its durable result', async () => {
  const { service, oauthClient } = fixture()
  const start = await service.start({ ownerId: OWNER_A, handle: '@Alice.Bsky.Social' })
  const state = new URL(start.authorization_url).searchParams.get('state')
  assert.notEqual(state, oauthClient.lastAppState)

  await assert.rejects(
    service.callback({ ownerId: OWNER_B, query: `code=code-1&state=${state}` }),
    (error) => error.code === 'oauth_owner_mismatch' && error.status === 403,
  )

  const connected = await service.callback({
    ownerId: OWNER_A,
    query: `iss=https%3A%2F%2Fpds.example&code=code-1&state=${state}`,
  })
  assert.equal(connected.external_subject, ALICE_DID)
  assert.equal(connected.status, 'active')
  assert.match(connected.connection_ref, /^[A-Za-z0-9_-]{32,}$/)
  assert.equal(JSON.stringify(connected).includes('access-token'), false)
  assert.equal(oauthClient.callbackCount, 1)

  const replayed = await service.callback({
    ownerId: OWNER_A,
    query: `code=code-1&state=${state}`,
  })
  assert.deepEqual(replayed, connected)
  assert.equal(oauthClient.callbackCount, 1)
})

test('PAR authorization binds protocol state even when the browser URL omits it', async () => {
  const { service, oauthClient } = fixture({ par: true })
  const start = await service.start({ ownerId: OWNER_A, handle: 'alice.bsky.social' })
  const authorization = new URL(start.authorization_url)
  assert.equal(authorization.searchParams.get('state'), null)
  assert.equal(authorization.searchParams.get('request_uri'), 'urn:ietf:params:oauth:request_uri:test')

  const connected = await service.callback({
    ownerId: OWNER_A,
    query: `code=code-1&state=${oauthClient.lastProtocolState}`,
  })
  assert.equal(connected.external_subject, ALICE_DID)
})

test('invalid callback parameters are rejected before the official client', async () => {
  const { service, oauthClient } = fixture()
  const start = await service.start({ ownerId: OWNER_A, handle: 'alice.bsky.social' })
  const state = new URL(start.authorization_url).searchParams.get('state')

  await assert.rejects(
    service.callback({
      ownerId: OWNER_A,
      query: `code=one&code=two&state=${state}`,
    }),
    (error) => error.code === 'oauth_callback_invalid',
  )
  assert.equal(oauthClient.callbackCount, 0)
})

test('callback state mismatch revokes the session and creates no connection', async () => {
  const { service, oauthClient, connections } = fixture({ callbackState: 'wrong-state' })
  const start = await service.start({ ownerId: OWNER_A, handle: 'alice.bsky.social' })
  const state = new URL(start.authorization_url).searchParams.get('state')

  await assert.rejects(
    service.callback({ ownerId: OWNER_A, query: `code=one&state=${state}` }),
    (error) => error.code === 'oauth_callback_state_mismatch',
  )
  assert.deepEqual(oauthClient.revoked, [ALICE_DID])
  assert.equal(connections.size, 0)
})

test('malformed official callback sessions are consumed without leaving processing state', async () => {
  const { service, oauthClient, ownerStates } = fixture({ callbackSession: {} })
  const start = await service.start({ ownerId: OWNER_A, handle: 'alice.bsky.social' })
  const state = new URL(start.authorization_url).searchParams.get('state')

  await assert.rejects(
    service.callback({ ownerId: OWNER_A, query: `code=one&state=${state}` }),
    (error) => error.code === 'atproto_subject_invalid',
  )
  assert.equal(ownerStates.size, 0)
  assert.deepEqual(oauthClient.revoked, [])
  await assert.rejects(
    service.callback({ ownerId: OWNER_A, query: `code=one&state=${state}` }),
    (error) => error.code === 'oauth_state_unknown',
  )
})

test('restore returns only an opaque DPoP lease and executes bounded operations', async () => {
  const { service, executorCalls } = fixture()
  const connected = await connect(service)
  const restored = await service.restore({
    ownerId: OWNER_A,
    connectionRef: connected.connection_ref,
  })

  assert.equal(restored.authorization_scheme, 'DPoP')
  assert.equal(restored.external_subject, ALICE_DID)
  assert.match(restored.lease_ref, /^[A-Za-z0-9_-]{32,}$/)
  assert.deepEqual(
    Object.keys(restored).sort(),
    [
      'authorization_scheme',
      'connection_ref',
      'expires_at',
      'external_subject',
      'lease_ref',
      'platform',
    ].sort(),
  )

  const result = await service.execute({
    ownerId: OWNER_A,
    leaseRef: restored.lease_ref,
    operation: 'graph.follow',
    input: { actor: 'did:plc:bob456' },
  })
  assert.deepEqual(result.result, {
    uri: `at://${ALICE_DID}/app.bsky.graph.follow/follow-test`,
    cid: 'bafy-test-cid',
  })
  assert.equal(executorCalls[0].did, ALICE_DID)
  assert.deepEqual(executorCalls[0].input, { actor: 'did:plc:bob456' })

  await assert.rejects(
    service.execute({
      ownerId: OWNER_B,
      leaseRef: restored.lease_ref,
      operation: 'graph.follow',
      input: { actor: 'did:plc:bob456' },
    }),
    (error) => error.code === 'session_lease_not_found',
  )
  await assert.rejects(
    service.execute({
      ownerId: OWNER_A,
      leaseRef: restored.lease_ref,
      operation: 'feed.create_post',
      input: {},
    }),
    (error) => error.code === 'bridge_operation_denied' && error.status === 403,
  )
})

test('credential-shaped operation responses are blocked', async () => {
  const { service } = fixture({
    operationResult: { access_token: 'must-not-cross-boundary' },
  })
  const connected = await connect(service)
  const restored = await service.restore({
    ownerId: OWNER_A,
    connectionRef: connected.connection_ref,
  })
  await assert.rejects(
    service.execute({
      ownerId: OWNER_A,
      leaseRef: restored.lease_ref,
      operation: 'actor.get_preferences',
      input: {},
    }),
    (error) => error.code === 'bridge_response_invalid' && error.status === 502,
  )
})

test('credential values echoed under allowlisted platform fields are blocked', async () => {
  const { service } = fixture({
    operationResult: {
      preferences: [
        {
          $type: 'app.bsky.actor.defs#mutedWordsPref',
          items: [
            {
              id: 'echo-test',
              value: 'held-only-by-sidecar',
              targets: ['content'],
            },
          ],
        },
      ],
      observed_sha256: '0'.repeat(64),
    },
  })
  const connected = await connect(service)
  const restored = await service.restore({
    ownerId: OWNER_A,
    connectionRef: connected.connection_ref,
  })
  await assert.rejects(
    service.execute({
      ownerId: OWNER_A,
      leaseRef: restored.lease_ref,
      operation: 'actor.get_preferences',
      input: {},
    }),
    (error) => error.code === 'bridge_secret_detected' && error.status === 502,
  )
})

test('revoke is owner-bound and invalidates connection leases', async () => {
  const { service, oauthClient } = fixture()
  const connected = await connect(service)
  const restored = await service.restore({
    ownerId: OWNER_A,
    connectionRef: connected.connection_ref,
  })

  await assert.rejects(
    service.revoke({ ownerId: OWNER_B, connectionRef: connected.connection_ref }),
    (error) => error.code === 'atproto_connection_not_found',
  )
  const revoked = await service.revoke({
    ownerId: OWNER_A,
    connectionRef: connected.connection_ref,
  })
  assert.equal(revoked.status, 'revoked')
  assert.equal(revoked.revocation_proof, 'provider_confirmed')
  assert.deepEqual(oauthClient.revoked, [ALICE_DID])
  await assert.rejects(
    service.execute({
      ownerId: OWNER_A,
      leaseRef: restored.lease_ref,
      operation: 'actor.get_preferences',
      input: {},
    }),
    (error) => error.code === 'session_lease_not_found',
  )
})

test('revocation transition blocks queued restore and execute work', async () => {
  let releaseRevoke
  let markRevokeStarted
  const revokeStarted = new Promise((resolve) => {
    markRevokeStarted = resolve
  })
  const revokeGate = new Promise((resolve) => {
    releaseRevoke = resolve
  })
  const { service, connections, executorCalls } = fixture({
    revokeImpl: async () => {
      markRevokeStarted()
      await revokeGate
    },
  })
  const connected = await connect(service)
  const restored = await service.restore({
    ownerId: OWNER_A,
    connectionRef: connected.connection_ref,
  })

  const revoking = service.revoke({
    ownerId: OWNER_A,
    connectionRef: connected.connection_ref,
  })
  await revokeStarted
  assert.equal(
    (await connections.get(connected.connection_ref, { ownerId: OWNER_A })).status,
    'revoking',
  )
  const rejectedExecute = assert.rejects(
    service.execute({
      ownerId: OWNER_A,
      leaseRef: restored.lease_ref,
      operation: 'graph.follow',
      input: { actor: 'did:plc:bob456' },
    }),
    (error) => error.code === 'session_lease_not_found',
  )
  const rejectedRestore = assert.rejects(
    service.restore({
      ownerId: OWNER_A,
      connectionRef: connected.connection_ref,
    }),
    (error) => error.code === 'atproto_connection_inactive',
  )
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(executorCalls.length, 0)

  releaseRevoke()
  const revoked = await revoking
  await Promise.all([rejectedExecute, rejectedRestore])
  assert.equal(revoked.revocation_proof, 'provider_confirmed')
  const repeated = await service.revoke({
    ownerId: OWNER_A,
    connectionRef: connected.connection_ref,
  })
  assert.equal(repeated.revocation_proof, 'sidecar_terminal')
})

test('unknown provider revocation never becomes confirmed after the local session disappears', async () => {
  const { service, oauthClient, connections } = fixture()
  const connected = await connect(service)
  let sessionAvailable = true
  oauthClient.revokeWithProof = async () => {
    if (!sessionAvailable) throw new Error('official session missing')
    sessionAvailable = false
    throw new Error('provider outcome unknown after local session deletion')
  }

  for (let attempt = 0; attempt < 2; attempt += 1) {
    await assert.rejects(
      service.revoke({ ownerId: OWNER_A, connectionRef: connected.connection_ref }),
      (error) => error.code === 'session_revoke_failed',
    )
  }
  const record = await connections.get(connected.connection_ref, { ownerId: OWNER_A })
  assert.equal(record.status, 'revoking')
  assert.equal(record.providerConfirmedAt, undefined)
})

test('JWKS endpoint refuses private key material', () => {
  const { service } = fixture({ jwks: { keys: [{ kty: 'EC', crv: 'P-256', d: 'private' }] } })
  assert.throws(
    () => service.publicJwks(),
    (error) => error.code === 'jwks_private_material' && error.status === 503,
  )
})

test('JWKS endpoint removes undefined private placeholders from official public keys', () => {
  const { service } = fixture({
    jwks: {
      keys: [{ kty: 'EC', crv: 'P-256', x: 'public-x', y: 'public-y', d: undefined }],
    },
  })
  const jwks = service.publicJwks()
  assert.equal(jwks.keys.length, 1)
  assert.equal('d' in jwks.keys[0], false)
})

async function connect(service) {
  const start = await service.start({ ownerId: OWNER_A, handle: 'alice.bsky.social' })
  const state = new URL(start.authorization_url).searchParams.get('state')
  return service.callback({ ownerId: OWNER_A, query: `code=code-1&state=${state}` })
}

function fixture({ callbackSession, callbackState, jwks, operationResult, revokeImpl, par = false } = {}) {
  let now = Date.parse('2026-09-01T12:00:00.000Z')
  const ownerStates = new InMemoryOwnerStateStore()
  const appStates = new Map()
  const activeSessions = new Set()
  let authorizationCount = 0
  const oauthClient = {
    clientMetadata: {
      client_id: 'https://feed.example/oauth/atproto/client-metadata.json',
      dpop_bound_access_tokens: true,
    },
    jwks: jwks ?? { keys: [{ kty: 'EC', crv: 'P-256', x: 'public-x', y: 'public-y' }] },
    callbackCount: 0,
    revoked: [],
    async authorize(handle, { state: appState }) {
      authorizationCount += 1
      const protocolState = `${'p'.repeat(42)}${authorizationCount}`
      await ownerStates.bindProtocolState(appState, protocolState)
      appStates.set(protocolState, appState)
      this.lastAppState = appState
      this.lastProtocolState = protocolState
      const url = new URL('https://pds.example/oauth/authorize')
      url.searchParams.set('login_hint', handle)
      if (par) {
        url.searchParams.set('client_id', 'https://feed.example/oauth/atproto/client-metadata.json')
        url.searchParams.set('request_uri', 'urn:ietf:params:oauth:request_uri:test')
      } else {
        url.searchParams.set('state', protocolState)
      }
      return url
    },
    async callback(params) {
      this.callbackCount += 1
      activeSessions.add(ALICE_DID)
      return {
        state: callbackState ?? appStates.get(params.get('state')),
        session: callbackSession ?? { did: ALICE_DID, secret: 'held-only-by-sidecar' },
      }
    },
    async restore(did) {
      assert.equal(did, ALICE_DID)
      return { did, secret: 'held-only-by-sidecar' }
    },
    async revoke(did) {
      this.revoked.push(did)
      activeSessions.delete(did)
    },
    async revokeWithProof(did, onProviderConfirmed) {
      if (!activeSessions.has(did)) throw new Error('official session missing')
      this.revoked.push(did)
      if (revokeImpl) await revokeImpl(did)
      await onProviderConfirmed()
      activeSessions.delete(did)
    },
    async discardSession(did) {
      activeSessions.delete(did)
    },
  }
  const connections = new InMemoryOwnerConnectionStore(ownerStates)
  const sessionLeases = new InMemorySessionLeaseStore()
  const executorCalls = []
  const operationExecutor = {
    async execute(call) {
      executorCalls.push(call)
      return operationResult ?? validOperationResult(call)
    },
  }
  return {
    oauthClient,
    ownerStates,
    connections,
    sessionLeases,
    executorCalls,
    service: new AtprotoOAuthService({
      oauthClient,
      ownerStates,
      connections,
      sessionLeases,
      operationExecutor,
      now: () => now,
      randomState: () => 'a'.repeat(43),
    }),
    advance(ms) {
      now += ms
    },
  }
}

function validOperationResult(call) {
  switch (call.operation) {
    case 'graph.follow':
      return {
        uri: `at://${call.did}/app.bsky.graph.follow/follow-test`,
        cid: 'bafy-test-cid',
      }
    case 'actor.get_preferences':
      return { preferences: [], observed_sha256: '0'.repeat(64) }
    default:
      throw new Error(`missing fixture result for ${call.operation}`)
  }
}
