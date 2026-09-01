import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto'
import {
  chmod,
  lstat,
  mkdir,
  open,
  readFile,
  rename,
  stat,
  unlink,
} from 'node:fs/promises'
import { dirname, isAbsolute, resolve } from 'node:path'

import { SidecarError } from './errors.mjs'
import { opaqueReference, stateDigest } from './stores.mjs'
import { requireDid, requireOpaqueReference, requireOwnerId } from './validation.mjs'

const FORMAT_VERSION = 1
const ALGORITHM = 'A256GCM'
const MAX_ENCRYPTED_BYTES = 16 * 1024 * 1024
const MAX_PLAINTEXT_BYTES = 8 * 1024 * 1024
const MAX_RECORD_BYTES = 1024 * 1024
const MAX_RECORDS = 50_000
const CONNECTION_TOMBSTONE_TTL_MS = 30 * 24 * 60 * 60 * 1000
const OAUTH_STATE_TTL_MS = 15 * 60 * 1000
const MAX_OFFICIAL_OAUTH_STATES = 1024
const MAX_OWNER_OAUTH_STATES = 8
const RECOVERED_RECORDS = Symbol('recoveredRecords')
const PROCESS_STARTED_AT_MS = Math.round(Date.now() - process.uptime() * 1000)
const NAMESPACES = Object.freeze([
  'oauth_state',
  'oauth_session',
  'owner_state',
  'owner_connection',
  'session_lease',
  'oauth_receipt',
])
const LEGACY_NAMESPACES = Object.freeze(NAMESPACES.filter((value) => value !== 'oauth_receipt'))

export class EncryptedAtomicStore {
  #closed = false
  #encryptionKey
  #filePath
  #keyId
  #lockHandle
  #lockNonce
  #lockPath
  #recoveryHandle
  #recoveryNonce
  #recoveryPath
  #snapshot = emptySnapshot()
  #tail = Promise.resolve()

  static async open({ filePath, encryptionKey, keyId = 'local-v1' }) {
    const store = new EncryptedAtomicStore({ filePath, encryptionKey, keyId })
    try {
      await store.#acquireLock()
      const snapshot = await store.#load()
      const now = Date.now()
      const migration = migrateLegacyOAuthStates(snapshot, now)
      const preliminaryChanges =
        (snapshot[RECOVERED_RECORDS] ?? 0) +
        migration.changes +
        pruneExpired(snapshot, now, { includeLeases: true })
      const changes =
        preliminaryChanges +
        boundOAuthStateSnapshot(snapshot, migration.keys, preliminaryChanges > 0)
      if (changes > 0) {
        snapshot.revision += 1
        await store.#persist(snapshot)
      }
      store.#snapshot = snapshot
      return store
    } catch (error) {
      await store.#releaseRecoveryLock()
      await store.#releaseLock()
      throw error
    }
  }

  constructor({ filePath, encryptionKey, keyId }) {
    if (typeof filePath !== 'string' || !isAbsolute(filePath) || filePath.length > 4096) {
      throw new SidecarError(
        'sidecar_store_path_invalid',
        'The encrypted sidecar store requires an absolute file path.',
        { status: 503 },
      )
    }
    if (!Buffer.isBuffer(encryptionKey) || encryptionKey.length !== 32) {
      throw new SidecarError(
        'sidecar_store_key_invalid',
        'The encrypted sidecar store requires an external 32-byte key.',
        { status: 503 },
      )
    }
    if (typeof keyId !== 'string' || !/^[A-Za-z0-9._-]{1,128}$/.test(keyId)) {
      throw new SidecarError(
        'sidecar_store_key_invalid',
        'The encrypted sidecar store key identifier is invalid.',
        { status: 503 },
      )
    }
    this.#filePath = resolve(filePath)
    this.#lockPath = `${this.#filePath}.lock`
    this.#recoveryPath = `${this.#lockPath}.recovery`
    this.#encryptionKey = Buffer.from(encryptionKey)
    this.#keyId = keyId
  }

  async get(namespace, key) {
    requireNamespace(namespace)
    requireRecordKey(key)
    await this.#tail
    this.#requireOpen()
    return cloneValue(this.#snapshot.namespaces.get(namespace).get(key))
  }

  async set(namespace, key, value) {
    requireNamespace(namespace)
    requireRecordKey(key)
    const safeValue = clonePersistable(value)
    return this.mutate(namespace, (records) => {
      records.set(key, safeValue)
    })
  }

  async del(namespace, key) {
    requireNamespace(namespace)
    requireRecordKey(key)
    return this.mutate(namespace, (records) => records.delete(key))
  }

  async getAndDeleteIf(namespace, key, predicate) {
    requireNamespace(namespace)
    requireRecordKey(key)
    if (typeof predicate !== 'function') {
      throw new TypeError('store conditional-delete predicate is required')
    }
    return this.#enqueue(async () => {
      this.#requireOpen()
      const record = cloneValue(this.#snapshot.namespaces.get(namespace).get(key))
      if (record === undefined || !predicate(cloneValue(record))) return record

      const next = cloneSnapshot(this.#snapshot)
      pruneExpired(next, Date.now())
      next.namespaces.get(namespace).delete(key)
      next.revision += 1
      await this.#persist(next)
      this.#snapshot = next
      return undefined
    })
  }

  async mutate(namespace, callback) {
    requireNamespace(namespace)
    if (typeof callback !== 'function') throw new TypeError('store mutation callback is required')
    return this.mutateNamespaces((namespaces) => callback(namespaces.get(namespace)))
  }

  async mutateNamespaces(callback) {
    if (typeof callback !== 'function') throw new TypeError('store mutation callback is required')
    return this.#enqueue(async () => {
      this.#requireOpen()
      const next = cloneSnapshot(this.#snapshot)
      pruneExpired(next, Date.now())
      const result = await callback(next.namespaces)
      if (recordCount(next) > MAX_RECORDS) {
        throw new SidecarError(
          'sidecar_store_capacity_exceeded',
          'The encrypted sidecar store reached its safe record limit.',
          { status: 503 },
        )
      }
      next.revision += 1
      await this.#persist(next)
      this.#snapshot = next
      return cloneValue(result)
    })
  }

  size(namespace) {
    requireNamespace(namespace)
    this.#requireOpen()
    return this.#snapshot.namespaces.get(namespace).size
  }

  async close() {
    if (this.#closed) return
    await this.#tail
    this.#closed = true
    this.#encryptionKey.fill(0)
    await this.#releaseLock()
  }

  toJSON() {
    return {
      type: 'EncryptedAtomicStore',
      file: '<configured>',
      contents: '<redacted>',
    }
  }

  #enqueue(callback) {
    const run = this.#tail.then(callback)
    this.#tail = run.catch(() => {})
    return run
  }

  #requireOpen() {
    if (this.#closed) {
      throw new SidecarError('sidecar_store_closed', 'The encrypted sidecar store is closed.', {
        status: 503,
      })
    }
  }

  async #acquireLock() {
    await mkdir(dirname(this.#filePath), { recursive: true, mode: 0o700 })
    await rejectSymlink(this.#filePath)
    await this.#acquireRecoveryLock()
    this.#lockNonce = randomBytes(24).toString('base64url')
    const record = JSON.stringify({
      version: 1,
      pid: process.pid,
      nonce: this.#lockNonce,
      process_identity: await processIdentity(process.pid),
      created_at: new Date().toISOString(),
    })
    try {
      for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
          this.#lockHandle = await open(this.#lockPath, 'wx', 0o600)
          await this.#lockHandle.writeFile(`${record}\n`, { encoding: 'utf8' })
          await this.#lockHandle.sync()
          return
        } catch (error) {
          if (error?.code !== 'EEXIST') throw storeUnavailable(error)
          if (await lockOwnerIsActive(this.#lockPath)) {
            throw new SidecarError(
              'sidecar_store_locked',
              'Another sidecar process owns the encrypted state store.',
              { status: 503 },
            )
          }
          try {
            await unlink(this.#lockPath)
          } catch (unlinkError) {
            if (unlinkError?.code !== 'ENOENT') throw storeUnavailable(unlinkError)
          }
        }
      }
      throw new SidecarError(
        'sidecar_store_locked',
        'The encrypted sidecar state lock could not be acquired.',
        { status: 503 },
      )
    } finally {
      await this.#releaseRecoveryLock()
    }
  }

  async #acquireRecoveryLock() {
    this.#recoveryNonce = randomBytes(24).toString('base64url')
    const record = JSON.stringify({
      version: 1,
      pid: process.pid,
      nonce: this.#recoveryNonce,
      process_identity: await processIdentity(process.pid),
      created_at: new Date().toISOString(),
    })
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        this.#recoveryHandle = await open(this.#recoveryPath, 'wx', 0o600)
        await this.#recoveryHandle.writeFile(`${record}\n`, { encoding: 'utf8' })
        await this.#recoveryHandle.sync()
        return
      } catch (error) {
        if (error?.code !== 'EEXIST') throw storeUnavailable(error)
        if (await lockOwnerIsActive(this.#recoveryPath)) {
          throw new SidecarError(
            'sidecar_store_locked',
            'Another sidecar process is starting or recovering the encrypted state store.',
            { status: 503 },
          )
        }
        const quarantine = `${this.#recoveryPath}.${randomBytes(12).toString('hex')}.stale`
        try {
          await rename(this.#recoveryPath, quarantine)
          await unlink(quarantine).catch(() => {})
        } catch (renameError) {
          if (renameError?.code !== 'ENOENT') throw storeUnavailable(renameError)
        }
      }
    }
    throw new SidecarError(
      'sidecar_store_locked',
      'The encrypted sidecar recovery lock could not be acquired.',
      { status: 503 },
    )
  }

  async #releaseRecoveryLock() {
    try {
      await this.#recoveryHandle?.close()
    } catch {
      // The exclusive recovery marker still prevents unsafe concurrent recovery.
    }
    this.#recoveryHandle = undefined
    if (!this.#recoveryNonce) return
    try {
      const value = JSON.parse(await readFile(this.#recoveryPath, 'utf8'))
      if (value?.nonce === this.#recoveryNonce) await unlink(this.#recoveryPath)
    } catch (error) {
      if (error?.code !== 'ENOENT') {
        // A leftover recovery marker fails future startup closed.
      }
    } finally {
      this.#recoveryNonce = undefined
    }
  }

  async #releaseLock() {
    try {
      await this.#lockHandle?.close()
    } catch {
      // The lock file identity check below remains authoritative.
    }
    this.#lockHandle = undefined
    if (!this.#lockNonce) return
    try {
      const value = JSON.parse(await readFile(this.#lockPath, 'utf8'))
      if (value?.nonce === this.#lockNonce) await unlink(this.#lockPath)
    } catch (error) {
      if (error?.code !== 'ENOENT') {
        // Close is best effort. A stale lock is recovered safely on next startup.
      }
    } finally {
      this.#lockNonce = undefined
    }
  }

  async #load() {
    let raw
    try {
      const metadata = await stat(this.#filePath)
      if (metadata.size > MAX_ENCRYPTED_BYTES) {
        throw new SidecarError(
          'sidecar_store_capacity_exceeded',
          'The encrypted sidecar store exceeded its safe size limit.',
          { status: 503 },
        )
      }
      raw = await readFile(this.#filePath, 'utf8')
    } catch (error) {
      if (error?.code === 'ENOENT') return emptySnapshot()
      if (error instanceof SidecarError) throw error
      throw storeUnavailable(error)
    }
    try {
      const envelope = JSON.parse(raw)
      assertEnvelope(envelope, this.#keyId)
      const iv = Buffer.from(envelope.iv, 'base64url')
      const ciphertext = Buffer.from(envelope.ciphertext, 'base64url')
      const tag = Buffer.from(envelope.tag, 'base64url')
      if (iv.length !== 12 || tag.length !== 16 || ciphertext.length === 0) throw new Error()
      const decipher = createDecipheriv('aes-256-gcm', this.#encryptionKey, iv)
      decipher.setAAD(aad(this.#keyId))
      decipher.setAuthTag(tag)
      const plaintext = Buffer.concat([decipher.update(ciphertext), decipher.final()])
      const value = JSON.parse(plaintext.toString('utf8'))
      plaintext.fill(0)
      return parseSnapshot(value)
    } catch (error) {
      if (error instanceof SidecarError) throw error
      throw new SidecarError(
        'sidecar_store_decryption_failed',
        'The encrypted sidecar state could not be authenticated or decoded.',
        { status: 503, cause: error },
      )
    }
  }

  async #persist(snapshot) {
    const plaintext = Buffer.from(JSON.stringify(serializeSnapshot(snapshot)), 'utf8')
    if (plaintext.length > MAX_PLAINTEXT_BYTES) {
      plaintext.fill(0)
      throw new SidecarError(
        'sidecar_store_capacity_exceeded',
        'The encrypted sidecar store reached its safe size limit.',
        { status: 503 },
      )
    }
    const iv = randomBytes(12)
    const cipher = createCipheriv('aes-256-gcm', this.#encryptionKey, iv)
    cipher.setAAD(aad(this.#keyId))
    const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()])
    plaintext.fill(0)
    const envelope = {
      version: FORMAT_VERSION,
      algorithm: ALGORITHM,
      key_id: this.#keyId,
      iv: iv.toString('base64url'),
      ciphertext: ciphertext.toString('base64url'),
      tag: cipher.getAuthTag().toString('base64url'),
    }
    const temporaryPath = `${this.#filePath}.${process.pid}.${randomBytes(12).toString('hex')}.tmp`
    let handle
    try {
      handle = await open(temporaryPath, 'wx', 0o600)
      await handle.writeFile(`${JSON.stringify(envelope)}\n`, { encoding: 'utf8' })
      await handle.sync()
      await handle.close()
      handle = undefined
      await rename(temporaryPath, this.#filePath)
      await chmod(this.#filePath, 0o600).catch(() => {})
      await syncDirectory(dirname(this.#filePath))
    } catch (error) {
      try {
        await handle?.close()
      } catch {
        // Preserve the original persistence failure.
      }
      try {
        await unlink(temporaryPath)
      } catch {
        // A uniquely named encrypted temporary file is safe to leave for cleanup.
      }
      throw storeUnavailable(error)
    }
  }
}

export class DurableSecretStore {
  constructor(
    database,
    namespace,
    {
      clock = () => Date.now(),
      oauthStateTtlMs = OAUTH_STATE_TTL_MS,
      maxOauthStates = MAX_OFFICIAL_OAUTH_STATES,
    } = {},
  ) {
    requireDatabase(database)
    if (!['oauth_state', 'oauth_session'].includes(namespace)) {
      throw new TypeError('durable OAuth secret namespace is invalid')
    }
    if (
      typeof clock !== 'function' ||
      !Number.isSafeInteger(oauthStateTtlMs) ||
      oauthStateTtlMs < 60_000 ||
      !Number.isSafeInteger(maxOauthStates) ||
      maxOauthStates < 1 ||
      maxOauthStates > MAX_OFFICIAL_OAUTH_STATES
    ) {
      throw new TypeError('durable OAuth state retention options are invalid')
    }
    this.database = database
    this.namespace = namespace
    this.clock = clock
    this.oauthStateTtlMs = oauthStateTtlMs
    this.maxOauthStates = maxOauthStates
  }

  async set(key, value) {
    requireRecordKey(key)
    if (this.namespace !== 'oauth_state') {
      return this.database.set(this.namespace, key, value)
    }
    const now = requireFiniteNumber(this.clock(), 'current time')
    const safeValue = clonePersistable(value)
    const envelope = clonePersistable({
      format: 'timed-v1',
      storedAt: now,
      expiresAt: now + this.oauthStateTtlMs,
      value: safeValue,
    })
    return this.database.mutate(this.namespace, (records) => {
      for (const [recordKey, record] of records) {
        const expiresAt = oauthStateExpiresAt(record, this.oauthStateTtlMs)
        if (expiresAt !== undefined && expiresAt <= now) {
          records.delete(recordKey)
        }
      }
      if (!records.has(key) && records.size >= this.maxOauthStates) {
        throw new SidecarError(
          'oauth_state_capacity_exceeded',
          'The sidecar has too many pending official OAuth transactions.',
          { status: 429 },
        )
      }
      records.set(key, envelope)
    })
  }

  async get(key) {
    if (this.namespace !== 'oauth_state') return this.database.get(this.namespace, key)
    const now = requireFiniteNumber(this.clock(), 'current time')
    const record = await this.database.getAndDeleteIf(this.namespace, key, (candidate) => {
      const expiresAt = oauthStateExpiresAt(candidate, this.oauthStateTtlMs)
      return expiresAt !== undefined && expiresAt <= now
    })
    return isTimedOAuthState(record) || isLegacyTimedOAuthState(record)
      ? cloneValue(record.value)
      : record
  }

  async del(key) {
    return this.database.del(this.namespace, key)
  }

  get size() {
    return this.database.size(this.namespace)
  }

  toJSON() {
    return { type: 'DurableSecretStore', contents: '<redacted>' }
  }
}

export class DurableOwnerStateStore {
  constructor(database) {
    requireDatabase(database)
    this.database = database
  }

  async create(state, record) {
    const digest = stateDigest(state)
    const ownerId = requireOwnerId(record.ownerId)
    const expiresAt = requireFiniteNumber(record.expiresAt, 'OAuth state expiry')
    return this.database.mutateNamespaces((namespaces) => {
      const records = namespaces.get('owner_state')
      if (records.has(digest) || namespaces.get('oauth_receipt').has(digest)) {
        throw new SidecarError('oauth_state_collision', 'Start authorization again.', {
          status: 409,
        })
      }
      const outstanding = [...records.values()].filter(
        (value) => value.ownerId === ownerId,
      ).length
      if (outstanding >= MAX_OWNER_OAUTH_STATES) {
        throw new SidecarError(
          'oauth_start_limit_reached',
          'Too many authorization attempts are already pending for this owner.',
          { status: 429 },
        )
      }
      records.set(digest, { ownerId, expiresAt, status: 'pending' })
    })
  }

  async consume(state, { ownerId, now }) {
    const digest = stateDigest(state)
    const owner = requireOwnerId(ownerId)
    const currentTime = requireFiniteNumber(now, 'current time')
    const result = await this.database.mutate('owner_state', (records) => {
      const record = records.get(digest)
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
      if (record.expiresAt <= currentTime) {
        records.delete(digest)
        return { expired: true }
      }
      records.delete(digest)
      return {
        expired: false,
        record: { ownerId: record.ownerId, expiresAt: record.expiresAt },
      }
    })
    if (result.expired) {
      throw new SidecarError('oauth_state_expired', 'The OAuth transaction expired.', {
        status: 409,
      })
    }
    return result.record
  }

  async claim(state, { ownerId, now }) {
    const digest = stateDigest(state)
    const owner = requireOwnerId(ownerId)
    const currentTime = requireFiniteNumber(now, 'current time')
    const result = await this.database.mutate('owner_state', (records) => {
      const record = records.get(digest)
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
      if (record.expiresAt <= currentTime) {
        records.delete(digest)
        return { expired: true }
      }
      if (record.status === 'processing') {
        throw new SidecarError(
          'oauth_callback_in_progress',
          'The OAuth callback is already being completed; start authorization again if it does not finish.',
          { status: 409 },
        )
      }
      records.set(digest, { ...record, status: 'processing' })
      return {
        expired: false,
        record: { ownerId: record.ownerId, expiresAt: record.expiresAt },
      }
    })
    if (result.expired) {
      throw new SidecarError('oauth_state_expired', 'The OAuth transaction expired.', {
        status: 409,
      })
    }
    return result.record
  }

  async finish(state, { ownerId }) {
    const digest = stateDigest(state)
    const owner = requireOwnerId(ownerId)
    return this.database.mutate('owner_state', (records) => {
      const record = records.get(digest)
      if (record && record.ownerId !== owner) {
        throw new SidecarError(
          'oauth_owner_mismatch',
          'The OAuth transaction belongs to a different signed-in user.',
          { status: 403 },
        )
      }
      records.delete(digest)
    })
  }

  async delete(state) {
    return this.database.del('owner_state', stateDigest(state))
  }

  get size() {
    return this.database.size('owner_state')
  }
}

export class DurableOwnerConnectionStore {
  constructor(database) {
    requireDatabase(database)
    this.database = database
  }

  async bind({ ownerId, did, now }) {
    const owner = requireOwnerId(ownerId)
    const subject = requireDid(did)
    const createdAt = requireFiniteNumber(now, 'connection time')
    return this.database.mutate('owner_connection', (records) => {
      const existing = [...records.values()].find(
        (record) => record.did === subject && connectionStatus(record) !== 'revoked',
      )
      if (existing) {
        if (existing.ownerId !== owner) {
          throw new SidecarError(
            'atproto_subject_already_bound',
            'This AT Protocol account is already connected to a different owner.',
            { status: 409 },
          )
        }
        if (connectionStatus(existing) !== 'active') {
          throw new SidecarError(
            'atproto_connection_revoking',
            'The AT Protocol connection is being revoked.',
            { status: 409 },
          )
        }
        return { ...existing, status: 'active' }
      }
      const record = {
        connectionRef: opaqueReference(),
        ownerId: owner,
        did: subject,
        createdAt,
        status: 'active',
      }
      records.set(record.connectionRef, record)
      return { ...record }
    })
  }

  async bindCallback({ ownerId, did, state, now, receiptTtlMs }) {
    const owner = requireOwnerId(ownerId)
    const subject = requireDid(did)
    const digest = stateDigest(state)
    const currentTime = requireFiniteNumber(now, 'connection time')
    const receiptLifetime = requireFiniteNumber(receiptTtlMs, 'callback receipt lifetime')
    return this.database.mutateNamespaces((namespaces) => {
      const states = namespaces.get('owner_state')
      const stateRecord = states.get(digest)
      if (!stateRecord || stateRecord.ownerId !== owner || stateRecord.status !== 'processing') {
        throw new SidecarError(
          'oauth_state_unknown',
          'The OAuth transaction is missing or has already been used.',
          { status: 409 },
        )
      }
      if (stateRecord.expiresAt <= currentTime) {
        states.delete(digest)
        throw new SidecarError('oauth_state_expired', 'The OAuth transaction expired.', {
          status: 409,
        })
      }
      const connections = namespaces.get('owner_connection')
      const existing = [...connections.values()].find(
        (record) => record.did === subject && connectionStatus(record) !== 'revoked',
      )
      let record
      if (existing) {
        if (existing.ownerId !== owner) {
          throw new SidecarError(
            'atproto_subject_already_bound',
            'This AT Protocol account is already connected to a different owner.',
            { status: 409 },
          )
        }
        if (connectionStatus(existing) !== 'active') {
          throw new SidecarError(
            'atproto_connection_revoking',
            'The AT Protocol connection is being revoked.',
            { status: 409 },
          )
        }
        record = { ...existing, status: 'active' }
      } else {
        record = {
          connectionRef: opaqueReference(),
          ownerId: owner,
          did: subject,
          createdAt: currentTime,
          status: 'active',
        }
        connections.set(record.connectionRef, record)
      }
      namespaces.get('oauth_receipt').set(digest, {
        ownerId: owner,
        connectionRef: record.connectionRef,
        did: subject,
        expiresAt: currentTime + receiptLifetime,
      })
      states.delete(digest)
      return { ...record }
    })
  }

  async callbackResult(state, { ownerId, now }) {
    const digest = stateDigest(state)
    const owner = requireOwnerId(ownerId)
    const currentTime = requireFiniteNumber(now, 'current time')
    const receipt = await this.database.get('oauth_receipt', digest)
    if (!receipt) return undefined
    if (receipt.ownerId !== owner) {
      throw new SidecarError(
        'oauth_owner_mismatch',
        'The OAuth transaction belongs to a different signed-in user.',
        { status: 403 },
      )
    }
    if (receipt.expiresAt <= currentTime) {
      await this.database.del('oauth_receipt', digest)
      return undefined
    }
    const record = await this.database.get('owner_connection', receipt.connectionRef)
    if (
      !record ||
      record.ownerId !== owner ||
      record.did !== receipt.did ||
      connectionStatus(record) !== 'active'
    ) {
      await this.database.del('oauth_receipt', digest)
      return undefined
    }
    return { ...record, status: 'active' }
  }

  async get(connectionRef, { ownerId }) {
    const reference = requireOpaqueReference(connectionRef, 'connection_ref')
    const owner = requireOwnerId(ownerId)
    const record = await this.database.get('owner_connection', reference)
    if (!record || record.ownerId !== owner) {
      throw new SidecarError(
        'atproto_connection_not_found',
        'The AT Protocol connection was not found.',
        { status: 404 },
      )
    }
    return { ...record, status: connectionStatus(record) }
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
    const reference = requireOpaqueReference(connectionRef, 'connection_ref')
    const owner = requireOwnerId(ownerId)
    const currentTime = requireFiniteNumber(now, 'revocation time')
    return this.database.mutateNamespaces((namespaces) => {
      const connections = namespaces.get('owner_connection')
      const current = connections.get(reference)
      if (!current || current.ownerId !== owner) {
        throw new SidecarError(
          'atproto_connection_not_found',
          'The AT Protocol connection was not found.',
          { status: 404 },
        )
      }
      const record = { ...current, status: connectionStatus(current) }
      if (record.status === 'active') {
        record.status = 'revoking'
        record.revokingAt = currentTime
        connections.set(reference, record)
      }
      removeReceipts(namespaces.get('oauth_receipt'), reference)
      return { ...record }
    })
  }

  async completeRevoke(connectionRef, { ownerId, now }) {
    const reference = requireOpaqueReference(connectionRef, 'connection_ref')
    const owner = requireOwnerId(ownerId)
    const currentTime = requireFiniteNumber(now, 'revocation time')
    return this.database.mutateNamespaces((namespaces) => {
      const connections = namespaces.get('owner_connection')
      const current = connections.get(reference)
      if (!current || current.ownerId !== owner) {
        throw new SidecarError(
          'atproto_connection_not_found',
          'The AT Protocol connection was not found.',
          { status: 404 },
        )
      }
      const record = {
        ...current,
        status: 'revoked',
        revokedAt: currentTime,
      }
      delete record.revokingAt
      connections.set(reference, record)
      for (const [leaseRef, lease] of namespaces.get('session_lease')) {
        if (lease.connectionRef === reference) namespaces.get('session_lease').delete(leaseRef)
      }
      removeReceipts(namespaces.get('oauth_receipt'), reference)
      return { ...record }
    })
  }

  async delete(connectionRef, { ownerId }) {
    const record = await this.get(connectionRef, { ownerId })
    await this.database.del('owner_connection', record.connectionRef)
    return record
  }

  get size() {
    return this.database.size('owner_connection')
  }
}

export class DurableSessionLeaseStore {
  #restoreSession
  #sessions = new Map()

  constructor(database) {
    requireDatabase(database)
    this.database = database
  }

  setSessionRestorer(callback) {
    if (typeof callback !== 'function') throw new TypeError('session restorer is required')
    this.#restoreSession = callback
  }

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
      expiresAt:
        requireFiniteNumber(now, 'current time') +
        requireFiniteNumber(ttlMs, 'lease lifetime'),
    }
    const removed = await this.database.mutate('session_lease', (records) => {
      const references = []
      for (const [leaseRef, existing] of records) {
        if (existing.connectionRef === record.connectionRef) {
          records.delete(leaseRef)
          references.push(leaseRef)
        }
      }
      records.set(record.leaseRef, record)
      return references
    })
    for (const leaseRef of removed) this.#sessions.delete(leaseRef)
    this.#sessions.set(record.leaseRef, session)
    return publicLease(record)
  }

  async get(leaseRef, { ownerId, now }) {
    const reference = requireOpaqueReference(leaseRef, 'lease_ref')
    const owner = requireOwnerId(ownerId)
    const currentTime = requireFiniteNumber(now, 'current time')
    const record = await this.database.get('session_lease', reference)
    if (!record || record.ownerId !== owner) {
      throw new SidecarError('session_lease_not_found', 'The session lease was not found.', {
        status: 404,
      })
    }
    if (record.expiresAt <= currentTime) {
      await this.database.del('session_lease', reference)
      this.#sessions.delete(reference)
      throw new SidecarError('session_lease_expired', 'The session lease expired.', {
        status: 409,
      })
    }
    let session = this.#sessions.get(reference)
    if (!session) {
      if (typeof this.#restoreSession !== 'function') {
        throw new SidecarError(
          'session_restore_failed',
          'The OAuth session could not be restored; reconnect the account.',
          { status: 409 },
        )
      }
      try {
        session = await this.#restoreSession(record.did)
      } catch (error) {
        throw new SidecarError(
          'session_restore_failed',
          'The OAuth session could not be restored; reconnect the account.',
          { status: 409, cause: error },
        )
      }
      const current = await this.database.get('session_lease', reference)
      if (!current || current.ownerId !== owner || current.expiresAt <= currentTime) {
        throw new SidecarError('session_lease_not_found', 'The session lease was not found.', {
          status: 404,
        })
      }
      this.#sessions.set(reference, session)
    }
    return { ...record, session }
  }

  async deleteForConnection(connectionRef) {
    const reference = requireOpaqueReference(connectionRef, 'connection_ref')
    const removed = await this.database.mutate('session_lease', (records) => {
      const references = []
      for (const [leaseRef, record] of records) {
        if (record.connectionRef === reference) {
          records.delete(leaseRef)
          references.push(leaseRef)
        }
      }
      return references
    })
    for (const leaseRef of removed) this.#sessions.delete(leaseRef)
  }

  get size() {
    return this.database.size('session_lease')
  }

  toJSON() {
    return { type: 'DurableSessionLeaseStore', contents: '<redacted>' }
  }
}

export async function createDurableSidecarStores(options) {
  const database = await EncryptedAtomicStore.open(options)
  return Object.freeze({
    database,
    oauthStateStore: new DurableSecretStore(database, 'oauth_state'),
    oauthSessionStore: new DurableSecretStore(database, 'oauth_session'),
    ownerStates: new DurableOwnerStateStore(database),
    connections: new DurableOwnerConnectionStore(database),
    sessionLeases: new DurableSessionLeaseStore(database),
    close: () => database.close(),
  })
}

function emptySnapshot() {
  return {
    revision: 0,
    namespaces: new Map(NAMESPACES.map((namespace) => [namespace, new Map()])),
  }
}

function cloneSnapshot(snapshot) {
  return {
    revision: snapshot.revision,
    namespaces: new Map(
      [...snapshot.namespaces].map(([namespace, records]) => [
        namespace,
        new Map([...records].map(([key, value]) => [key, cloneValue(value)])),
      ]),
    ),
  }
}

function serializeSnapshot(snapshot) {
  return {
    version: FORMAT_VERSION,
    revision: snapshot.revision,
    namespaces: NAMESPACES.map((namespace) => [
      namespace,
      [...snapshot.namespaces.get(namespace).entries()].sort(([left], [right]) =>
        left.localeCompare(right),
      ),
    ]),
  }
}

function parseSnapshot(value) {
  if (
    !value ||
    typeof value !== 'object' ||
    value.version !== FORMAT_VERSION ||
    !Number.isSafeInteger(value.revision) ||
    value.revision < 0 ||
    !Array.isArray(value.namespaces)
  ) {
    throw new Error('snapshot shape is invalid')
  }
  const snapshot = emptySnapshot()
  snapshot.revision = value.revision
  snapshot[RECOVERED_RECORDS] = 0
  const seen = new Set()
  for (const entry of value.namespaces) {
    if (!Array.isArray(entry) || entry.length !== 2) throw new Error('namespace is invalid')
    const [namespace, values] = entry
    requireNamespace(namespace)
    if (seen.has(namespace) || !Array.isArray(values)) throw new Error('namespace is invalid')
    seen.add(namespace)
    const records = snapshot.namespaces.get(namespace)
    const recordKeys = new Set()
    for (const item of values) {
      if (!Array.isArray(item) || item.length !== 2) throw new Error('record is invalid')
      const [key, record] = item
      requireRecordKey(key)
      if (recordKeys.has(key)) throw new Error('record is duplicated')
      recordKeys.add(key)
      try {
        records.set(key, clonePersistable(record))
      } catch (error) {
        if (namespace !== 'oauth_state' || !isRecordCapacityError(error)) throw error
        snapshot[RECOVERED_RECORDS] += 1
      }
    }
  }
  if (!LEGACY_NAMESPACES.every((namespace) => seen.has(namespace))) {
    throw new Error('snapshot namespaces are incomplete')
  }
  return snapshot
}

function assertEnvelope(value, expectedKeyId) {
  if (
    !value ||
    typeof value !== 'object' ||
    value.version !== FORMAT_VERSION ||
    value.algorithm !== ALGORITHM ||
    value.key_id !== expectedKeyId ||
    typeof value.iv !== 'string' ||
    typeof value.ciphertext !== 'string' ||
    typeof value.tag !== 'string'
  ) {
    throw new Error('encrypted envelope is invalid')
  }
}

function aad(keyId) {
  return Buffer.from(`feed-passport:atproto-sidecar-store:v${FORMAT_VERSION}:${keyId}`, 'utf8')
}

function clonePersistable(value) {
  let encoded
  try {
    encoded = JSON.stringify(value)
  } catch (error) {
    throw new SidecarError(
      'sidecar_store_value_invalid',
      'The sidecar state contains a non-persistable value.',
      { status: 503, cause: error },
    )
  }
  if (encoded === undefined) {
    throw new SidecarError(
      'sidecar_store_value_invalid',
      'The sidecar state contains an invalid or oversized value.',
      { status: 503 },
    )
  }
  const encodedBytes = Buffer.byteLength(encoded)
  if (encodedBytes > MAX_RECORD_BYTES) {
    throw new SidecarError(
      'sidecar_store_capacity_exceeded',
      'An encrypted sidecar record exceeded its safe size limit.',
      { status: 503 },
    )
  }
  if (encodedBytes > 8_000_000) {
    throw new SidecarError(
      'sidecar_store_value_invalid',
      'The sidecar state contains an invalid or oversized value.',
      { status: 503 },
    )
  }
  return JSON.parse(encoded)
}

function cloneValue(value) {
  return value === undefined ? undefined : structuredClone(value)
}

function requireNamespace(value) {
  if (!NAMESPACES.includes(value)) throw new TypeError('encrypted store namespace is invalid')
}

function requireRecordKey(value) {
  if (typeof value !== 'string' || value.length === 0 || value.length > 4096) {
    throw new SidecarError('sidecar_store_key_invalid', 'The sidecar state key is invalid.', {
      status: 503,
    })
  }
}

function requireDatabase(value) {
  if (
    !value ||
    typeof value.get !== 'function' ||
    typeof value.getAndDeleteIf !== 'function' ||
    typeof value.mutate !== 'function'
  ) {
    throw new TypeError('encrypted sidecar database is required')
  }
}

function requireFiniteNumber(value, fieldName) {
  const number = Number(value)
  if (!Number.isFinite(number)) throw new TypeError(`${fieldName} must be finite`)
  return number
}

async function rejectSymlink(filePath) {
  try {
    const value = await lstat(filePath)
    if (value.isSymbolicLink() || !value.isFile()) {
      throw new SidecarError(
        'sidecar_store_path_invalid',
        'The encrypted sidecar store path must identify a regular file.',
        { status: 503 },
      )
    }
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error
  }
}

async function lockOwnerIsActive(lockPath) {
  let metadata
  try {
    metadata = await stat(lockPath)
    const raw = await readFile(lockPath, 'utf8')
    if (Buffer.byteLength(raw) > 4096) return true
    const value = JSON.parse(raw)
    if (!Number.isSafeInteger(value?.pid) || value.pid < 1) {
      return Date.now() - metadata.mtimeMs < 30_000
    }
    if (typeof value.process_identity === 'string') {
      const actualIdentity = await processIdentity(value.pid)
      if (actualIdentity !== undefined && actualIdentity !== value.process_identity) return false
      if (actualIdentity === value.process_identity) return true
    } else if (value.pid === process.pid) {
      const createdAt = Date.parse(value.created_at)
      if (Number.isFinite(createdAt) && createdAt < PROCESS_STARTED_AT_MS) return false
    }
    try {
      process.kill(value.pid, 0)
      return true
    } catch (error) {
      return error?.code === 'EPERM'
    }
  } catch (error) {
    if (error?.code === 'ENOENT') return false
    return !metadata || Date.now() - metadata.mtimeMs < 30_000
  }
}

async function processIdentity(pid) {
  if (process.platform === 'linux') {
    try {
      const [bootId, processStat] = await Promise.all([
        readFile('/proc/sys/kernel/random/boot_id', 'utf8'),
        readFile(`/proc/${pid}/stat`, 'utf8'),
      ])
      const commandEnd = processStat.lastIndexOf(')')
      if (commandEnd < 0) throw new Error()
      // Fields after the command begin at procfs field 3; field 22 is
      // therefore index 19 and uniquely identifies this PID incarnation.
      const fields = processStat.slice(commandEnd + 1).trim().split(/\s+/)
      const startTicks = fields[19]
      if (!/^[0-9]+$/.test(startTicks) || !bootId.trim()) throw new Error()
      return `linux:${bootId.trim()}:${startTicks}`
    } catch {
      // Fall through to the current-process identity or conservative PID lock.
    }
  }
  if (pid === process.pid) return `process:${PROCESS_STARTED_AT_MS}`
  return undefined
}

async function syncDirectory(directory) {
  let handle
  try {
    handle = await open(directory, 'r')
    await handle.sync()
  } catch {
    // Some Windows filesystems do not expose directory fsync.
  } finally {
    await handle?.close().catch(() => {})
  }
}

function storeUnavailable(cause) {
  return new SidecarError(
    'sidecar_store_unavailable',
    'The encrypted sidecar state could not be persisted safely.',
    { status: 503, cause },
  )
}

function publicLease(record) {
  return {
    leaseRef: record.leaseRef,
    connectionRef: record.connectionRef,
    did: record.did,
    expiresAt: record.expiresAt,
  }
}

function connectionStatus(record) {
  if (record?.status === undefined) return 'active'
  if (['active', 'revoking', 'revoked'].includes(record.status)) return record.status
  throw new SidecarError(
    'sidecar_store_record_invalid',
    'The encrypted sidecar store contains invalid connection lifecycle state.',
    { status: 503 },
  )
}

function removeReceipts(receipts, connectionRef) {
  for (const [digest, receipt] of receipts) {
    if (receipt.connectionRef === connectionRef) receipts.delete(digest)
  }
}

function isTimedOAuthState(record) {
  return (
    record &&
    typeof record === 'object' &&
    record.format === 'timed-v1' &&
    Number.isFinite(record.storedAt) &&
    Number.isFinite(record.expiresAt) &&
    record.expiresAt >= record.storedAt &&
    Object.hasOwn(record, 'value')
  )
}

function isLegacyTimedOAuthState(record) {
  return (
    record &&
    typeof record === 'object' &&
    record.format === 'timed-v1' &&
    Number.isFinite(record.storedAt) &&
    !Number.isFinite(record.expiresAt) &&
    Object.hasOwn(record, 'value')
  )
}

function oauthStateExpiresAt(record, fallbackTtlMs) {
  if (isTimedOAuthState(record)) return record.expiresAt
  if (isLegacyTimedOAuthState(record)) return record.storedAt + fallbackTtlMs
  return undefined
}

function migrateLegacyOAuthStates(snapshot, now) {
  let changes = 0
  const migratedKeys = new Set()
  const records = snapshot.namespaces.get('oauth_state')
  for (const [key, record] of [...records].sort(([left], [right]) => left.localeCompare(right))) {
    if (isTimedOAuthState(record)) continue
    const transitional = isLegacyTimedOAuthState(record)
    const storedAt = transitional ? record.storedAt : now
    try {
      records.set(
        key,
        clonePersistable({
          format: 'timed-v1',
          storedAt,
          expiresAt: storedAt + OAUTH_STATE_TTL_MS,
          value: cloneValue(transitional ? record.value : record),
        }),
      )
      migratedKeys.add(key)
    } catch (error) {
      if (!isRecordCapacityError(error)) throw error
      records.delete(key)
    }
    changes += 1
  }
  return { changes, keys: migratedKeys }
}

function boundOAuthStateSnapshot(snapshot, migratedKeys, willPersist) {
  const records = snapshot.namespaces.get('oauth_state')
  const dropOrder = [...records.keys()].sort((left, right) => {
    const migrationOrder = Number(!migratedKeys.has(left)) - Number(!migratedKeys.has(right))
    if (migrationOrder !== 0) return migrationOrder
    const timeOrder = records.get(left).storedAt - records.get(right).storedAt
    return timeOrder === 0 ? left.localeCompare(right) : timeOrder
  })
  let removed = 0
  for (const key of dropOrder) {
    if (records.size <= MAX_OFFICIAL_OAUTH_STATES) break
    records.delete(key)
    removed += 1
  }

  if (removed > 0) willPersist = true
  if (!willPersist) return removed

  let serializedBytes = serializedSnapshotBytes(snapshot, 1)
  for (const key of dropOrder) {
    if (serializedBytes <= MAX_PLAINTEXT_BYTES) break
    const record = records.get(key)
    if (record === undefined) continue
    const entryBytes = Buffer.byteLength(JSON.stringify([key, record]))
    const commaBytes = records.size > 1 ? 1 : 0
    records.delete(key)
    serializedBytes -= entryBytes + commaBytes
    removed += 1
  }
  if (serializedBytes > MAX_PLAINTEXT_BYTES) {
    throw new SidecarError(
      'sidecar_store_capacity_exceeded',
      'The encrypted sidecar store reached its safe size limit.',
      { status: 503 },
    )
  }
  return removed
}

function serializedSnapshotBytes(snapshot, revisionDelta = 0) {
  const serialized = serializeSnapshot(snapshot)
  serialized.revision += revisionDelta
  return Buffer.byteLength(JSON.stringify(serialized))
}

function isRecordCapacityError(error) {
  return error instanceof SidecarError && error.code === 'sidecar_store_capacity_exceeded'
}

function pruneExpired(snapshot, now, { includeLeases = false } = {}) {
  let removed = 0
  for (const [key, record] of snapshot.namespaces.get('oauth_state')) {
    if (isTimedOAuthState(record) && record.expiresAt <= now) {
      snapshot.namespaces.get('oauth_state').delete(key)
      removed += 1
    }
  }
  for (const namespace of ['owner_state', 'oauth_receipt']) {
    for (const [key, record] of snapshot.namespaces.get(namespace)) {
      if (Number.isFinite(record?.expiresAt) && record.expiresAt <= now) {
        snapshot.namespaces.get(namespace).delete(key)
        removed += 1
      }
    }
  }
  for (const [key, record] of snapshot.namespaces.get('owner_connection')) {
    if (
      connectionStatus(record) === 'revoked' &&
      Number.isFinite(record.revokedAt) &&
      record.revokedAt + CONNECTION_TOMBSTONE_TTL_MS <= now
    ) {
      snapshot.namespaces.get('owner_connection').delete(key)
      removed += 1
    }
  }
  if (includeLeases) {
    for (const [key, record] of snapshot.namespaces.get('session_lease')) {
      if (Number.isFinite(record?.expiresAt) && record.expiresAt <= now) {
        snapshot.namespaces.get('session_lease').delete(key)
        removed += 1
      }
    }
  }
  return removed
}

function recordCount(snapshot) {
  return [...snapshot.namespaces.values()].reduce((total, records) => total + records.size, 0)
}
