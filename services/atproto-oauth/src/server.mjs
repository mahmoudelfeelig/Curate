import { createServer } from 'node:http'
import { pathToFileURL } from 'node:url'

import { loadSidecarConfig } from './config.mjs'
import { createDurableSidecarStores } from './durable-store.mjs'
import { SidecarError } from './errors.mjs'
import { createSharedSecretAuthenticator } from './http.mjs'
import { createAtprotoSidecarRuntime } from './runtime.mjs'

export async function startSidecarServer({
  config,
  env = process.env,
  runtimeFactory = createAtprotoSidecarRuntime,
  storesFactory = createDurableSidecarStores,
  installSignalHandlers = true,
} = {}) {
  const resolved = config ?? (await loadSidecarConfig(env))
  let stores
  let server
  let closed = false
  const signalHandlers = new Map()
  try {
    stores = await storesFactory({
      filePath: resolved.storePath,
      encryptionKey: resolved.storeKey,
      keyId: resolved.storeKeyId,
    })
    resolved.storeKey.fill(0)
    const runtime = await runtimeFactory({
      client: resolved.client,
      privateKeys: resolved.privateKeys,
      oauthStateStore: stores.oauthStateStore,
      oauthSessionStore: stores.oauthSessionStore,
      requestLock: createSingleProcessRequestLock(),
      ownerStates: stores.ownerStates,
      connections: stores.connections,
      sessionLeases: stores.sessionLeases,
      authenticate: createSharedSecretAuthenticator(resolved.internalServiceSecret),
      appCallbackUri: resolved.appCallbackUri,
      origin: resolved.internalOrigin,
    })
    if (!runtime || typeof runtime.nodeRequestListener !== 'function') {
      throw new SidecarError(
        'sidecar_runtime_unconfigured',
        'The AT Protocol sidecar runtime listener is unavailable.',
        { status: 503 },
      )
    }
    server = createServer(runtime.nodeRequestListener)
    server.on('clientError', (_error, socket) => {
      if (socket.writable) socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n')
    })
    await listen(server, resolved.port, resolved.host)

    const close = async () => {
      if (closed) return
      closed = true
      for (const [signal, handler] of signalHandlers) process.off(signal, handler)
      await closeServer(server)
      await stores.close()
    }
    if (installSignalHandlers) {
      for (const signal of ['SIGINT', 'SIGTERM']) {
        const handler = () => {
          close().catch(() => {
            process.exitCode = 1
          })
        }
        signalHandlers.set(signal, handler)
        process.once(signal, handler)
      }
    }
    return Object.freeze({
      server,
      close,
      address: () => server.address(),
      publicOrigin: resolved.publicOrigin,
    })
  } catch (error) {
    resolved.storeKey?.fill(0)
    await closeServer(server).catch(() => {})
    await stores?.close().catch(() => {})
    throw error
  }
}

export function createSingleProcessRequestLock() {
  const locks = new Map()
  return async function requestLock(key, callback) {
    if (typeof callback !== 'function') throw new TypeError('request lock callback is required')
    const canonicalKey = String(key)
    const previous = locks.get(canonicalKey) ?? Promise.resolve()
    let release
    const current = new Promise((resolve) => {
      release = resolve
    })
    locks.set(canonicalKey, current)
    await previous
    try {
      return await callback()
    } finally {
      release()
      if (locks.get(canonicalKey) === current) locks.delete(canonicalKey)
    }
  }
}

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
    const running = await startSidecarServer()
    const address = running.address()
    const location =
      address && typeof address === 'object'
        ? `http://${address.family === 'IPv6' ? `[${address.address}]` : address.address}:${address.port}`
        : 'configured local socket'
    process.stdout.write(`AT Protocol sidecar ready at ${location}\n`)
  } catch (error) {
    const code =
      error instanceof SidecarError && /^[a-z0-9_]+$/.test(error.code)
        ? error.code
        : 'sidecar_start_failed'
    process.stderr.write(`AT Protocol sidecar failed to start (${code}).\n`)
    process.exitCode = 1
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main()
}
