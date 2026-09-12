import { createServer } from 'node:http'
import { pathToFileURL } from 'node:url'

const JSON_TYPE = 'application/json; charset=utf-8'
const MAX_UPSTREAM_BODY_BYTES = 128 * 1024
const PUBLIC_PATHS = new Set([
  '/health',
  '/oauth/atproto/client-metadata.json',
  '/oauth/atproto/jwks.json',
  '/oauth/atproto/callback',
])
const FORWARDED_RESPONSE_HEADERS = new Set([
  'cache-control',
  'content-type',
  'location',
  'pragma',
  'referrer-policy',
  'x-content-type-options',
])

export function createPublicGatewayListener({
  upstreamOrigin = 'http://127.0.0.1:4310',
  fetchImpl = globalThis.fetch,
} = {}) {
  const upstream = requireLoopbackOrigin(upstreamOrigin)
  if (typeof fetchImpl !== 'function') throw new TypeError('gateway fetch implementation is required')

  return async function publicGateway(incoming, outgoing) {
    try {
      const source = new URL(incoming.url ?? '/', 'http://gateway.invalid')
      if (!PUBLIC_PATHS.has(source.pathname)) {
        return jsonError(outgoing, 404, 'public_route_not_found')
      }
      if (incoming.method !== 'GET') {
        return jsonError(outgoing, 405, 'method_not_allowed')
      }
      if (source.pathname !== '/oauth/atproto/callback' && source.search) {
        return jsonError(outgoing, 400, 'canonical_public_url_required')
      }

      const target = new URL(source.pathname + source.search, upstream)
      const response = await fetchImpl(target, {
        method: 'GET',
        redirect: 'manual',
        signal: AbortSignal.timeout(10_000),
      })
      const body = await readBoundedBody(response)

      outgoing.statusCode = response.status
      for (const [name, value] of response.headers) {
        if (FORWARDED_RESPONSE_HEADERS.has(name.toLowerCase())) outgoing.setHeader(name, value)
      }
      outgoing.setHeader('x-content-type-options', 'nosniff')
      outgoing.end(body)
    } catch (error) {
      jsonError(
        outgoing,
        502,
        error instanceof UpstreamBodyTooLargeError
          ? 'public_upstream_response_too_large'
          : 'public_upstream_unavailable',
      )
    }
  }
}

export async function startPublicGateway({
  host = '127.0.0.1',
  port = 4311,
  upstreamOrigin = 'http://127.0.0.1:4310',
  fetchImpl,
  installSignalHandlers = true,
} = {}) {
  const resolvedHost = requireLoopbackHost(host)
  const resolvedPort = requirePort(port)
  const server = createServer(
    createPublicGatewayListener({
      upstreamOrigin,
      ...(fetchImpl ? { fetchImpl } : {}),
    }),
  )
  server.on('clientError', (_error, socket) => {
    if (socket.writable) socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n')
  })
  await listen(server, resolvedPort, resolvedHost)

  let closed = false
  const signalHandlers = new Map()
  const close = async () => {
    if (closed) return
    closed = true
    for (const [signal, handler] of signalHandlers) process.off(signal, handler)
    await closeServer(server)
  }
  if (installSignalHandlers) {
    for (const signal of ['SIGINT', 'SIGTERM']) {
      const handler = () => close().catch(() => { process.exitCode = 1 })
      signalHandlers.set(signal, handler)
      process.once(signal, handler)
    }
  }
  return Object.freeze({ server, close, address: () => server.address() })
}

function requireLoopbackOrigin(value) {
  let url
  try {
    url = new URL(String(value))
  } catch {
    throw new TypeError('gateway upstream must be a loopback HTTP origin')
  }
  if (
    url.protocol !== 'http:' ||
    !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) ||
    url.username ||
    url.password ||
    url.pathname !== '/' ||
    url.search ||
    url.hash
  ) {
    throw new TypeError('gateway upstream must be a loopback HTTP origin')
  }
  return url
}

function requireLoopbackHost(value) {
  const host = String(value)
  if (!['127.0.0.1', 'localhost', '::1'].includes(host)) {
    throw new TypeError('public gateway must bind to a loopback host')
  }
  return host
}

function requirePort(value) {
  const port = Number(value)
  if (!Number.isSafeInteger(port) || port < 0 || port > 65_535) {
    throw new TypeError('public gateway port must be between 0 and 65535')
  }
  return port
}

function jsonError(response, status, error) {
  response.statusCode = status
  response.setHeader('cache-control', 'no-store, max-age=0')
  response.setHeader('content-type', JSON_TYPE)
  response.setHeader('x-content-type-options', 'nosniff')
  response.end(JSON.stringify({ error }))
}

async function readBoundedBody(response) {
  const declaredLength = Number(response.headers.get('content-length') ?? 0)
  if (declaredLength > MAX_UPSTREAM_BODY_BYTES) throw new UpstreamBodyTooLargeError()
  if (!response.body) return Buffer.alloc(0)

  const reader = response.body.getReader()
  const chunks = []
  let total = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      total += value.byteLength
      if (total > MAX_UPSTREAM_BODY_BYTES) throw new UpstreamBodyTooLargeError()
      chunks.push(Buffer.from(value))
    }
  } catch (error) {
    await reader.cancel().catch(() => {})
    throw error
  }
  return Buffer.concat(chunks, total)
}

class UpstreamBodyTooLargeError extends Error {}

function listen(server, port, host) {
  return new Promise((resolve, reject) => {
    const onError = (error) => {
      server.off('listening', onListening)
      reject(error)
    }
    const onListening = () => {
      server.off('error', onError)
      resolve()
    }
    server.once('error', onError)
    server.once('listening', onListening)
    server.listen(port, host)
  })
}

function closeServer(server) {
  if (!server?.listening) return Promise.resolve()
  return new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()))
  })
}

async function main() {
  try {
    const running = await startPublicGateway({
      host: process.env.FEED_PASSPORT_ATPROTO_PUBLIC_GATEWAY_HOST ?? '127.0.0.1',
      port: process.env.FEED_PASSPORT_ATPROTO_PUBLIC_GATEWAY_PORT ?? '4311',
      upstreamOrigin:
        process.env.FEED_PASSPORT_ATPROTO_PUBLIC_GATEWAY_UPSTREAM ??
        'http://127.0.0.1:4310',
    })
    const address = running.address()
    const location =
      address && typeof address === 'object'
        ? `http://${address.family === 'IPv6' ? `[${address.address}]` : address.address}:${address.port}`
        : 'configured local socket'
    process.stdout.write(`AT Protocol public gateway ready at ${location}\n`)
  } catch {
    process.stderr.write('AT Protocol public gateway failed to start.\n')
    process.exitCode = 1
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main()
}
