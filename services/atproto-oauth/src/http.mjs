import { timingSafeEqual } from 'node:crypto'

import { asPublicError, SidecarError } from './errors.mjs'
import { requireOwnerId } from './validation.mjs'

const PRIVATE_CACHE = 'no-store, max-age=0'
const PUBLIC_CACHE = 'public, max-age=300'
const JSON_TYPE = 'application/json; charset=utf-8'
const CALLBACK_PATH = '/oauth/atproto/callback'
const CALLBACK_KEYS = new Set(['code', 'state', 'iss', 'error', 'error_description'])

export function createSidecarHandler({ service, authenticate, appCallbackUri }) {
  if (!service || typeof service.start !== 'function') {
    throw new SidecarError('sidecar_unconfigured', 'The AT Protocol service is required.', {
      status: 503,
    })
  }
  if (typeof authenticate !== 'function') {
    throw new SidecarError(
      'sidecar_auth_unconfigured',
      'An authenticated owner resolver is required for private sidecar endpoints.',
      { status: 503 },
    )
  }
  const callbackTarget = requireAppCallbackTarget(appCallbackUri)

  return async function handle(request) {
    let publicCallback = false
    try {
      const url = new URL(request.url)
      if (url.pathname === '/health' && request.method === 'GET') {
        return jsonResponse(200, { status: 'ready', service: 'atproto-oauth-sidecar' }, PUBLIC_CACHE)
      }
      if (
        url.pathname === '/oauth/atproto/client-metadata.json' &&
        request.method === 'GET'
      ) {
        return jsonResponse(200, service.clientMetadata(), PUBLIC_CACHE)
      }
      if (url.pathname === '/oauth/atproto/jwks.json' && request.method === 'GET') {
        return jsonResponse(200, service.publicJwks(), PUBLIC_CACHE)
      }
      if (url.pathname === CALLBACK_PATH) {
        publicCallback = true
        if (request.method !== 'GET') {
          throw new SidecarError('method_not_allowed', 'The OAuth callback requires GET.', {
            status: 405,
          })
        }
        return callbackRelay(url, callbackTarget)
      }

      const principal = await authenticate(request)
      const ownerId = requireOwnerId(principal?.ownerId)
      if (request.method !== 'POST') {
        throw new SidecarError('method_not_allowed', 'This endpoint requires POST.', {
          status: 405,
        })
      }
      const body = await readJsonObject(request)
      rejectCredentialMaterial(body)

      let output
      switch (url.pathname) {
        case '/v1/oauth/atproto/start':
          output = await service.start({
            ownerId,
            handle: body.handle,
            signal: request.signal,
          })
          break
        case '/v1/oauth/atproto/callback':
          output = await service.callback({ ownerId, query: body.query })
          break
        case '/v1/oauth/atproto/sessions/restore':
          output = await service.restore({
            ownerId,
            connectionRef: body.connection_ref,
          })
          break
        case '/v1/oauth/atproto/sessions/execute':
          output = await service.execute({
            ownerId,
            leaseRef: body.lease_ref,
            operation: body.operation,
            input: body.input,
          })
          break
        case '/v1/oauth/atproto/sessions/revoke':
          output = await service.revoke({
            ownerId,
            connectionRef: body.connection_ref,
          })
          break
        default:
          throw new SidecarError('route_not_found', 'The sidecar route was not found.', {
            status: 404,
          })
      }
      return jsonResponse(200, output, PRIVATE_CACHE)
    } catch (error) {
      const publicError = asPublicError(error)
      const response = jsonResponse(publicError.status, publicError.body, PRIVATE_CACHE)
      if (publicCallback) applyCallbackPrivacyHeaders(response.headers)
      return response
    }
  }
}

function requireAppCallbackTarget(value) {
  try {
    const url = new URL(value)
    const loopback =
      url.hostname === 'localhost' ||
      url.hostname === '127.0.0.1' ||
      url.hostname === '[::1]'
    if (
      !((url.protocol === 'https:') || (url.protocol === 'http:' && loopback)) ||
      url.username ||
      url.password ||
      url.pathname !== '/oauth/callback' ||
      url.search ||
      url.hash
    ) {
      throw new Error()
    }
    return url
  } catch (error) {
    throw new SidecarError(
      'sidecar_callback_unconfigured',
      'A strict application OAuth callback URI is required.',
      { status: 503, cause: error },
    )
  }
}

function callbackRelay(source, configuredTarget) {
  if (Buffer.byteLength(source.search, 'utf8') > 16_384) {
    throw new SidecarError('oauth_callback_too_large', 'The OAuth callback is too large.', {
      status: 413,
    })
  }
  const params = source.searchParams
  for (const key of params.keys()) {
    const values = params.getAll(key)
    if (
      !CALLBACK_KEYS.has(key) ||
      values.length !== 1 ||
      containsControl(key) ||
      values.some(containsControl)
    ) {
      throw new SidecarError('oauth_callback_invalid', 'The OAuth callback is invalid.')
    }
  }
  const state = singleCallbackValue(params, 'state', { maxLength: 512 })
  const code = singleCallbackValue(params, 'code', { optional: true, maxLength: 4096 })
  const oauthError = singleCallbackValue(params, 'error', {
    optional: true,
    maxLength: 128,
  })
  if ((!code && !oauthError) || (code && oauthError)) {
    throw new SidecarError(
      'oauth_callback_invalid',
      'The OAuth callback must contain one authorization result.',
    )
  }
  const issuer = singleCallbackValue(params, 'iss', { optional: true, maxLength: 2048 })
  const description = singleCallbackValue(params, 'error_description', {
    optional: true,
    maxLength: 2048,
  })
  if (description && !oauthError) {
    throw new SidecarError('oauth_callback_invalid', 'The OAuth callback is invalid.')
  }

  const target = new URL(configuredTarget)
  target.searchParams.set('state', state)
  if (code) target.searchParams.set('code', code)
  if (issuer) target.searchParams.set('iss', issuer)
  if (oauthError) target.searchParams.set('error', oauthError)
  if (description) target.searchParams.set('error_description', description)
  const headers = new Headers({ location: target.toString() })
  applyCallbackPrivacyHeaders(headers)
  return new Response(null, { status: 302, headers })
}

function singleCallbackValue(params, name, { optional = false, maxLength }) {
  const values = params.getAll(name)
  if (values.length === 0 && optional) return undefined
  if (
    values.length !== 1 ||
    values[0].length === 0 ||
    values[0].length > maxLength ||
    containsControl(values[0])
  ) {
    throw new SidecarError('oauth_callback_invalid', 'The OAuth callback is invalid.')
  }
  return values[0]
}

function containsControl(value) {
  return /[\u0000-\u001f\u007f]/.test(value)
}

function applyCallbackPrivacyHeaders(headers) {
  headers.set('cache-control', PRIVATE_CACHE)
  headers.set('pragma', 'no-cache')
  headers.set('referrer-policy', 'no-referrer')
  headers.set('content-security-policy', "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
  headers.set('x-content-type-options', 'nosniff')
}

export function createSharedSecretAuthenticator(secret) {
  if (
    typeof secret !== 'string' ||
    secret.length < 32 ||
    secret.length > 4096 ||
    /[\r\n]/.test(secret)
  ) {
    throw new SidecarError(
      'sidecar_auth_unconfigured',
      'A high-entropy internal sidecar bearer secret is required.',
      { status: 503 },
    )
  }
  const expected = Buffer.from(secret)
  return async (request) => {
    const header = request.headers.get('authorization') ?? ''
    const supplied = header.startsWith('Bearer ') ? Buffer.from(header.slice(7)) : Buffer.alloc(0)
    if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
      throw new SidecarError(
        'sidecar_authentication_required',
        'Sidecar authentication is required.',
        { status: 401 },
      )
    }
    return { ownerId: requireOwnerId(request.headers.get('x-feed-passport-owner')) }
  }
}

export function createNodeRequestListener(fetchHandler, { origin = 'http://127.0.0.1' } = {}) {
  if (typeof fetchHandler !== 'function') throw new TypeError('fetchHandler is required')
  const base = new URL(origin)
  if (!['http:', 'https:'].includes(base.protocol)) throw new TypeError('origin must be HTTP(S)')

  return async function nodeRequestListener(incoming, outgoing) {
    try {
      const body = ['GET', 'HEAD'].includes(incoming.method)
        ? undefined
        : await readNodeBody(incoming, 70_000)
      const request = new Request(new URL(incoming.url, base), {
        method: incoming.method,
        headers: incoming.headers,
        ...(body ? { body } : {}),
      })
      const response = await fetchHandler(request)
      outgoing.statusCode = response.status
      for (const [name, value] of response.headers) outgoing.setHeader(name, value)
      outgoing.end(Buffer.from(await response.arrayBuffer()))
    } catch {
      outgoing.statusCode = 500
      outgoing.setHeader('content-type', JSON_TYPE)
      outgoing.setHeader('cache-control', PRIVATE_CACHE)
      outgoing.end(
        JSON.stringify({
          error: 'atproto_sidecar_error',
          detail: 'The AT Protocol credential sidecar could not complete the request.',
        }),
      )
    }
  }
}

async function readJsonObject(request) {
  const contentType = request.headers.get('content-type')?.split(';', 1)[0].trim().toLowerCase()
  if (contentType !== 'application/json') {
    throw new SidecarError('content_type_invalid', 'This endpoint requires application/json.', {
      status: 415,
    })
  }
  const declaredLength = Number(request.headers.get('content-length') ?? 0)
  if (declaredLength > 65_536) {
    throw new SidecarError('request_too_large', 'The sidecar request is too large.', {
      status: 413,
    })
  }
  const raw = await request.text()
  if (Buffer.byteLength(raw) > 65_536) {
    throw new SidecarError('request_too_large', 'The sidecar request is too large.', {
      status: 413,
    })
  }
  let value
  try {
    value = JSON.parse(raw)
  } catch (error) {
    throw new SidecarError('json_invalid', 'The sidecar request body is invalid JSON.', {
      cause: error,
    })
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new SidecarError('json_invalid', 'The sidecar request body must be an object.')
  }
  return value
}

function rejectCredentialMaterial(value, seen = new WeakSet()) {
  if (!value || typeof value !== 'object') return
  if (seen.has(value)) {
    throw new SidecarError('json_invalid', 'The sidecar request body is invalid.')
  }
  seen.add(value)
  const forbidden = /^(?:access_?token|refresh_?token|id_?token|session_?token|authorization|cookie|dpop|dpop_?key|private_?key|private_?jwk|client_?secret|secret)$/i
  for (const [key, child] of Object.entries(value)) {
    if (forbidden.test(key)) {
      throw new SidecarError(
        'credential_material_denied',
        'Credential material must never be sent through the sidecar JSON API.',
        { status: 400 },
      )
    }
    rejectCredentialMaterial(child, seen)
  }
  seen.delete(value)
}

function jsonResponse(status, body, cacheControl) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'cache-control': cacheControl,
      'content-type': JSON_TYPE,
      'x-content-type-options': 'nosniff',
    },
  })
}

async function readNodeBody(stream, limit) {
  const chunks = []
  let total = 0
  for await (const chunk of stream) {
    total += chunk.length
    if (total > limit) throw new Error('request too large')
    chunks.push(chunk)
  }
  return Buffer.concat(chunks)
}
