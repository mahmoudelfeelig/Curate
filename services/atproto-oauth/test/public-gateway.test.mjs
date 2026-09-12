import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { test } from 'node:test'

import { startPublicGateway } from '../src/public-gateway.mjs'

test('gateway exposes only the four public GET paths', async (context) => {
  const seen = []
  const upstream = await listeningServer((request, response) => {
    seen.push(request.url)
    response.statusCode = 200
    response.setHeader('content-type', 'application/json')
    response.end(JSON.stringify({ path: request.url }))
  })
  const gateway = await startGateway(upstream)
  context.after(() => Promise.all([gateway.close(), closeServer(upstream)]))
  const origin = serverOrigin(gateway.server)

  for (const path of [
    '/health',
    '/oauth/atproto/client-metadata.json',
    '/oauth/atproto/jwks.json',
    '/oauth/atproto/callback?state=s&code=c',
  ]) {
    assert.equal((await fetch(origin + path, { redirect: 'manual' })).status, 200)
  }
  assert.deepEqual(seen, [
    '/health',
    '/oauth/atproto/client-metadata.json',
    '/oauth/atproto/jwks.json',
    '/oauth/atproto/callback?state=s&code=c',
  ])
})

test('private, unknown, noncanonical, and non-GET requests never reach upstream', async (context) => {
  let upstreamCalls = 0
  const upstream = await listeningServer((_request, response) => {
    upstreamCalls += 1
    response.end('unexpected')
  })
  const gateway = await startGateway(upstream)
  context.after(() => Promise.all([gateway.close(), closeServer(upstream)]))
  const origin = serverOrigin(gateway.server)

  assert.equal((await fetch(`${origin}/v1/oauth/atproto/start`)).status, 404)
  assert.equal((await fetch(`${origin}/unknown`)).status, 404)
  assert.equal((await fetch(`${origin}/health?probe=1`)).status, 400)
  assert.equal((await fetch(`${origin}/health`, { method: 'POST' })).status, 405)
  assert.equal(upstreamCalls, 0)
})

test('incoming account and service credentials are stripped before proxying', async (context) => {
  let observedHeaders
  const upstream = await listeningServer((request, response) => {
    observedHeaders = request.headers
    response.statusCode = 200
    response.setHeader('cache-control', 'public, max-age=300')
    response.setHeader('set-cookie', 'must-not-cross=1')
    response.setHeader('x-internal-debug', 'must-not-cross')
    response.end('{}')
  })
  const gateway = await startGateway(upstream)
  context.after(() => Promise.all([gateway.close(), closeServer(upstream)]))

  const response = await fetch(`${serverOrigin(gateway.server)}/health`, {
    headers: {
      authorization: 'Bearer internal-secret',
      cookie: 'session=owner-session',
      dpop: 'private-proof',
      'x-feed-passport-owner': 'user:owner',
      'x-forwarded-for': '203.0.113.7',
    },
  })

  assert.equal(response.status, 200)
  for (const name of [
    'authorization',
    'cookie',
    'dpop',
    'x-feed-passport-owner',
    'x-forwarded-for',
  ]) {
    assert.equal(observedHeaders[name], undefined)
  }
  assert.equal(response.headers.get('cache-control'), 'public, max-age=300')
  assert.equal(response.headers.get('set-cookie'), null)
  assert.equal(response.headers.get('x-internal-debug'), null)
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff')
})

test('callback redirects and privacy headers cross unchanged without following the redirect', async (context) => {
  const target = 'http://127.0.0.1:5173/oauth/callback?state=s&code=c'
  const upstream = await listeningServer((_request, response) => {
    response.statusCode = 302
    response.setHeader('location', target)
    response.setHeader('cache-control', 'no-store, max-age=0')
    response.setHeader('referrer-policy', 'no-referrer')
    response.setHeader('pragma', 'no-cache')
    response.end()
  })
  const gateway = await startGateway(upstream)
  context.after(() => Promise.all([gateway.close(), closeServer(upstream)]))

  const response = await fetch(
    `${serverOrigin(gateway.server)}/oauth/atproto/callback?state=s&code=c`,
    { redirect: 'manual' },
  )
  assert.equal(response.status, 302)
  assert.equal(response.headers.get('location'), target)
  assert.equal(response.headers.get('cache-control'), 'no-store, max-age=0')
  assert.equal(response.headers.get('referrer-policy'), 'no-referrer')
  assert.equal(response.headers.get('pragma'), 'no-cache')
})

test('oversized upstream responses fail closed without being relayed', async (context) => {
  const upstream = await listeningServer((_request, response) => {
    response.statusCode = 200
    response.end(Buffer.alloc(128 * 1024 + 1, 1))
  })
  const gateway = await startGateway(upstream)
  context.after(() => Promise.all([gateway.close(), closeServer(upstream)]))

  const response = await fetch(`${serverOrigin(gateway.server)}/health`)
  assert.equal(response.status, 502)
  assert.deepEqual(await response.json(), { error: 'public_upstream_response_too_large' })
})

test('gateway requires loopback bind and upstream boundaries', async () => {
  await assert.rejects(
    startPublicGateway({ host: '0.0.0.0', port: 0 }),
    /loopback host/,
  )
  await assert.rejects(
    startPublicGateway({ port: 0, upstreamOrigin: 'https://example.com' }),
    /loopback HTTP origin/,
  )
  await assert.rejects(
    startPublicGateway({ port: 0, upstreamOrigin: 'http://127.0.0.1:4310/v1' }),
    /loopback HTTP origin/,
  )
})

async function startGateway(upstream) {
  return startPublicGateway({
    host: '127.0.0.1',
    port: 0,
    upstreamOrigin: serverOrigin(upstream),
    installSignalHandlers: false,
  })
}

function listeningServer(listener) {
  const server = createServer(listener)
  return new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      server.off('error', reject)
      resolve(server)
    })
  })
}

function serverOrigin(server) {
  const address = server.address()
  return `http://127.0.0.1:${address.port}`
}

function closeServer(server) {
  if (!server.listening) return Promise.resolve()
  return new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()))
  })
}
