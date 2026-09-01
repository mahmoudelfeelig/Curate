import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  createSharedSecretAuthenticator,
  createSidecarHandler,
} from '../src/http.mjs'

const SECRET = 'internal-sidecar-secret-with-at-least-32-characters'
const APP_CALLBACK = 'https://app.example/oauth/callback'

test('metadata, JWKS, and health are public while workflow routes require authentication', async () => {
  const calls = []
  const handler = createSidecarHandler({
    service: fakeService(calls),
    authenticate: createSharedSecretAuthenticator(SECRET),
    appCallbackUri: APP_CALLBACK,
  })

  const metadata = await handler(
    new Request('https://sidecar.example/oauth/atproto/client-metadata.json'),
  )
  assert.equal(metadata.status, 200)
  assert.equal((await metadata.json()).dpop_bound_access_tokens, true)
  assert.equal(metadata.headers.get('cache-control'), 'public, max-age=300')

  const unauthorized = await handler(
    jsonRequest('https://sidecar.example/v1/oauth/atproto/start', { handle: 'a.bsky.social' }),
  )
  assert.equal(unauthorized.status, 401)
  assert.equal((await unauthorized.json()).error, 'sidecar_authentication_required')

  const authorized = await handler(
    jsonRequest(
      'https://sidecar.example/v1/oauth/atproto/start',
      { handle: 'a.bsky.social' },
      { authorization: `Bearer ${SECRET}`, 'x-feed-passport-owner': 'user:one' },
    ),
  )
  assert.equal(authorized.status, 200)
  assert.equal(authorized.headers.get('cache-control'), 'no-store, max-age=0')
  assert.deepEqual(calls, [
    { route: 'start', ownerId: 'user:one', handle: 'a.bsky.social' },
  ])
})

test('JSON credential material is rejected before route execution', async () => {
  const calls = []
  const handler = createSidecarHandler({
    service: fakeService(calls),
    authenticate: async () => ({ ownerId: 'user:one' }),
    appCallbackUri: APP_CALLBACK,
  })
  const response = await handler(
    jsonRequest('https://sidecar.example/v1/oauth/atproto/sessions/execute', {
      lease_ref: 'l'.repeat(43),
      operation: 'graph.follow',
      input: { access_token: 'should-never-arrive' },
    }),
  )
  assert.equal(response.status, 400)
  assert.equal((await response.json()).error, 'credential_material_denied')
  assert.deepEqual(calls, [])
})

test('strict routes reject wrong methods and content types', async () => {
  const handler = createSidecarHandler({
    service: fakeService([]),
    authenticate: async () => ({ ownerId: 'user:one' }),
    appCallbackUri: APP_CALLBACK,
  })
  const method = await handler(
    new Request('https://sidecar.example/v1/oauth/atproto/start', { method: 'GET' }),
  )
  assert.equal(method.status, 405)

  const contentType = await handler(
    new Request('https://sidecar.example/v1/oauth/atproto/start', {
      method: 'POST',
      headers: { 'content-type': 'text/plain' },
      body: '{}',
    }),
  )
  assert.equal(contentType.status, 415)
})

test('handler construction fails closed without an owner authenticator', () => {
  assert.throws(
    () => createSidecarHandler({ service: fakeService([]), appCallbackUri: APP_CALLBACK }),
    (error) => error.code === 'sidecar_auth_unconfigured' && error.status === 503,
  )
  assert.throws(
    () => createSharedSecretAuthenticator('short'),
    (error) => error.code === 'sidecar_auth_unconfigured' && error.status === 503,
  )
})

test('public callback relays only the canonical OAuth result with privacy headers', async () => {
  let authenticationCalls = 0
  const handler = createSidecarHandler({
    service: fakeService([]),
    authenticate: async () => {
      authenticationCalls += 1
      return { ownerId: 'user:one' }
    },
    appCallbackUri: APP_CALLBACK,
  })
  const state = 's'.repeat(43)
  const response = await handler(
    new Request(
      `https://sidecar.example/oauth/atproto/callback?iss=${encodeURIComponent('https://pds.example')}&state=${state}&code=approved`,
    ),
  )

  assert.equal(response.status, 302)
  assert.equal(
    response.headers.get('location'),
    `https://app.example/oauth/callback?state=${state}&code=approved&iss=https%3A%2F%2Fpds.example`,
  )
  assert.equal(response.headers.get('cache-control'), 'no-store, max-age=0')
  assert.equal(response.headers.get('referrer-policy'), 'no-referrer')
  assert.equal(response.headers.get('pragma'), 'no-cache')
  assert.equal(authenticationCalls, 0)
})

test('public callback relays an OAuth error but rejects duplicates and unknown inputs', async () => {
  const handler = createSidecarHandler({
    service: fakeService([]),
    authenticate: async () => ({ ownerId: 'user:one' }),
    appCallbackUri: APP_CALLBACK,
  })
  const state = 's'.repeat(43)
  const denied = await handler(
    new Request(
      `https://sidecar.example/oauth/atproto/callback?state=${state}&error=access_denied&error_description=No`,
    ),
  )
  assert.equal(denied.status, 302)
  assert.equal(
    denied.headers.get('location'),
    `https://app.example/oauth/callback?state=${state}&error=access_denied&error_description=No`,
  )

  for (const query of [
    `state=${state}&state=${state}&code=approved`,
    `state=${state}&code=approved&next=https%3A%2F%2Fevil.example`,
    `state=${state}&code=approved%0Ainjected`,
    `state=${state}&code=approved&error=access_denied`,
  ]) {
    const response = await handler(
      new Request(`https://sidecar.example/oauth/atproto/callback?${query}`),
    )
    assert.equal(response.status, 400)
    assert.equal((await response.json()).error, 'oauth_callback_invalid')
    assert.equal(response.headers.get('referrer-policy'), 'no-referrer')
  }
  const oversized = await handler(
    new Request(
      `https://sidecar.example/oauth/atproto/callback?state=${state}&code=${'x'.repeat(17_000)}`,
    ),
  )
  assert.equal(oversized.status, 413)
  assert.equal((await oversized.json()).error, 'oauth_callback_too_large')
})

test('callback configuration and method fail closed', async () => {
  assert.throws(
    () =>
      createSidecarHandler({
        service: fakeService([]),
        authenticate: async () => ({ ownerId: 'user:one' }),
        appCallbackUri: 'https://app.example/wrong',
      }),
    (error) => error.code === 'sidecar_callback_unconfigured',
  )
  const handler = createSidecarHandler({
    service: fakeService([]),
    authenticate: async () => ({ ownerId: 'user:one' }),
    appCallbackUri: 'http://127.0.0.1:5173/oauth/callback',
  })
  const response = await handler(
    new Request('https://sidecar.example/oauth/atproto/callback', { method: 'POST' }),
  )
  assert.equal(response.status, 405)
  assert.equal(response.headers.get('referrer-policy'), 'no-referrer')
})

function jsonRequest(url, body, headers = {}) {
  return new Request(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...headers },
    body: JSON.stringify(body),
  })
}

function fakeService(calls) {
  return {
    clientMetadata() {
      return { client_id: 'https://sidecar.example/metadata', dpop_bound_access_tokens: true }
    },
    publicJwks() {
      return { keys: [{ kty: 'EC', x: 'x', y: 'y' }] }
    },
    async start({ ownerId, handle }) {
      calls.push({ route: 'start', ownerId, handle })
      return { authorization_url: 'https://pds.example/authorize' }
    },
    async callback(input) {
      calls.push({ route: 'callback', ...input })
      return { status: 'active' }
    },
    async restore(input) {
      calls.push({ route: 'restore', ...input })
      return { status: 'active' }
    },
    async execute(input) {
      calls.push({ route: 'execute', ...input })
      return { status: 'complete' }
    },
    async revoke(input) {
      calls.push({ route: 'revoke', ...input })
      return { status: 'revoked' }
    },
  }
}
