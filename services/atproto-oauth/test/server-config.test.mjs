import assert from 'node:assert/strict'
import { randomBytes } from 'node:crypto'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, test } from 'node:test'
import { inspect } from 'node:util'

import { loadSidecarConfig, SidecarServerConfig } from '../src/config.mjs'
import { createSingleProcessRequestLock, startSidecarServer } from '../src/server.mjs'

const temporaries = []

afterEach(async () => {
  await Promise.all(temporaries.splice(0).map((directory) => rm(directory, { recursive: true })))
})

test('environment config derives exact public metadata and redacts every secret', async () => {
  const directory = await temporaryDirectory()
  const privateKeyPath = join(directory, 'oauth-private.jwk')
  await writeFile(privateKeyPath, JSON.stringify({ kty: 'EC', d: 'private-coordinate' }))
  const environment = validEnvironment(directory, privateKeyPath)

  const config = await loadSidecarConfig(environment)

  assert.equal(
    config.client.clientId,
    'https://feed.example/oauth/atproto/client-metadata.json',
  )
  assert.equal(config.client.jwksUri, 'https://feed.example/oauth/atproto/jwks.json')
  assert.equal(config.client.redirectUri, 'https://feed.example/oauth/atproto/callback')
  assert.equal(config.appCallbackUri, 'http://127.0.0.1:5173/oauth/callback')
  assert.equal(config.internalOrigin, 'http://127.0.0.1:4310')
  assert.equal(config.client.scope, 'atproto transition:generic')
  assert.equal(config.storeKey.length, 32)
  const rendered = JSON.stringify(config)
  assert.equal(rendered.includes(environment.FEED_PASSPORT_ATPROTO_STORE_KEY_B64), false)
  assert.equal(rendered.includes(environment.FEED_PASSPORT_ATPROTO_SIDECAR_INTERNAL_SECRET), false)
  assert.equal(rendered.includes('private-coordinate'), false)
  const inspected = inspect(config)
  assert.equal(inspected.includes(environment.FEED_PASSPORT_ATPROTO_SIDECAR_INTERNAL_SECRET), false)
  assert.equal(inspected.includes('private-coordinate'), false)
})

test('configuration fails closed for partial, non-HTTPS, and weak inputs', async () => {
  const directory = await temporaryDirectory()
  const privateKeyPath = join(directory, 'oauth-private.jwk')
  await writeFile(privateKeyPath, 'x'.repeat(64))
  const valid = validEnvironment(directory, privateKeyPath)

  await assert.rejects(
    loadSidecarConfig({ ...valid, FEED_PASSPORT_ATPROTO_PUBLIC_ORIGIN: 'http://feed.example' }),
    (error) => error.code === 'sidecar_configuration_invalid',
  )
  await assert.rejects(
    loadSidecarConfig({ ...valid, FEED_PASSPORT_ATPROTO_SIDECAR_INTERNAL_SECRET: 'short' }),
    (error) => error.code === 'sidecar_configuration_invalid',
  )
  await assert.rejects(
    loadSidecarConfig({ ...valid, FEED_PASSPORT_ATPROTO_STORE_PATH: './relative.enc' }),
    (error) => error.code === 'sidecar_configuration_invalid',
  )
  await assert.rejects(
    loadSidecarConfig({
      ...valid,
      FEED_PASSPORT_ATPROTO_APP_CALLBACK_URI: 'http://app.example/oauth/callback',
    }),
    (error) => error.code === 'sidecar_configuration_invalid',
  )
  await assert.rejects(
    loadSidecarConfig({
      ...valid,
      FEED_PASSPORT_ATPROTO_APP_CALLBACK_URI: 'https://app.example/not-the-callback',
    }),
    (error) => error.code === 'sidecar_configuration_invalid',
  )
  for (const scope of ['atproto', 'atproto transition:generic extra', 'atproto atproto']) {
    await assert.rejects(
      loadSidecarConfig({ ...valid, FEED_PASSPORT_ATPROTO_SCOPE: scope }),
      (error) => error.code === 'sidecar_configuration_invalid',
    )
  }
  const reordered = await loadSidecarConfig({
    ...valid,
    FEED_PASSPORT_ATPROTO_SCOPE: 'transition:generic atproto',
  })
  assert.equal(reordered.client.scope, 'atproto transition:generic')
  const missing = { ...valid }
  delete missing.FEED_PASSPORT_ATPROTO_PRIVATE_KEY_FILE
  await assert.rejects(
    loadSidecarConfig(missing),
    (error) => error.code === 'sidecar_configuration_invalid',
  )
})

test('runnable server owns lifecycle, durable store, and health endpoint', async () => {
  const directory = await temporaryDirectory()
  const config = new SidecarServerConfig({
    host: '127.0.0.1',
    port: 0,
    internalOrigin: 'http://127.0.0.1:0',
    publicOrigin: 'https://feed.example/',
    storePath: join(directory, 'server.enc'),
    storeKey: randomBytes(32),
    storeKeyId: 'test-v1',
    internalServiceSecret: 'internal-sidecar-secret-with-at-least-32-characters',
    appCallbackUri: 'http://127.0.0.1:5173/oauth/callback',
    privateKeys: [{ kid: 'test-key', importable: 'x'.repeat(64) }],
    client: { clientId: 'https://feed.example/metadata' },
  })
  const calls = []
  const runtimeFactory = async (input) => {
    calls.push(input)
    input.sessionLeases.setSessionRestorer(async (did) => ({ did }))
    return {
      nodeRequestListener(_request, response) {
        response.statusCode = 200
        response.setHeader('content-type', 'application/json')
        response.end(JSON.stringify({ status: 'ready', service: 'atproto-oauth-sidecar' }))
      },
    }
  }

  const running = await startSidecarServer({
    config,
    runtimeFactory,
    installSignalHandlers: false,
  })
  const address = running.address()
  const response = await fetch(`http://127.0.0.1:${address.port}/health`)

  assert.equal(response.status, 200)
  assert.deepEqual(await response.json(), {
    status: 'ready',
    service: 'atproto-oauth-sidecar',
  })
  assert.equal(calls.length, 1)
  assert.equal(typeof calls[0].requestLock, 'function')
  assert.equal(config.storeKey.every((value) => value === 0), true)
  await running.close()
})

test('single-process request lock serializes the same refresh key', async () => {
  const lock = createSingleProcessRequestLock()
  const events = []
  let releaseFirst
  const gate = new Promise((resolve) => {
    releaseFirst = resolve
  })
  const first = lock('did:plc:alice', async () => {
    events.push('first:start')
    await gate
    events.push('first:end')
  })
  const second = lock('did:plc:alice', async () => {
    events.push('second:start')
    events.push('second:end')
  })
  await new Promise((resolve) => setImmediate(resolve))
  assert.deepEqual(events, ['first:start'])
  releaseFirst()
  await Promise.all([first, second])
  assert.deepEqual(events, ['first:start', 'first:end', 'second:start', 'second:end'])
})

function validEnvironment(directory, privateKeyPath) {
  return {
    FEED_PASSPORT_ATPROTO_PUBLIC_ORIGIN: 'https://feed.example',
    FEED_PASSPORT_ATPROTO_HOST: '127.0.0.1',
    FEED_PASSPORT_ATPROTO_PORT: '4310',
    FEED_PASSPORT_ATPROTO_STORE_PATH: join(directory, 'sidecar.enc'),
    FEED_PASSPORT_ATPROTO_STORE_KEY_B64: randomBytes(32).toString('base64url'),
    FEED_PASSPORT_ATPROTO_STORE_KEY_ID: 'test-v1',
    FEED_PASSPORT_ATPROTO_SIDECAR_INTERNAL_SECRET:
      'internal-sidecar-secret-with-at-least-32-characters',
    FEED_PASSPORT_ATPROTO_APP_CALLBACK_URI: 'http://127.0.0.1:5173/oauth/callback',
    FEED_PASSPORT_ATPROTO_PRIVATE_KEY_FILE: privateKeyPath,
    FEED_PASSPORT_ATPROTO_PRIVATE_KEY_ID: 'test-key',
  }
}

async function temporaryDirectory() {
  const directory = await mkdtemp(join(tmpdir(), 'feed-passport-atproto-config-'))
  temporaries.push(directory)
  return directory
}
