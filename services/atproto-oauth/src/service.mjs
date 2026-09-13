import { randomBytes } from 'node:crypto'

import { SidecarError } from './errors.mjs'
import {
  assertPublicJwks,
  equalOpaque,
  requireDid,
  requireHandle,
  requireOAuthProtocolState,
  requireOpaqueReference,
  requireOwnerId,
  requireSingleParam,
  sanitizeOperationResult,
  sanitizePreferences,
} from './validation.mjs'

const CALLBACK_KEYS = new Set([
  'code',
  'state',
  'iss',
  'error',
  'error_description',
])

const OPERATIONS = new Set([
  'graph.get_follows',
  'graph.get_mutes',
  'graph.follow',
  'graph.delete_follow',
  'graph.mute',
  'graph.unmute',
  'actor.get_preferences',
  'actor.put_preferences',
])

export class AtprotoOAuthService {
  constructor({
    oauthClient,
    ownerStates,
    connections,
    sessionLeases,
    operationExecutor,
    now = () => Date.now(),
    randomState = () => randomBytes(32).toString('base64url'),
    stateTtlMs = 10 * 60 * 1000,
    leaseTtlMs = 60 * 1000,
    callbackReceiptTtlMs = 15 * 60 * 1000,
  }) {
    for (const [name, dependency] of Object.entries({
      oauthClient,
      ownerStates,
      connections,
      sessionLeases,
      operationExecutor,
    })) {
      if (!dependency || typeof dependency !== 'object') {
        throw new SidecarError(
          'sidecar_unconfigured',
          `The AT Protocol sidecar requires an injected ${name}.`,
          { status: 503 },
        )
      }
    }
    if (typeof oauthClient.authorize !== 'function' || typeof oauthClient.callback !== 'function') {
      throw new SidecarError(
        'sidecar_unconfigured',
        'The official AT Protocol OAuth client is unavailable.',
        { status: 503 },
      )
    }
    if (!Number.isSafeInteger(stateTtlMs) || stateTtlMs < 60_000 || stateTtlMs > 3_600_000) {
      throw new TypeError('stateTtlMs must be between one minute and one hour')
    }
    if (!Number.isSafeInteger(leaseTtlMs) || leaseTtlMs < 5_000 || leaseTtlMs > 300_000) {
      throw new TypeError('leaseTtlMs must be between five seconds and five minutes')
    }
    if (
      !Number.isSafeInteger(callbackReceiptTtlMs) ||
      callbackReceiptTtlMs < 60_000 ||
      callbackReceiptTtlMs > 3_600_000
    ) {
      throw new TypeError('callbackReceiptTtlMs must be between one minute and one hour')
    }
    this.oauthClient = oauthClient
    this.ownerStates = ownerStates
    this.connections = connections
    this.sessionLeases = sessionLeases
    this.operationExecutor = operationExecutor
    this.now = now
    this.randomState = randomState
    this.stateTtlMs = stateTtlMs
    this.leaseTtlMs = leaseTtlMs
    this.callbackReceiptTtlMs = callbackReceiptTtlMs
    this.lifecycle = new SerialBoundary()
  }

  clientMetadata() {
    const metadata = this.oauthClient.clientMetadata
    if (!metadata || typeof metadata !== 'object') {
      throw new SidecarError(
        'client_metadata_unavailable',
        'AT Protocol client metadata is unavailable.',
        { status: 503 },
      )
    }
    return structuredClone(metadata)
  }

  publicJwks() {
    return assertPublicJwks(this.oauthClient.jwks)
  }

  async start({ ownerId, handle, signal }) {
    const owner = requireOwnerId(ownerId)
    const identifier = requireHandle(handle)
    const appState = this.randomState()
    if (typeof appState !== 'string' || appState.length < 43 || appState.length > 256) {
      throw new SidecarError(
        'oauth_state_generation_failed',
        'The OAuth transaction could not be created.',
        { status: 503 },
      )
    }
    const startedAt = this.now()
    const expiresAt = startedAt + this.stateTtlMs
    await this.ownerStates.create(appState, { ownerId: owner, expiresAt })
    try {
      const authorization = await this.oauthClient.authorize(identifier, {
        state: appState,
        signal,
      })
      const authorizationUrl = new URL(String(authorization))
      if (authorizationUrl.protocol !== 'https:') {
        throw new SidecarError(
          'oauth_authorization_url_invalid',
          'The AT Protocol authorization server did not return an HTTPS URL.',
          { status: 502 },
        )
      }
      // The official client generates the OAuth protocol state itself and
      // stores our value as appState. With PAR, the protocol state is not in
      // this URL at all, so the injected state-store seam must have durably
      // bound it to the owner before authorize returns.
      await this.ownerStates.bound(appState, { ownerId: owner, now: this.now() })
      return {
        platform: 'bluesky',
        authorization_url: authorizationUrl.toString(),
        expires_at: new Date(expiresAt).toISOString(),
      }
    } catch (error) {
      await this.ownerStates.deleteByAppState(appState)
      if (error instanceof SidecarError) throw error
      throw new SidecarError(
        'oauth_start_failed',
        'AT Protocol authorization could not be started.',
        { status: 502, cause: error },
      )
    }
  }

  async callback({ ownerId, query }) {
    const owner = requireOwnerId(ownerId)
    const params = callbackParams(query)
    const protocolState = requireOAuthProtocolState(requireSingleParam(params, 'state'))
    return this.lifecycle.run(async () => {
      const now = this.now()
      const receipt = await this.connections.callbackResult(protocolState, {
        ownerId: owner,
        now,
      })
      if (receipt) return publicConnection(receipt)
      const transaction = await this.ownerStates.claim(protocolState, {
        ownerId: owner,
        now,
      })

      let callbackResult
      try {
        callbackResult = await this.oauthClient.callback(params)
      } catch (error) {
        await this.ownerStates.delete(protocolState)
        throw new SidecarError(
          'oauth_callback_rejected',
          'The AT Protocol OAuth callback was rejected; start authorization again.',
          { status: 409, cause: error },
        )
      }
      if (!callbackResult || !equalOpaque(callbackResult.state, transaction.appState)) {
        await this.ownerStates.delete(protocolState)
        await bestEffortRevoke(this.oauthClient, callbackResult?.session)
        throw new SidecarError(
          'oauth_callback_state_mismatch',
          'The AT Protocol OAuth callback did not match its transaction.',
          { status: 409 },
        )
      }
      const session = callbackResult.session
      try {
        const did = requireDid(session?.did ?? session?.sub)
        const connection = await this.connections.bindCallback({
          ownerId: owner,
          did,
          state: protocolState,
          now: this.now(),
          receiptTtlMs: this.callbackReceiptTtlMs,
        })
        return publicConnection(connection)
      } catch (error) {
        await this.ownerStates.delete(protocolState)
        await bestEffortRevoke(this.oauthClient, session)
        throw error
      }
    })
  }

  async restore({ ownerId, connectionRef }) {
    const owner = requireOwnerId(ownerId)
    const reference = requireOpaqueReference(connectionRef, 'connection_ref')
    return this.lifecycle.run(async () => {
      const connection = await this.connections.getActive(reference, { ownerId: owner })
      let session
      try {
        session = await this.oauthClient.restore(connection.did)
      } catch (error) {
        throw new SidecarError(
          'session_restore_failed',
          'The AT Protocol session could not be restored; reconnect the account.',
          { status: 409, cause: error },
        )
      }
      await this.connections.getActive(reference, { ownerId: owner })
      const lease = await this.sessionLeases.create({
        ownerId: owner,
        connectionRef: reference,
        did: connection.did,
        session,
        now: this.now(),
        ttlMs: this.leaseTtlMs,
      })
      return {
        platform: 'bluesky',
        connection_ref: lease.connectionRef,
        lease_ref: lease.leaseRef,
        external_subject: lease.did,
        expires_at: new Date(lease.expiresAt).toISOString(),
        authorization_scheme: 'DPoP',
      }
    })
  }

  async execute({ ownerId, leaseRef, operation, input = {} }) {
    const owner = requireOwnerId(ownerId)
    const leaseReference = requireOpaqueReference(leaseRef, 'lease_ref')
    if (!OPERATIONS.has(operation)) {
      throw new SidecarError(
        'bridge_operation_denied',
        'The requested AT Protocol operation is not allowed by the credential bridge.',
        { status: 403 },
      )
    }
    return this.lifecycle.run(async () => {
      const lease = await this.sessionLeases.get(leaseReference, {
        ownerId: owner,
        now: this.now(),
      })
      await this.connections.getActive(lease.connectionRef, { ownerId: owner })
      const safeInput = validateOperationInput(operation, input, lease.did)
      try {
        const result = await this.operationExecutor.execute({
          session: lease.session,
          did: lease.did,
          operation,
          input: safeInput,
        })
        return {
          platform: 'bluesky',
          operation,
          result: sanitizeOperationResult(operation, result, {
            ownerDid: lease.did,
            forbiddenScalars: credentialScalars(lease.session),
          }),
        }
      } catch (error) {
        if (error instanceof SidecarError) throw error
        throw new SidecarError(
          'bridge_operation_failed',
          'The AT Protocol operation failed inside the credential boundary.',
          { status: 502, cause: error },
        )
      }
    })
  }

  async revoke({ ownerId, connectionRef }) {
    const owner = requireOwnerId(ownerId)
    const reference = requireOpaqueReference(connectionRef, 'connection_ref')
    return this.lifecycle.run(async () => {
      const connection = await this.connections.beginRevoke(reference, {
        ownerId: owner,
        now: this.now(),
      })
      if (connection.status === 'revoked') {
        return revokedConnection(reference, 'sidecar_terminal')
      }
      await this.sessionLeases.deleteForConnection(reference)
      if (Number.isFinite(connection.providerConfirmedAt)) {
        if (typeof this.oauthClient.discardSession !== 'function') {
          throw new SidecarError(
            'sidecar_unconfigured',
            'The official AT Protocol session cleanup boundary is unavailable.',
            { status: 503 },
          )
        }
        try {
          await this.oauthClient.discardSession(connection.did)
        } catch (error) {
          throw new SidecarError(
            'session_cleanup_failed',
            'Provider revocation is confirmed but local session cleanup must be retried.',
            { status: 503, cause: error },
          )
        }
      } else {
        if (typeof this.oauthClient.revokeWithProof !== 'function') {
          throw new SidecarError(
            'sidecar_unconfigured',
            'The official AT Protocol confirmed-revocation boundary is unavailable.',
            { status: 503 },
          )
        }
        try {
          await this.oauthClient.revokeWithProof(connection.did, async () => {
            await this.connections.confirmRevoke(reference, {
              ownerId: owner,
              now: this.now(),
            })
          })
        } catch (error) {
          throw new SidecarError(
            'session_revoke_failed',
            'The AT Protocol authorization could not be confirmed revoked.',
            { status: 502, cause: error },
          )
        }
      }
      await this.connections.completeRevoke(reference, {
        ownerId: owner,
        now: this.now(),
      })
      return revokedConnection(reference, 'provider_confirmed')
    })
  }
}

function callbackParams(query) {
  if (typeof query !== 'string' || query.length === 0 || query.length > 16_384) {
    throw new SidecarError(
      'oauth_callback_invalid',
      'The OAuth callback parameters are invalid.',
    )
  }
  const normalized = query.startsWith('?') ? query.slice(1) : query
  const params = new URLSearchParams(normalized)
  for (const key of params.keys()) {
    if (!CALLBACK_KEYS.has(key) || params.getAll(key).length !== 1) {
      throw new SidecarError(
        'oauth_callback_invalid',
        'The OAuth callback parameters are invalid.',
      )
    }
  }
  requireSingleParam(params, 'state')
  const code = requireSingleParam(params, 'code', { optional: true })
  const oauthError = requireSingleParam(params, 'error', { optional: true })
  if ((!code && !oauthError) || (code && oauthError)) {
    throw new SidecarError(
      'oauth_callback_invalid',
      'The OAuth callback must contain one authorization result.',
    )
  }
  return params
}

function validateOperationInput(operation, input, ownerDid) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    throw new SidecarError('bridge_input_invalid', 'The operation input must be an object.')
  }
  const serialized = JSON.stringify(input)
  if (Buffer.byteLength(serialized) > 64_000) {
    throw new SidecarError('bridge_input_too_large', 'The operation input is too large.', {
      status: 413,
    })
  }
  switch (operation) {
    case 'graph.get_follows':
    case 'graph.get_mutes':
      return paginationInput(input, ownerDid)
    case 'graph.follow':
    case 'graph.mute':
    case 'graph.unmute':
      return { actor: requireDid(input.actor) }
    case 'graph.delete_follow': {
      if (
        typeof input.uri !== 'string' ||
        !input.uri.startsWith(`at://${ownerDid}/app.bsky.graph.follow/`) ||
        input.uri.length > 1024
      ) {
        throw new SidecarError(
          'follow_uri_invalid',
          'Only a follow record owned by the connected account can be removed.',
        )
      }
      return { uri: input.uri }
    }
    case 'actor.get_preferences':
      return {}
    case 'actor.put_preferences':
      if (!Array.isArray(input.preferences) || input.preferences.length > 200) {
        throw new SidecarError(
          'preferences_invalid',
          'AT Protocol preferences must be a bounded array.',
        )
      }
      if (!/^[a-f0-9]{64}$/.test(input.expected_sha256 ?? '')) {
        throw new SidecarError(
          'preferences_version_required',
          'Updating preferences requires the observed preference digest.',
        )
      }
      return {
        preferences: sanitizePreferences(input.preferences),
        expected_sha256: input.expected_sha256,
      }
    default:
      throw new SidecarError('bridge_operation_denied', 'The operation is not allowed.', {
        status: 403,
      })
  }
}

function paginationInput(input, ownerDid) {
  const limit = input.limit === undefined ? 100 : Number(input.limit)
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
    throw new SidecarError('pagination_invalid', 'The page size must be between 1 and 100.')
  }
  if (input.cursor !== undefined && (typeof input.cursor !== 'string' || input.cursor.length > 2048)) {
    throw new SidecarError('pagination_invalid', 'The pagination cursor is invalid.')
  }
  return {
    actor: ownerDid,
    limit,
    ...(input.cursor ? { cursor: input.cursor } : {}),
  }
}

function publicConnection(connection) {
  return {
    platform: 'bluesky',
    connection_ref: connection.connectionRef,
    external_subject: connection.did,
    status: 'active',
  }
}

function revokedConnection(connectionRef, proof) {
  return {
    platform: 'bluesky',
    connection_ref: connectionRef,
    status: 'revoked',
    revocation_proof: proof,
  }
}

function credentialScalars(session) {
  const output = new Set()
  const credentialField = /(?:access|refresh|id|session)?_?(?:jwt|token)|dpop|proof|private|secret/i
  const visit = (value, credentialContext, depth) => {
    if (depth > 6 || value === null || value === undefined) return
    if (typeof value === 'string') {
      if (credentialContext && value.length >= 8) output.add(value)
      return
    }
    if (Array.isArray(value)) {
      for (const child of value) visit(child, credentialContext, depth + 1)
      return
    }
    if (typeof value !== 'object') return
    for (const [key, child] of Object.entries(value)) {
      visit(child, credentialContext || credentialField.test(key), depth + 1)
    }
  }
  visit(session, false, 0)
  return output
}

class SerialBoundary {
  #tail = Promise.resolve()

  async run(callback) {
    const previous = this.#tail
    let release
    this.#tail = new Promise((resolve) => {
      release = resolve
    })
    await previous.catch(() => {})
    try {
      return await callback()
    } finally {
      release()
    }
  }
}

async function bestEffortRevoke(client, session) {
  const did = session?.did ?? session?.sub
  if (typeof did !== 'string') return
  try {
    await client.revoke(did)
  } catch {
    // The original failure remains authoritative and no credential detail is logged.
  }
}
