import { timingSafeEqual } from 'node:crypto'

import { SidecarError } from './errors.mjs'

const OWNER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$/
const DID_PATTERN = /^did:[a-z0-9]+:[A-Za-z0-9._:%-]{1,300}$/
const HANDLE_LABEL_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/

export function requireOwnerId(value) {
  if (typeof value !== 'string' || !OWNER_PATTERN.test(value)) {
    throw new SidecarError('owner_invalid', 'The authenticated owner is invalid.', {
      status: 401,
    })
  }
  return value
}

export function requireDid(value) {
  if (typeof value !== 'string' || !DID_PATTERN.test(value)) {
    throw new SidecarError(
      'atproto_subject_invalid',
      'The AT Protocol server returned an invalid account identifier.',
      { status: 502 },
    )
  }
  return value
}

export function requireHandle(value) {
  if (typeof value !== 'string') {
    throw new SidecarError('handle_invalid', 'An AT Protocol handle is required.')
  }
  const normalized = value.trim().replace(/^@/, '').toLowerCase()
  if (
    normalized.length > 253 ||
    normalized.length < 3 ||
    !normalized.includes('.') ||
    !normalized.split('.').every((label) => HANDLE_LABEL_PATTERN.test(label))
  ) {
    throw new SidecarError('handle_invalid', 'Enter a valid AT Protocol handle.')
  }
  return normalized
}

export function requireOpaqueReference(value, field = 'reference') {
  if (
    typeof value !== 'string' ||
    value.length < 32 ||
    value.length > 256 ||
    !/^[A-Za-z0-9_-]+$/.test(value)
  ) {
    throw new SidecarError(
      `${field}_invalid`,
      `The ${field.replaceAll('_', ' ')} is invalid.`,
    )
  }
  return value
}

export function requireSingleParam(params, name, { optional = false } = {}) {
  const values = params.getAll(name)
  if (values.length === 0 && optional) return undefined
  if (values.length !== 1 || values[0].length === 0 || values[0].length > 4096) {
    throw new SidecarError(
      'oauth_callback_invalid',
      'The OAuth callback parameters are invalid.',
    )
  }
  return values[0]
}

export function equalOpaque(left, right) {
  if (typeof left !== 'string' || typeof right !== 'string') return false
  const leftBytes = Buffer.from(left)
  const rightBytes = Buffer.from(right)
  if (leftBytes.length !== rightBytes.length) return false
  return timingSafeEqual(leftBytes, rightBytes)
}

export function assertPublicJwks(value) {
  if (!value || typeof value !== 'object' || !Array.isArray(value.keys)) {
    throw new SidecarError(
      'jwks_unavailable',
      'The public AT Protocol key set is unavailable.',
      { status: 503 },
    )
  }
  const privateNames = new Set(['d', 'p', 'q', 'dp', 'dq', 'qi', 'oth', 'k'])
  const publicKeys = []
  for (const key of value.keys) {
    if (!key || typeof key !== 'object') {
      throw new SidecarError('jwks_unavailable', 'The public key set is invalid.', {
        status: 503,
      })
    }
    for (const name of privateNames) {
      if (Object.hasOwn(key, name) && key[name] !== undefined) {
        throw new SidecarError(
          'jwks_private_material',
          'The key set contains private key material and cannot be served.',
          { status: 503 },
        )
      }
    }
    const publicKey = {}
    for (const [name, member] of Object.entries(key)) {
      if (!privateNames.has(name)) publicKey[name] = member
    }
    publicKeys.push(structuredClone(publicKey))
  }
  return { keys: publicKeys }
}

export function sanitizeOperationResult(
  operation,
  value,
  { ownerDid, forbiddenScalars = new Set() } = {},
) {
  const subject = requireDid(ownerDid)
  const result = requireObject(value)
  let output
  switch (operation) {
    case 'graph.get_follows': {
      requireOnlyKeys(result, ['cursor', 'follows', 'subject'])
      if (!Array.isArray(result.follows) || result.follows.length > 100) invalidResponse()
      output = {
        follows: result.follows.map((profile) => projectFollowProfile(profile, subject)),
        ...(result.cursor === undefined ? {} : { cursor: boundedString(result.cursor, 2048) }),
      }
      break
    }
    case 'graph.get_mutes':
      requireOnlyKeys(result, ['cursor', 'mutes'])
      if (!Array.isArray(result.mutes) || result.mutes.length > 100) invalidResponse()
      output = {
        mutes: result.mutes.map((profile) => ({ did: requireDid(requireObject(profile).did) })),
        ...(result.cursor === undefined ? {} : { cursor: boundedString(result.cursor, 2048) }),
      }
      break
    case 'graph.follow': {
      requireOnlyKeys(result, ['cid', 'uri'])
      const uri = ownedRecordUri(result.uri, subject, 'app.bsky.graph.follow')
      output = { uri, cid: boundedString(result.cid, 256) }
      break
    }
    case 'graph.delete_follow':
      requireOnlyKeys(result, ['deleted', 'uri'])
      if (result.deleted !== true) invalidResponse()
      output = {
        deleted: true,
        uri: ownedRecordUri(result.uri, subject, 'app.bsky.graph.follow'),
      }
      break
    case 'graph.mute':
    case 'graph.unmute':
      requireOnlyKeys(result, ['actor', 'muted'])
      if (result.muted !== (operation === 'graph.mute')) invalidResponse()
      output = { actor: requireDid(result.actor), muted: result.muted }
      break
    case 'actor.get_preferences':
      requireOnlyKeys(result, ['observed_sha256', 'preferences'])
      if (!/^[a-f0-9]{64}$/.test(result.observed_sha256 ?? '')) invalidResponse()
      output = {
        preferences: sanitizePreferences(result.preferences),
        observed_sha256: result.observed_sha256,
      }
      break
    case 'actor.put_preferences':
      requireOnlyKeys(result, ['external_subject', 'preferences_sha256', 'updated'])
      if (
        result.updated !== true ||
        requireDid(result.external_subject) !== subject ||
        !/^[a-f0-9]{64}$/.test(result.preferences_sha256 ?? '')
      ) {
        invalidResponse()
      }
      output = {
        updated: true,
        external_subject: subject,
        preferences_sha256: result.preferences_sha256,
      }
      break
    default:
      throw new SidecarError('bridge_operation_denied', 'The operation is not allowed.', {
        status: 403,
      })
  }
  if (Buffer.byteLength(JSON.stringify(output)) > 1_000_000) {
    throw new SidecarError(
      'bridge_response_too_large',
      'The AT Protocol response exceeded the sidecar limit.',
      { status: 502 },
    )
  }
  rejectCredentialEcho(output, forbiddenScalars)
  return output
}

export function sanitizePreferences(value) {
  if (!Array.isArray(value) || value.length > 200) invalidPreferences()
  const mutedWords = value.filter(
    (item) =>
      item &&
      typeof item === 'object' &&
      !Array.isArray(item) &&
      item.$type === 'app.bsky.actor.defs#mutedWordsPref',
  )
  if (mutedWords.length > 1) invalidPreferences()
  return mutedWords.map(sanitizeMutedWordsPreference)
}

function sanitizeMutedWordsPreference(value) {
  const preference = requireObject(value, invalidPreferences)
  if (Object.keys(preference).some((key) => !['$type', 'items'].includes(key))) {
    invalidPreferences()
  }
  if (
    preference.$type !== 'app.bsky.actor.defs#mutedWordsPref' ||
    !Array.isArray(preference.items) ||
    preference.items.length > 500
  ) {
    invalidPreferences()
  }
  return {
    $type: preference.$type,
    items: preference.items.map((value) => {
      const item = requireObject(value, invalidPreferences)
      const allowed = ['$type', 'actorTarget', 'createdAt', 'expiresAt', 'id', 'targets', 'value']
      if (Object.keys(item).some((key) => !allowed.includes(key))) invalidPreferences()
      if (!Array.isArray(item.targets) || item.targets.length > 16) invalidPreferences()
      const output = {
        id: boundedPreferenceString(item.id, 256),
        value: boundedPreferenceString(item.value, 4096),
        targets: item.targets.map((target) => boundedPreferenceString(target, 128)),
      }
      if (item.$type !== undefined) {
        if (item.$type !== 'app.bsky.actor.defs#mutedWord') invalidPreferences()
        output.$type = item.$type
      }
      for (const key of ['actorTarget', 'createdAt', 'expiresAt']) {
        if (item[key] !== undefined) output[key] = boundedPreferenceString(item[key], 256)
      }
      return output
    }),
  }
}

function projectFollowProfile(value, ownerDid) {
  const profile = requireObject(value)
  const did = requireDid(profile.did)
  const output = { did }
  if (profile.viewer !== undefined) {
    const viewer = requireObject(profile.viewer)
    if (viewer.following !== undefined) {
      output.viewer = {
        following: ownedRecordUri(viewer.following, ownerDid, 'app.bsky.graph.follow'),
      }
    }
  }
  return output
}

function rejectCredentialEcho(value, forbiddenScalars) {
  if (!(forbiddenScalars instanceof Set) || forbiddenScalars.size === 0) return
  const visit = (item) => {
    if (typeof item === 'string' && forbiddenScalars.has(item)) {
      throw new SidecarError(
        'bridge_secret_detected',
        'The AT Protocol response echoed credential material and was blocked.',
        { status: 502 },
      )
    }
    if (Array.isArray(item)) {
      for (const child of item) visit(child)
    } else if (item && typeof item === 'object') {
      for (const child of Object.values(item)) visit(child)
    }
  }
  visit(value)
}

function requireObject(value, onInvalid = invalidResponse) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) onInvalid()
  return value
}

function requireOnlyKeys(value, allowed) {
  const keys = Object.keys(value)
  if (keys.some((key) => !allowed.includes(key))) invalidResponse()
}

function boundedString(value, maxLength) {
  if (
    typeof value !== 'string' ||
    value.length === 0 ||
    value.length > maxLength ||
    /[\0\r\n]/.test(value)
  ) {
    invalidResponse()
  }
  return value
}

function boundedPreferenceString(value, maxLength) {
  if (
    typeof value !== 'string' ||
    value.length === 0 ||
    value.length > maxLength ||
    /[\0\r\n]/.test(value)
  ) {
    invalidPreferences()
  }
  return value
}

function ownedRecordUri(value, ownerDid, collection) {
  const uri = boundedString(value, 1024)
  if (!uri.startsWith(`at://${ownerDid}/${collection}/`)) invalidResponse()
  return uri
}

function invalidPreferences() {
  throw new SidecarError(
    'preferences_response_invalid',
    'The AT Protocol server returned invalid preferences.',
    { status: 502 },
  )
}

function invalidResponse() {
  throw new SidecarError('bridge_response_invalid', 'The AT Protocol response is invalid.', {
    status: 502,
  })
}
