import { lstat, readFile } from 'node:fs/promises'
import { isAbsolute, resolve } from 'node:path'
import { inspect } from 'node:util'

import { SidecarError } from './errors.mjs'

export class SidecarServerConfig {
  constructor(value) {
    Object.assign(this, value)
    Object.freeze(this.client)
    Object.freeze(this.privateKeys)
    Object.freeze(this)
  }

  toJSON() {
    return {
      host: this.host,
      port: this.port,
      public_origin: this.publicOrigin,
      store: '<configured>',
      store_key: '<redacted>',
      internal_auth: '<redacted>',
      private_keys: '<redacted>',
    }
  }

  [inspect.custom]() {
    return `SidecarServerConfig ${JSON.stringify(this.toJSON())}`
  }
}

export async function loadSidecarConfig(env = process.env) {
  const publicOrigin = requireOrigin(
    env.FEED_PASSPORT_ATPROTO_PUBLIC_ORIGIN,
    'FEED_PASSPORT_ATPROTO_PUBLIC_ORIGIN',
  )
  const host = requireHost(env.FEED_PASSPORT_ATPROTO_HOST ?? '127.0.0.1')
  const port = requirePort(env.FEED_PASSPORT_ATPROTO_PORT ?? '4310')
  const storePath = requireAbsolutePath(
    env.FEED_PASSPORT_ATPROTO_STORE_PATH,
    'FEED_PASSPORT_ATPROTO_STORE_PATH',
  )
  const storeKey = decodeExternalKey(env.FEED_PASSPORT_ATPROTO_STORE_KEY_B64)
  const storeKeyId = requireIdentifier(
    env.FEED_PASSPORT_ATPROTO_STORE_KEY_ID ?? 'local-v1',
    'FEED_PASSPORT_ATPROTO_STORE_KEY_ID',
  )
  const internalServiceSecret = requireSecret(
    env.FEED_PASSPORT_ATPROTO_SIDECAR_INTERNAL_SECRET,
  )
  const privateKeyPath = requireAbsolutePath(
    env.FEED_PASSPORT_ATPROTO_PRIVATE_KEY_FILE,
    'FEED_PASSPORT_ATPROTO_PRIVATE_KEY_FILE',
  )
  const privateKeyId = requireIdentifier(
    env.FEED_PASSPORT_ATPROTO_PRIVATE_KEY_ID,
    'FEED_PASSPORT_ATPROTO_PRIVATE_KEY_ID',
  )
  const importable = await readPrivateKey(privateKeyPath)
  const clientId = new URL('/oauth/atproto/client-metadata.json', publicOrigin).toString()
  const jwksUri = new URL('/oauth/atproto/jwks.json', publicOrigin).toString()
  const redirectUri = new URL('/oauth/atproto/callback', publicOrigin).toString()
  const appCallbackUri = requireAppCallbackUri(
    env.FEED_PASSPORT_ATPROTO_APP_CALLBACK_URI,
  )
  const clientUri = requireHttpsUrl(
    env.FEED_PASSPORT_ATPROTO_CLIENT_URI ?? publicOrigin,
    'FEED_PASSPORT_ATPROTO_CLIENT_URI',
  )
  const signingAlgorithm = env.FEED_PASSPORT_ATPROTO_SIGNING_ALGORITHM ?? 'ES256'
  if (!['ES256', 'RS256'].includes(signingAlgorithm)) {
    throw configurationError('FEED_PASSPORT_ATPROTO_SIGNING_ALGORITHM is invalid')
  }
  const requestedScope = (env.FEED_PASSPORT_ATPROTO_SCOPE ?? 'atproto transition:generic').trim()
  const scopeTokens = requestedScope.split(/\s+/).filter(Boolean)
  if (
    scopeTokens.length !== 2 ||
    new Set(scopeTokens).size !== 2 ||
    !scopeTokens.includes('atproto') ||
    !scopeTokens.includes('transition:generic')
  ) {
    throw configurationError(
      'FEED_PASSPORT_ATPROTO_SCOPE must be exactly atproto transition:generic',
    )
  }
  const scope = 'atproto transition:generic'
  const clientName = (env.FEED_PASSPORT_ATPROTO_CLIENT_NAME ?? 'Feed Passport').trim()
  if (clientName.length < 2 || clientName.length > 80 || /[\r\n]/.test(clientName)) {
    throw configurationError('FEED_PASSPORT_ATPROTO_CLIENT_NAME is invalid')
  }
  const client = {
    clientId,
    metadataUri: clientId,
    clientUri,
    jwksUri,
    redirectUri,
    name: clientName,
    signingAlgorithm,
    scope,
    ...(env.FEED_PASSPORT_ATPROTO_POLICY_URI
      ? {
          policyUri: requireHttpsUrl(
            env.FEED_PASSPORT_ATPROTO_POLICY_URI,
            'FEED_PASSPORT_ATPROTO_POLICY_URI',
          ),
        }
      : {}),
    ...(env.FEED_PASSPORT_ATPROTO_TERMS_URI
      ? {
          termsUri: requireHttpsUrl(
            env.FEED_PASSPORT_ATPROTO_TERMS_URI,
            'FEED_PASSPORT_ATPROTO_TERMS_URI',
          ),
        }
      : {}),
  }
  return new SidecarServerConfig({
    host,
    port,
    internalOrigin: internalOrigin(host, port),
    publicOrigin,
    storePath,
    storeKey,
    storeKeyId,
    internalServiceSecret,
    appCallbackUri,
    privateKeys: [{ kid: privateKeyId, importable }],
    client,
  })
}

export function decodeExternalKey(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]+={0,2}$/.test(value.trim())) {
    throw configurationError(
      'FEED_PASSPORT_ATPROTO_STORE_KEY_B64 must be URL-safe base64 for exactly 32 bytes',
    )
  }
  const decoded = Buffer.from(value.trim().replace(/=+$/, ''), 'base64url')
  if (decoded.length !== 32) {
    throw configurationError(
      'FEED_PASSPORT_ATPROTO_STORE_KEY_B64 must decode to exactly 32 bytes',
    )
  }
  return decoded
}

function requireOrigin(value, name) {
  const canonical = requireHttpsUrl(value, name)
  const url = new URL(canonical)
  if (url.pathname !== '/' || url.search || url.hash) {
    throw configurationError(`${name} must be an HTTPS origin without a path or query`)
  }
  return canonical
}

function requireHttpsUrl(value, name) {
  try {
    const url = new URL(value)
    if (
      url.protocol !== 'https:' ||
      url.username ||
      url.password ||
      url.hash ||
      /[\r\n]/.test(String(value))
    ) {
      throw new Error()
    }
    return url.toString()
  } catch {
    throw configurationError(`${name} must be an absolute HTTPS URL`)
  }
}

function requireAppCallbackUri(value) {
  const name = 'FEED_PASSPORT_ATPROTO_APP_CALLBACK_URI'
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
      url.hash ||
      /[\r\n]/.test(String(value))
    ) {
      throw new Error()
    }
    return url.toString()
  } catch {
    throw configurationError(
      `${name} must be HTTPS (or loopback HTTP) and end /oauth/callback`,
    )
  }
}

function requireHost(value) {
  if (
    typeof value !== 'string' ||
    value.length === 0 ||
    value.length > 253 ||
    /[\s/\\?#@]/.test(value)
  ) {
    throw configurationError('FEED_PASSPORT_ATPROTO_HOST is invalid')
  }
  return value
}

function requirePort(value) {
  const port = Number(value)
  if (!Number.isSafeInteger(port) || port < 1 || port > 65535) {
    throw configurationError('FEED_PASSPORT_ATPROTO_PORT must be between 1 and 65535')
  }
  return port
}

function requireAbsolutePath(value, name) {
  if (typeof value !== 'string' || !isAbsolute(value) || value.length > 4096 || /[\r\n]/.test(value)) {
    throw configurationError(`${name} must be an absolute file path`)
  }
  return resolve(value)
}

function requireIdentifier(value, name) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9._-]{1,128}$/.test(value)) {
    throw configurationError(`${name} is invalid`)
  }
  return value
}

function requireSecret(value) {
  if (
    typeof value !== 'string' ||
    value.length < 32 ||
    value.length > 4096 ||
    /[\r\n]/.test(value)
  ) {
    throw configurationError(
      'FEED_PASSPORT_ATPROTO_SIDECAR_INTERNAL_SECRET must contain 32-4096 characters',
    )
  }
  return value
}

async function readPrivateKey(filePath) {
  try {
    const metadata = await lstat(filePath)
    if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.size < 32 || metadata.size > 65_536) {
      throw new Error()
    }
    const value = await readFile(filePath, 'utf8')
    if (value.length < 32 || value.includes('\0')) throw new Error()
    return value
  } catch (error) {
    throw new SidecarError(
      'sidecar_private_key_unavailable',
      'The externally supplied AT Protocol OAuth client key file is unavailable or invalid.',
      { status: 503, cause: error },
    )
  }
}

function internalOrigin(host, port) {
  const formattedHost = host.includes(':') && !host.startsWith('[') ? `[${host}]` : host
  return `http://${formattedHost}:${port}`
}

function configurationError(detail) {
  return new SidecarError('sidecar_configuration_invalid', detail, { status: 503 })
}
