import assert from 'node:assert/strict'
import { createCipheriv, randomBytes } from 'node:crypto'
import { mkdtemp, readFile, rm, utimes, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, test } from 'node:test'

import {
  createDurableSidecarStores,
  DurableSecretStore,
  EncryptedAtomicStore,
} from '../src/durable-store.mjs'
import { AtprotoOAuthService } from '../src/service.mjs'

const temporaries = []
const BASE_NOW = Date.now()

afterEach(async () => {
  await Promise.all(temporaries.splice(0).map((directory) => rm(directory, { recursive: true })))
})

test('encrypted stores survive restart without plaintext credentials', async () => {
  const directory = await temporaryDirectory()
  const filePath = join(directory, 'sidecar-state.enc')
  const encryptionKey = randomBytes(32)
  let stores = await createDurableSidecarStores({ filePath, encryptionKey, keyId: 'test-v1' })
  await stores.oauthStateStore.set('oauth-state-key', {
    verifier: 'secret-pkce-verifier',
  })
  await stores.oauthSessionStore.set('did:plc:alice123', {
    accessToken: 'secret-access-token',
    refreshToken: 'secret-refresh-token',
    dpopJwk: { d: 'secret-private-coordinate' },
  })
  await stores.ownerStates.create('browser-state-value', {
    ownerId: 'user:owner-a',
    expiresAt: BASE_NOW + 50_000,
  })
  const connection = await stores.connections.bind({
    ownerId: 'user:owner-a',
    did: 'did:plc:alice123',
    now: BASE_NOW + 1_000,
  })
  const lease = await stores.sessionLeases.create({
    ownerId: 'user:owner-a',
    connectionRef: connection.connectionRef,
    did: connection.did,
    session: { runtimeOnly: 'must-not-be-duplicated' },
    now: BASE_NOW + 1_000,
    ttlMs: 30_000,
  })
  const encrypted = await readFile(filePath, 'utf8')
  for (const forbidden of [
    'secret-access-token',
    'secret-refresh-token',
    'secret-private-coordinate',
    'secret-pkce-verifier',
    'browser-state-value',
    'user:owner-a',
    'did:plc:alice123',
    'must-not-be-duplicated',
  ]) {
    assert.equal(encrypted.includes(forbidden), false)
  }
  assert.deepEqual(JSON.parse(JSON.stringify(stores.database)), {
    type: 'EncryptedAtomicStore',
    file: '<configured>',
    contents: '<redacted>',
  })
  await stores.close()

  stores = await createDurableSidecarStores({ filePath, encryptionKey, keyId: 'test-v1' })
  assert.deepEqual(await stores.oauthStateStore.get('oauth-state-key'), {
    verifier: 'secret-pkce-verifier',
  })
  assert.equal(
    (await stores.oauthSessionStore.get('did:plc:alice123')).refreshToken,
    'secret-refresh-token',
  )
  await assert.rejects(
    stores.ownerStates.consume('browser-state-value', {
      ownerId: 'user:owner-b',
      now: BASE_NOW + 2_000,
    }),
    (error) => error.code === 'oauth_owner_mismatch',
  )
  assert.deepEqual(
    await stores.ownerStates.consume('browser-state-value', {
      ownerId: 'user:owner-a',
      now: BASE_NOW + 2_000,
    }),
    { ownerId: 'user:owner-a', expiresAt: BASE_NOW + 50_000 },
  )
  assert.equal(
    (await stores.connections.get(connection.connectionRef, { ownerId: 'user:owner-a' })).did,
    'did:plc:alice123',
  )
  let restoredDid
  stores.sessionLeases.setSessionRestorer(async (did) => {
    restoredDid = did
    return { restored: true, did }
  })
  const restoredLease = await stores.sessionLeases.get(lease.leaseRef, {
    ownerId: 'user:owner-a',
    now: BASE_NOW + 2_000,
  })
  assert.equal(restoredDid, 'did:plc:alice123')
  assert.deepEqual(restoredLease.session, { restored: true, did: 'did:plc:alice123' })
  await stores.close()
})

test('owner state expiry is durably consumed and cannot replay after restart', async () => {
  const directory = await temporaryDirectory()
  const filePath = join(directory, 'expiry.enc')
  const encryptionKey = randomBytes(32)
  let stores = await createDurableSidecarStores({ filePath, encryptionKey })
  await stores.ownerStates.create('expiring-state', {
    ownerId: 'user:owner-a',
    expiresAt: BASE_NOW + 2_000,
  })
  await assert.rejects(
    stores.ownerStates.consume('expiring-state', {
      ownerId: 'user:owner-a',
      now: BASE_NOW + 2_000,
    }),
    (error) => error.code === 'oauth_state_expired',
  )
  await stores.close()

  stores = await createDurableSidecarStores({ filePath, encryptionKey })
  await assert.rejects(
    stores.ownerStates.consume('expiring-state', {
      ownerId: 'user:owner-a',
      now: BASE_NOW + 2_001,
    }),
    (error) => error.code === 'oauth_state_unknown',
  )
  await stores.close()
})

test('callback receipt survives restart without persisting raw callback material', async () => {
  const directory = await temporaryDirectory()
  const filePath = join(directory, 'callback-receipt.enc')
  const encryptionKey = randomBytes(32)
  const state = 'r'.repeat(43)
  const sensitiveCode = 'one-time-sensitive-code'
  let callbackCount = 0
  const oauthClient = callbackClient({ state, onCallback: () => (callbackCount += 1) })
  let stores = await createDurableSidecarStores({ filePath, encryptionKey })
  let service = durableService(stores, oauthClient, state)
  const started = await service.start({ ownerId: 'user:owner-a', handle: 'alice.bsky.social' })
  assert.equal(new URL(started.authorization_url).searchParams.get('state'), state)
  const connected = await service.callback({
    ownerId: 'user:owner-a',
    query: `code=${sensitiveCode}&state=${state}`,
  })
  await stores.close()

  stores = await createDurableSidecarStores({ filePath, encryptionKey })
  service = durableService(stores, oauthClient, state)
  const replayed = await service.callback({
    ownerId: 'user:owner-a',
    query: `code=${sensitiveCode}&state=${state}`,
  })
  assert.deepEqual(replayed, connected)
  assert.equal(callbackCount, 1)
  const encrypted = await readFile(filePath, 'utf8')
  for (const forbidden of [state, sensitiveCode, connected.connection_ref, connected.external_subject]) {
    assert.equal(encrypted.includes(forbidden), false)
  }
  await stores.close()
})

test('processing callback without a receipt fails closed after restart', async () => {
  const directory = await temporaryDirectory()
  const filePath = join(directory, 'processing-callback.enc')
  const encryptionKey = randomBytes(32)
  const state = 'p'.repeat(43)
  let callbackCount = 0
  let stores = await createDurableSidecarStores({ filePath, encryptionKey })
  await stores.ownerStates.create(state, {
    ownerId: 'user:owner-a',
    expiresAt: Date.now() + 60_000,
  })
  await stores.ownerStates.claim(state, { ownerId: 'user:owner-a', now: Date.now() })
  await stores.close()

  stores = await createDurableSidecarStores({ filePath, encryptionKey })
  const service = durableService(
    stores,
    callbackClient({ state, onCallback: () => (callbackCount += 1) }),
    state,
  )
  await assert.rejects(
    service.callback({ ownerId: 'user:owner-a', query: `code=unused&state=${state}` }),
    (error) => error.code === 'oauth_callback_in_progress',
  )
  assert.equal(callbackCount, 0)
  await stores.close()
})

test('terminal revocation proof survives restart and is not replayed remotely', async () => {
  const directory = await temporaryDirectory()
  const filePath = join(directory, 'terminal-revoke.enc')
  const encryptionKey = randomBytes(32)
  const state = 'v'.repeat(43)
  let revokeCount = 0
  const oauthClient = callbackClient({
    state,
    onCallback: () => {},
    onRevoke: () => (revokeCount += 1),
  })
  let stores = await createDurableSidecarStores({ filePath, encryptionKey })
  let service = durableService(stores, oauthClient, state)
  const started = await service.start({ ownerId: 'user:owner-a', handle: 'alice.bsky.social' })
  const connected = await service.callback({
    ownerId: 'user:owner-a',
    query: `code=approved&state=${new URL(started.authorization_url).searchParams.get('state')}`,
  })
  const revoked = await service.revoke({
    ownerId: 'user:owner-a',
    connectionRef: connected.connection_ref,
  })
  assert.equal(revoked.revocation_proof, 'provider_confirmed')
  await stores.close()

  stores = await createDurableSidecarStores({ filePath, encryptionKey })
  service = durableService(stores, oauthClient, state)
  const replayed = await service.revoke({
    ownerId: 'user:owner-a',
    connectionRef: connected.connection_ref,
  })
  assert.equal(replayed.revocation_proof, 'sidecar_terminal')
  assert.equal(revokeCount, 1)
  await stores.close()
})

test('startup prunes expired transient records and individual records are bounded', async () => {
  const directory = await temporaryDirectory()
  const filePath = join(directory, 'bounded-store.enc')
  const encryptionKey = randomBytes(32)
  let stores = await createDurableSidecarStores({ filePath, encryptionKey })
  const now = Date.now()
  await stores.ownerStates.create('expired-owner-state', {
    ownerId: 'user:owner-a',
    expiresAt: now - 1,
  })
  const connection = await stores.connections.bind({
    ownerId: 'user:owner-a',
    did: 'did:plc:alice123',
    now: now - 100,
  })
  await stores.sessionLeases.create({
    ownerId: 'user:owner-a',
    connectionRef: connection.connectionRef,
    did: connection.did,
    session: { secret: 'runtime-only' },
    now: now - 100,
    ttlMs: 1,
  })
  await assert.rejects(
    stores.oauthStateStore.set('too-large', { value: 'x'.repeat(1_100_000) }),
    (error) => error.code === 'sidecar_store_capacity_exceeded',
  )
  await stores.close()

  stores = await createDurableSidecarStores({ filePath, encryptionKey })
  assert.equal(stores.ownerStates.size, 0)
  assert.equal(stores.sessionLeases.size, 0)
  assert.equal(stores.oauthStateStore.size, 0)
  await stores.close()
})

test('abandoned official states expire and each owner has a pending-start quota', async () => {
  const directory = await temporaryDirectory()
  const filePath = join(directory, 'oauth-state-retention.enc')
  const encryptionKey = randomBytes(32)
  const stores = await createDurableSidecarStores({ filePath, encryptionKey })
  let now = Date.now()
  const officialStates = new DurableSecretStore(stores.database, 'oauth_state', {
    clock: () => now,
    oauthStateTtlMs: 60_000,
    maxOauthStates: 2,
  })
  await officialStates.set('official-one', { verifier: 'one' })
  await officialStates.set('official-two', { verifier: 'two' })
  await assert.rejects(
    officialStates.set('official-three', { verifier: 'three' }),
    (error) => error.code === 'oauth_state_capacity_exceeded' && error.status === 429,
  )
  now += 60_001
  await officialStates.set('official-three', { verifier: 'three' })
  assert.equal(await officialStates.get('official-one'), undefined)
  assert.deepEqual(await officialStates.get('official-three'), { verifier: 'three' })

  for (let index = 0; index < 8; index += 1) {
    await stores.ownerStates.create(`owner-state-${index}`, {
      ownerId: 'user:owner-a',
      expiresAt: Date.now() + 60_000,
    })
  }
  await assert.rejects(
    stores.ownerStates.create('owner-state-over-quota', {
      ownerId: 'user:owner-a',
      expiresAt: Date.now() + 60_000,
    }),
    (error) => error.code === 'oauth_start_limit_reached' && error.status === 429,
  )
  await stores.ownerStates.create('different-owner-state', {
    ownerId: 'user:owner-b',
    expiresAt: Date.now() + 60_000,
  })
  await stores.close()
})

test('expired official-state reads cannot delete a concurrent replacement', async () => {
  const directory = await temporaryDirectory()
  const filePath = join(directory, 'oauth-state-read-race.enc')
  const encryptionKey = randomBytes(32)
  const stores = await createDurableSidecarStores({ filePath, encryptionKey })
  let now = Date.now()
  const officialStates = new DurableSecretStore(stores.database, 'oauth_state', {
    clock: () => now,
    oauthStateTtlMs: 60_000,
  })
  await officialStates.set('reused-state-key', { verifier: 'expired' })

  now += 60_001
  const expiredRead = officialStates.get('reused-state-key')
  const replacementWrite = officialStates.set('reused-state-key', { verifier: 'fresh' })
  assert.equal(await expiredRead, undefined)
  await replacementWrite
  assert.deepEqual(await officialStates.get('reused-state-key'), { verifier: 'fresh' })
  await stores.close()
})

test('official-state records carry their configured absolute expiry', async () => {
  const directory = await temporaryDirectory()
  const filePath = join(directory, 'oauth-state-custom-ttl.enc')
  const encryptionKey = randomBytes(32)
  const stores = await createDurableSidecarStores({ filePath, encryptionKey })
  const now = Date.now() - 20 * 60 * 1000
  const officialStates = new DurableSecretStore(stores.database, 'oauth_state', {
    clock: () => now,
    oauthStateTtlMs: 30 * 60 * 1000,
  })
  await officialStates.set('longer-lived-state', { verifier: 'still-valid' })

  await stores.oauthSessionStore.set('did:plc:mutation123', { session: 'opaque' })
  assert.deepEqual(await officialStates.get('longer-lived-state'), {
    verifier: 'still-valid',
  })
  await stores.close()
})

test('startup migrates raw and transitional OAuth state records into bounded envelopes', async () => {
  const directory = await temporaryDirectory()
  const filePath = join(directory, 'oauth-state-legacy-migration.enc')
  const encryptionKey = randomBytes(32)
  let stores = await createDurableSidecarStores({ filePath, encryptionKey })
  await stores.database.set('oauth_state', 'legacy-raw', { verifier: 'legacy-raw-value' })
  await stores.database.set('oauth_state', 'legacy-timed-expired', {
    format: 'timed-v1',
    storedAt: Date.now() - 16 * 60 * 1000,
    value: { verifier: 'legacy-timed-value' },
  })
  await stores.close()

  const reopenedAfter = Date.now()
  stores = await createDurableSidecarStores({ filePath, encryptionKey })
  const migrated = await stores.database.get('oauth_state', 'legacy-raw')
  assert.equal(migrated.format, 'timed-v1')
  assert.ok(migrated.storedAt >= reopenedAfter)
  assert.equal(migrated.expiresAt - migrated.storedAt, 15 * 60 * 1000)
  assert.deepEqual(await stores.oauthStateStore.get('legacy-raw'), {
    verifier: 'legacy-raw-value',
  })
  assert.equal(await stores.oauthStateStore.get('legacy-timed-expired'), undefined)
  await stores.close()
})

test('OAuth state envelopes enforce the complete persisted record limit', async () => {
  const directory = await temporaryDirectory()
  const filePath = join(directory, 'oauth-state-envelope-limit.enc')
  const encryptionKey = randomBytes(32)
  let stores = await createDurableSidecarStores({ filePath, encryptionKey })
  await assert.rejects(
    stores.oauthStateStore.set('near-record-limit', { blob: 'x'.repeat(1_048_500) }),
    (error) => error.code === 'sidecar_store_capacity_exceeded' && error.status === 503,
  )
  assert.equal(stores.oauthStateStore.size, 0)
  await stores.close()

  stores = await createDurableSidecarStores({ filePath, encryptionKey })
  assert.equal(stores.oauthStateStore.size, 0)
  await stores.close()
})

test('startup discards oversized ephemeral wrappers written by the prior format', async () => {
  const directory = await temporaryDirectory()
  const filePath = join(directory, 'oauth-state-oversized-wrapper.enc')
  const encryptionKey = randomBytes(32)
  let stores = await createDurableSidecarStores({ filePath, encryptionKey })
  await stores.database.mutate('oauth_state', (records) => {
    records.set('prior-oversized-wrapper', {
      format: 'timed-v1',
      storedAt: Date.now(),
      value: { blob: 'x'.repeat(1_048_500) },
    })
  })
  await stores.close()

  stores = await createDurableSidecarStores({ filePath, encryptionKey })
  assert.equal(stores.oauthStateStore.size, 0)
  await stores.close()
  stores = await createDurableSidecarStores({ filePath, encryptionKey })
  assert.equal(stores.oauthStateStore.size, 0)
  await stores.close()
})

test('startup bounds expanded legacy OAuth states by count and snapshot size', async () => {
  const directory = await temporaryDirectory()
  const countFilePath = join(directory, 'oauth-state-legacy-count.enc')
  const encryptionKey = randomBytes(32)
  let stores = await createDurableSidecarStores({ filePath: countFilePath, encryptionKey })
  await stores.database.mutate('oauth_state', (records) => {
    for (let index = 0; index < 1_025; index += 1) {
      records.set(`legacy-${String(index).padStart(4, '0')}`, { verifier: index })
    }
  })
  await stores.close()

  stores = await createDurableSidecarStores({ filePath: countFilePath, encryptionKey })
  assert.equal(stores.oauthStateStore.size, 1_024)
  await stores.close()

  const sizeFilePath = join(directory, 'oauth-state-legacy-size.enc')
  stores = await createDurableSidecarStores({ filePath: sizeFilePath, encryptionKey })
  for (let index = 0; index < 8; index += 1) {
    await stores.database.set('oauth_state', `s${index}`, {
      blob: `${index}${'x'.repeat(1_048_459)}`,
    })
  }
  await stores.close()

  stores = await createDurableSidecarStores({ filePath: sizeFilePath, encryptionKey })
  assert.ok(stores.oauthStateStore.size < 8)
  assert.ok(stores.oauthStateStore.size > 0)
  await stores.close()
  stores = await createDurableSidecarStores({ filePath: sizeFilePath, encryptionKey })
  assert.ok(stores.oauthStateStore.size < 8)
  await stores.close()
})

test('exclusive process lock rejects a second writer and is released on close', async () => {
  const directory = await temporaryDirectory()
  const filePath = join(directory, 'locked.enc')
  const encryptionKey = randomBytes(32)
  const first = await EncryptedAtomicStore.open({ filePath, encryptionKey })

  await assert.rejects(
    EncryptedAtomicStore.open({ filePath, encryptionKey }),
    (error) => error.code === 'sidecar_store_locked' && error.status === 503,
  )

  await first.close()
  const reopened = await EncryptedAtomicStore.open({ filePath, encryptionKey })
  await reopened.close()
})

test('persistent lock recovers when a container reuses the prior process PID', async () => {
  const directory = await temporaryDirectory()
  const filePath = join(directory, 'restarted-container.enc')
  const encryptionKey = randomBytes(32)
  await writeFile(
    `${filePath}.lock`,
    JSON.stringify({
      version: 1,
      pid: process.pid,
      nonce: 'prior-container-nonce',
      process_identity: 'linux:prior-container:start-ticks',
      created_at: new Date(Date.now() - 60_000).toISOString(),
    }),
    { encoding: 'utf8', mode: 0o600 },
  )

  const reopened = await EncryptedAtomicStore.open({ filePath, encryptionKey })
  await reopened.close()

  await writeFile(
    `${filePath}.lock`,
    JSON.stringify({
      version: 1,
      pid: process.pid,
      nonce: 'legacy-prior-container-nonce',
      created_at: new Date(0).toISOString(),
    }),
    { encoding: 'utf8', mode: 0o600 },
  )
  const legacyReopened = await EncryptedAtomicStore.open({ filePath, encryptionKey })
  await legacyReopened.close()
})

test('simultaneous stale-lock recovery admits exactly one writer', async () => {
  const directory = await temporaryDirectory()
  const filePath = join(directory, 'concurrent-recovery.enc')
  const encryptionKey = randomBytes(32)
  await writeFile(
    `${filePath}.lock`,
    JSON.stringify({
      version: 1,
      pid: process.pid,
      nonce: 'stale-shared-lock',
      process_identity: 'linux:prior-container:start-ticks',
      created_at: new Date(Date.now() - 60_000).toISOString(),
    }),
  )

  const results = await Promise.allSettled([
    EncryptedAtomicStore.open({ filePath, encryptionKey }),
    EncryptedAtomicStore.open({ filePath, encryptionKey }),
  ])
  const opened = results.filter((result) => result.status === 'fulfilled')
  const rejected = results.filter((result) => result.status === 'rejected')
  assert.equal(opened.length, 1)
  assert.equal(rejected.length, 1)
  assert.equal(rejected[0].reason.code, 'sidecar_store_locked')
  await opened[0].value.close()
})

test('old empty lock and recovery markers are safely reclaimed', async () => {
  const directory = await temporaryDirectory()
  const filePath = join(directory, 'empty-lock.enc')
  const encryptionKey = randomBytes(32)
  const old = new Date(Date.now() - 60_000)
  await writeFile(`${filePath}.lock.recovery`, '')
  await utimes(`${filePath}.lock.recovery`, old, old)

  let opened = await EncryptedAtomicStore.open({ filePath, encryptionKey })
  await opened.close()

  await writeFile(`${filePath}.lock`, '')
  await utimes(`${filePath}.lock`, old, old)
  opened = await EncryptedAtomicStore.open({ filePath, encryptionKey })
  await opened.close()
})

test('ciphertext tampering and wrong keys fail closed', async () => {
  const directory = await temporaryDirectory()
  const filePath = join(directory, 'tamper.enc')
  const encryptionKey = randomBytes(32)
  const database = await EncryptedAtomicStore.open({ filePath, encryptionKey })
  await database.set('oauth_session', 'did:plc:alice123', { refresh: 'secret' })
  await database.close()

  const envelope = JSON.parse(await readFile(filePath, 'utf8'))
  envelope.ciphertext = `${envelope.ciphertext.slice(0, -1)}${
    envelope.ciphertext.endsWith('A') ? 'B' : 'A'
  }`
  await writeFile(filePath, JSON.stringify(envelope), 'utf8')
  await assert.rejects(
    EncryptedAtomicStore.open({ filePath, encryptionKey }),
    (error) => error.code === 'sidecar_store_decryption_failed',
  )
  await assert.rejects(
    EncryptedAtomicStore.open({ filePath, encryptionKey: randomBytes(32) }),
    (error) => error.code === 'sidecar_store_decryption_failed',
  )
})

test('oversized-state recovery cannot mask duplicates or weaken session limits', async () => {
  const directory = await temporaryDirectory()
  const encryptionKey = randomBytes(32)
  const duplicatePath = join(directory, 'oversized-state-duplicate.enc')
  await writeAuthenticatedSnapshot(duplicatePath, encryptionKey, {
    oauth_state: [
      [
        'duplicate-state',
        {
          format: 'timed-v1',
          storedAt: Date.now(),
          value: { blob: 'x'.repeat(1_048_500) },
        },
      ],
      ['duplicate-state', { verifier: 'must-not-mask-duplicate' }],
    ],
  })
  await assert.rejects(
    EncryptedAtomicStore.open({ filePath: duplicatePath, encryptionKey }),
    (error) => error.code === 'sidecar_store_decryption_failed',
  )

  const sessionPath = join(directory, 'oversized-session.enc')
  await writeAuthenticatedSnapshot(sessionPath, encryptionKey, {
    oauth_session: [
      ['did:plc:oversized123', { refreshToken: 'x'.repeat(1_048_570) }],
    ],
  })
  await assert.rejects(
    EncryptedAtomicStore.open({ filePath: sessionPath, encryptionKey }),
    (error) => error.code === 'sidecar_store_capacity_exceeded',
  )
})

async function temporaryDirectory() {
  const directory = await mkdtemp(join(tmpdir(), 'feed-passport-atproto-'))
  temporaries.push(directory)
  return directory
}

async function writeAuthenticatedSnapshot(filePath, encryptionKey, overrides) {
  const namespaceNames = [
    'oauth_state',
    'oauth_session',
    'owner_state',
    'owner_connection',
    'session_lease',
    'oauth_receipt',
  ]
  const snapshot = {
    version: 1,
    revision: 0,
    namespaces: namespaceNames.map((namespace) => [namespace, overrides[namespace] ?? []]),
  }
  const iv = randomBytes(12)
  const cipher = createCipheriv('aes-256-gcm', encryptionKey, iv)
  cipher.setAAD(Buffer.from('feed-passport:atproto-sidecar-store:v1:local-v1', 'utf8'))
  const plaintext = Buffer.from(JSON.stringify(snapshot), 'utf8')
  const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()])
  plaintext.fill(0)
  await writeFile(
    filePath,
    JSON.stringify({
      version: 1,
      algorithm: 'A256GCM',
      key_id: 'local-v1',
      iv: iv.toString('base64url'),
      ciphertext: ciphertext.toString('base64url'),
      tag: cipher.getAuthTag().toString('base64url'),
    }),
    'utf8',
  )
}

function callbackClient({ state, onCallback, onRevoke = () => {} }) {
  return {
    clientMetadata: { client_id: 'https://feed.example/metadata' },
    jwks: { keys: [{ kty: 'EC', crv: 'P-256', x: 'x', y: 'y' }] },
    async authorize(handle, options) {
      const url = new URL('https://pds.example/oauth/authorize')
      url.searchParams.set('login_hint', handle)
      url.searchParams.set('state', options.state)
      return url
    },
    async callback(params) {
      onCallback()
      return { state: params.get('state'), session: { did: 'did:plc:alice123' } }
    },
    async restore(did) {
      return { did }
    },
    async revoke() {
      onRevoke()
    },
  }
}

function durableService(stores, oauthClient, state) {
  return new AtprotoOAuthService({
    oauthClient,
    ownerStates: stores.ownerStates,
    connections: stores.connections,
    sessionLeases: stores.sessionLeases,
    operationExecutor: { async execute() { return {} } },
    randomState: () => state,
  })
}
