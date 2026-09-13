import { createHash } from 'node:crypto'

import { SidecarError } from './errors.mjs'
import { sanitizePreferences } from './validation.mjs'

export async function createOfficialAgentExecutor({ AgentClass } = {}) {
  let Agent = AgentClass
  if (!Agent) {
    try {
      ;({ Agent } = await import('@atproto/api'))
    } catch (error) {
      throw new SidecarError(
        'sidecar_dependencies_unavailable',
        'The pinned official AT Protocol API package is not installed.',
        { status: 503, cause: error },
      )
    }
  }

  return {
    async execute({ session, did, operation, input }) {
      const agent = new Agent(session)
      switch (operation) {
        case 'graph.get_follows':
          return responseData(await agent.app.bsky.graph.getFollows(input))
        case 'graph.get_mutes': {
          const { actor: _actor, ...pagination } = input
          return responseData(await agent.app.bsky.graph.getMutes(pagination))
        }
        case 'graph.follow': {
          const result = responseData(await agent.follow(input.actor))
          return { uri: result?.uri, cid: result?.cid }
        }
        case 'graph.delete_follow':
          await agent.deleteFollow(input.uri)
          return { deleted: true, uri: input.uri }
        case 'graph.mute':
          await agent.mute(input.actor)
          return { muted: true, actor: input.actor }
        case 'graph.unmute':
          await agent.unmute(input.actor)
          return { muted: false, actor: input.actor }
        case 'actor.get_preferences': {
          const preferences = responseData(await agent.app.bsky.actor.getPreferences({}))
          const allPreferences = boundedRawPreferences(preferences.preferences)
          const safePreferences = sanitizePreferences(preferences.preferences)
          return {
            preferences: safePreferences,
            observed_sha256: digestPreferences(allPreferences),
          }
        }
        case 'actor.put_preferences': {
          const current = responseData(await agent.app.bsky.actor.getPreferences({}))
          const allPreferences = boundedRawPreferences(current.preferences)
          const observed = digestPreferences(allPreferences)
          if (observed !== input.expected_sha256) {
            throw new SidecarError(
              'preferences_changed',
              'AT Protocol preferences changed after observation; observe them again.',
              { status: 409 },
            )
          }
          const mergedPreferences = mergeMutedWordsPreferences(
            allPreferences,
            sanitizePreferences(input.preferences),
          )
          await agent.app.bsky.actor.putPreferences({ preferences: mergedPreferences })
          return {
            updated: true,
            external_subject: did,
            preferences_sha256: digestPreferences(mergedPreferences),
          }
        }
        default:
          throw new SidecarError(
            'bridge_operation_denied',
            'The requested AT Protocol operation is not implemented.',
            { status: 403 },
          )
      }
    },
  }
}

function responseData(response) {
  return response && typeof response === 'object' && 'data' in response
    ? response.data
    : response
}

function digestPreferences(preferences) {
  boundedRawPreferences(preferences)
  return createHash('sha256').update(stableJson(preferences)).digest('hex')
}

function boundedRawPreferences(preferences) {
  if (
    !Array.isArray(preferences) ||
    preferences.length > 200 ||
    preferences.some((item) => !item || typeof item !== 'object' || Array.isArray(item))
  ) {
    throw new SidecarError(
      'preferences_response_invalid',
      'The AT Protocol server returned invalid preferences.',
      { status: 502 },
    )
  }
  let encoded
  try {
    encoded = stableJson(preferences)
  } catch {
    encoded = ''
  }
  if (!encoded || Buffer.byteLength(encoded) > 1_000_000) {
    throw new SidecarError(
      'preferences_response_invalid',
      'The AT Protocol server returned invalid preferences.',
      { status: 502 },
    )
  }
  return structuredClone(preferences)
}

function mergeMutedWordsPreferences(current, replacement) {
  const type = 'app.bsky.actor.defs#mutedWordsPref'
  const index = current.findIndex((item) => item.$type === type)
  const output = current.filter((item) => item.$type !== type)
  if (replacement.length === 1) {
    output.splice(index < 0 ? output.length : Math.min(index, output.length), 0, replacement[0])
  }
  return output
}

function stableJson(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`
  return `{${Object.keys(value)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`)
    .join(',')}}`
}
