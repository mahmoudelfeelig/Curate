import { createHash, randomBytes } from 'node:crypto'

import { SidecarError } from './errors.mjs'
import {
  equalOpaque,
  requireDid,
  requireOpaqueReference,
  requireOwnerId,
} from './validation.mjs'

const MAX_OWNER_OAUTH_STATES = 8

export function opaqueReference(bytes = 32) {
  return randomBytes(bytes).toString('base64url')
}

export function stateDigest(state) {
  return createHash('sha256').update(state, 'utf8').digest('hex')
}

export class InMemorySecretStore {
  #records = new Map()

  async set(key, value) {
    this.#records.set(key, structuredClone(value))
  }

  async get(key) {
    const value = this.#records.get(key)
    return value === undefined ? undefined : structuredClone(value)
  }

  async del(key) {
    this.#records.delete(key)
  }

  get size() {
    return this.#records.size
  }

  toJSON() {
    return { type: 'InMemorySecretStore', contents: '<redacted>' }
  }
}

export class InMemoryOwnerStateStore {
  #records = new Map()

  async create(state, record) {
    const appState = requireOpaqueReference(state, 'oauth_app_state')
    const digest = stateDigest(appState)
    if (this.#records.has(digest)) {
      throw new SidecarError('oauth_state_collision', 'Start authorization again.', {
        status: 409,
      })
    }
    const ownerId = requireOwnerId(record.ownerId)
    const outstanding = [...this.#records.values()].filter(
      (value) => value.ownerId === ownerId,
    ).length
    if (outstanding >= MAX_OWNER_OAUTH_STATES) {
      throw new SidecarError(
        'oauth_start_limit_reached',
        'Too many authorization attempts are already pending for this owner.',
        { status: 429 },
      )
    }
    this.#records.set(digest, {
      ownerId,
      appState,
      expiresAt: Number(record.expiresAt),
      status: 'authorizing',
    })
  }

  async bindProtocolState(appState, protocolState) {
    const app = requireOpaqueReference(appState, 'oauth_app_state')
    const protocol = requireOpaqueReference(protocolState, 'oauth_protocol_state')
    const appDigest = stateDigest(app)
    const protocolDigest = stateDigest(protocol)
    const record = this.#records.get(appDigest)
    if (!record || record.status !== 'authorizing' || !equalOpaque(record.appState, app)) {
      throw new SidecarError('oauth_state_unknown', 'The OAuth transaction is unavailable.', {
        status: 409,
      })
    }
    if (this.#records.has(protocolDigest)) {
      throw new SidecarError('oauth_state_collision', 'Start authorization again.', {
        status: 409,
      })
    }
    this.#records.delete(appDigest)
    this.#records.set(protocolDigest, {
      ...record,
      protocolState: protocol,
      status: 'pending',
    })
  }

  async bound(appState, { ownerId, now }) {
    const app = requireOpaqueReference(appState, 'oauth_app_state')
    const owner = requireOwnerId(ownerId)
    const currentTime = Number(now)
    const record = [...this.#records.values()].find(
      (value) => value.ownerId === owner && equalOpaque(value.appState, app),
    )
    if (!record || record.status === 'authorizing' || !record.protocolState) {
      throw new SidecarError(
        'oauth_protocol_state_unbound',
        'The official OAuth client did not bind its protocol state.',
        { status: 502 },
      )
    }
    if (record.expiresAt <= currentTime) {
      await this.deleteByAppState(app)
      throw new SidecarError('oauth_state_expired', 'The OAuth transaction expired.', {
        status: 409,
      })
    }
    return { ...record }
  }

  async deleteByAppState(appState) {
    const app = requireOpaqueReference(appState, 'oauth_app_state')
    for (const [digest, record] of this.#records) {
      if (equalOpaque(record.appState, app)) this.#records.delete(digest)
    }
  }

  async consume(state, { ownerId, now }) {
    const digest = stateDigest(state)
    const record = this.#records.get(digest)
    if (!record) {
      throw new SidecarError(
        'oauth_state_unknown',
        'The OAuth transaction is missing or has already been used.',
        { status: 409 },
      )
    }
    if (record.ownerId !== requireOwnerId(ownerId)) {
      throw new SidecarError(
        'oauth_owner_mismatch',
        'The OAuth transaction belongs to a different signed-in user.',
        { status: 403 },
      )
    }
    if (record.expiresAt <= Number(now)) {
      this.#records.delete(digest)
      throw new SidecarError('oauth_state_expired', 'The OAuth transaction expired.', {
        status: 409,
      })
    }
    if (!record.protocolState || record.status === 'authorizing') {
      throw new SidecarError(
        'oauth_protocol_state_unbound',
        'The official OAuth client did not bind its protocol state.',
        { status: 409 },
      )
    }
    this.#records.delete(digest)
    return {
      ownerId: record.ownerId,
      appState: record.appState,
      protocolState: record.protocolState,
      expiresAt: record.expiresAt,
    }
  }

  async claim(state, { ownerId, now }) {
    const digest = stateDigest(state)
    const record = this.#records.get(digest)
    const owner = requireOwnerId(ownerId)
    if (!record) {
      throw new SidecarError(
        'oauth_state_unknown',
        'The OAuth transaction is missing or has already been used.',
        { status: 409 },
      )
    }
    if (record.ownerId !== owner) {
      throw new SidecarError(
        'oauth_owner_mismatch',
        'The OAuth transaction belongs to a different signed-in user.',
        { status: 403 },
      )
    }
    if (record.expiresAt <= Number(now)) {
      this.#records.delete(digest)
      throw new SidecarError('oauth_state_expired', 'The OAuth transaction expired.', {
        status: 409,
      })
    }
    if (!record.protocolState || record.status === 'authorizing') {
      throw new SidecarError(
        'oauth_protocol_state_unbound',
        'The official OAuth client did not bind its protocol state.',
        { status: 409 },
      )
    }
    if (record.status === 'processing') {
      throw new SidecarError(
        'oauth_callback_in_progress',
        'The OAuth callback is already being completed; start authorization again if it does not finish.',
        { status: 409 },
      )
    }
    record.status = 'processing'
    return {
      ownerId: record.ownerId,
      appState: record.appState,
      protocolState: record.protocolState,
      expiresAt: record.expiresAt,
    }
  }

  async finish(state, { ownerId }) {
    const digest = stateDigest(state)
    const record = this.#records.get(digest)
    if (record && record.ownerId !== requireOwnerId(ownerId)) {
      throw new SidecarError(
        'oauth_owner_mismatch',
        'The OAuth transaction belongs to a different signed-in user.',
        { status: 403 },
      )
    }
    this.#records.delete(digest)
  }

  async delete(state) {
    this.#records.delete(stateDigest(state))
  }

  get size() {
    return this.#records.size
  }
}

export class InMemoryOwnerConnectionStore {
  #byReference = new Map()
  #ownerStates
  #receipts = new Map()

  constructor(ownerStates) {
    this.#ownerStates = ownerStates
  }

  async bind({ ownerId, did, now }) {
    const canonicalOwner = requireOwnerId(ownerId)
    const canonicalDid = requireDid(did)
    const existing = [...this.#byReference.values()].find(
      (record) => record.did === canonicalDid && record.status !== 'revoked',
    )
    if (existing) {
      if (existing.ownerId !== canonicalOwner) {
        throw new SidecarError(
          'atproto_subject_already_bound',
          'This AT Protocol account is already connected to a different owner.',
          { status: 409 },
        )
      }
      if (existing.status !== 'active') {
        throw new SidecarError(
          'atproto_connection_revoking',
          'The AT Protocol connection is being revoked.',
          { status: 409 },
        )
      }
      return { ...existing }
    }
    const record = {
      connectionRef: opaqueReference(),
      ownerId: canonicalOwner,
      did: canonicalDid,
      createdAt: Number(now),
      status: 'active',
    }
    this.#byReference.set(record.connectionRef, record)
    return { ...record }
  }

  async bindCallback({ ownerId, did, state, now, receiptTtlMs }) {
    if (!this.#ownerStates || typeof this.#ownerStates.finish !== 'function') {
      throw new SidecarError(
        'sidecar_storage_unconfigured',
        'The callback transaction store is unavailable.',
        { status: 503 },
      )
    }
    const record = await this.bind({ ownerId, did, now })
    await this.#ownerStates.finish(state, { ownerId })
    this.#receipts.set(stateDigest(state), {
      ownerId: record.ownerId,
      connectionRef: record.connectionRef,
      did: record.did,
      expiresAt: Number(now) + Number(receiptTtlMs),
    })
    return record
  }

  async callbackResult(state, { ownerId, now }) {
    const digest = stateDigest(state)
    const owner = requireOwnerId(ownerId)
    const receipt = this.#receipts.get(digest)
    if (!receipt) return undefined
    if (receipt.ownerId !== owner) {
      throw new SidecarError(
        'oauth_owner_mismatch',
        'The OAuth transaction belongs to a different signed-in user.',
        { status: 403 },
      )
    }
    if (receipt.expiresAt <= Number(now)) {
      this.#receipts.delete(digest)
      return undefined
    }
    const connection = this.#byReference.get(receipt.connectionRef)
    if (!connection || connection.ownerId !== owner || connection.status !== 'active') {
      this.#receipts.delete(digest)
      return undefined
    }
    return { ...connection }
  }

  async get(connectionRef, { ownerId }) {
    requireOpaqueReference(connectionRef, 'connection_ref')
    const record = this.#byReference.get(connectionRef)
    if (!record || record.ownerId !== requireOwnerId(ownerId)) {
      throw new SidecarError(
        'atproto_connection_not_found',
        'The AT Protocol connection was not found.',
        { status: 404 },
      )
    }
    return { ...record }
  }

  async getActive(connectionRef, { ownerId }) {
    const record = await this.get(connectionRef, { ownerId })
    if (record.status !== 'active') {
      throw new SidecarError(
        'atproto_connection_inactive',
        'The AT Protocol connection is not active.',
        { status: 409 },
      )
    }
    return record
  }

  async beginRevoke(connectionRef, { ownerId, now }) {
    const record = await this.get(connectionRef, { ownerId })
    if (record.status === 'active') {
      record.status = 'revoking'
      record.revokingAt = Number(now)
      this.#byReference.set(record.connectionRef, record)
    }
    for (const [digest, receipt] of this.#receipts) {
      if (receipt.connectionRef === record.connectionRef) this.#receipts.delete(digest)
    }
    return { ...record }
  }

  async completeRevoke(connectionRef, { ownerId, now }) {
    const record = await this.get(connectionRef, { ownerId })
    if (!Number.isFinite(record.providerConfirmedAt)) {
      throw new SidecarError(
        'session_revoke_unconfirmed',
        'Provider revocation has not been durably confirmed.',
        { status: 409 },
      )
    }
    record.status = 'revoked'
    record.revokedAt = Number(now)
    delete record.revokingAt
    this.#byReference.set(record.connectionRef, record)
    return { ...record }
  }

  async confirmRevoke(connectionRef, { ownerId, now }) {
    const record = await this.get(connectionRef, { ownerId })
    if (record.status === 'revoked') return record
    if (record.status !== 'revoking') {
      throw new SidecarError(
        'session_revoke_unconfirmed',
        'Provider revocation cannot be confirmed before it begins.',
        { status: 409 },
      )
    }
    record.providerConfirmedAt = Number(now)
    this.#byReference.set(record.connectionRef, record)
    return { ...record }
  }

  async delete(connectionRef, { ownerId }) {
    const record = await this.get(connectionRef, { ownerId })
    this.#byReference.delete(connectionRef)
    return record
  }

  get size() {
    return this.#byReference.size
  }
}

export class InMemorySessionLeaseStore {
  #records = new Map()

  async create({ ownerId, connectionRef, did, session, now, ttlMs }) {
    if (!session || typeof session !== 'object') {
      throw new SidecarError('session_restore_failed', 'The OAuth session was unavailable.', {
        status: 503,
      })
    }
    const record = {
      leaseRef: opaqueReference(),
      ownerId: requireOwnerId(ownerId),
      connectionRef: requireOpaqueReference(connectionRef, 'connection_ref'),
      did: requireDid(did),
      session,
      expiresAt: Number(now) + Number(ttlMs),
    }
    for (const [leaseRef, existing] of this.#records) {
      if (existing.connectionRef === record.connectionRef) this.#records.delete(leaseRef)
    }
    this.#records.set(record.leaseRef, record)
    return publicLease(record)
  }

  async get(leaseRef, { ownerId, now }) {
    requireOpaqueReference(leaseRef, 'lease_ref')
    const record = this.#records.get(leaseRef)
    if (!record || record.ownerId !== requireOwnerId(ownerId)) {
      throw new SidecarError('session_lease_not_found', 'The session lease was not found.', {
        status: 404,
      })
    }
    if (record.expiresAt <= Number(now)) {
      this.#records.delete(leaseRef)
      throw new SidecarError('session_lease_expired', 'The session lease expired.', {
        status: 409,
      })
    }
    return record
  }

  async deleteForConnection(connectionRef) {
    for (const [leaseRef, record] of this.#records) {
      if (record.connectionRef === connectionRef) this.#records.delete(leaseRef)
    }
  }

  get size() {
    return this.#records.size
  }

  toJSON() {
    return { type: 'InMemorySessionLeaseStore', contents: '<redacted>' }
  }
}

function publicLease(record) {
  return {
    leaseRef: record.leaseRef,
    connectionRef: record.connectionRef,
    did: record.did,
    expiresAt: record.expiresAt,
  }
}
