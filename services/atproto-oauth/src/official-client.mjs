import { SidecarError } from './errors.mjs'
import {
  requireDid,
  requireOAuthProtocolState,
  requireOpaqueReference,
} from './validation.mjs'

export async function createOfficialOAuthClient({
  client,
  stateStore,
  sessionStore,
  requestLock,
  ownerStates,
  privateKeys,
  fetch,
}) {
  requireStore(stateStore, 'stateStore')
  requireStore(sessionStore, 'sessionStore')
  if (typeof requestLock !== 'function') {
    throw new SidecarError(
      'sidecar_lock_unconfigured',
      'A session refresh lock is required for the AT Protocol OAuth client.',
      { status: 503 },
    )
  }
  requireOwnerStateStore(ownerStates)
  const clientMetadata = buildClientMetadata(client)
  if (!Array.isArray(privateKeys) || privateKeys.length === 0) {
    throw new SidecarError(
      'sidecar_keyset_unconfigured',
      'At least one externally supplied OAuth client private key is required.',
      { status: 503 },
    )
  }

  let NodeOAuthClient
  let JoseKey
  try {
    ;({ NodeOAuthClient } = await import('@atproto/oauth-client-node'))
    ;({ JoseKey } = await import('@atproto/jwk-jose'))
  } catch (error) {
    throw new SidecarError(
      'sidecar_dependencies_unavailable',
      'The pinned official AT Protocol OAuth packages are not installed.',
      { status: 503, cause: error },
    )
  }

  const keyset = await Promise.all(
    privateKeys.map(async (key, index) => {
      if (
        !key ||
        typeof key.importable !== 'string' ||
        key.importable.length < 32 ||
        typeof key.kid !== 'string' ||
        !/^[A-Za-z0-9._-]{1,128}$/.test(key.kid)
      ) {
        throw new SidecarError(
          'sidecar_keyset_invalid',
          `AT Protocol OAuth client key ${index + 1} is invalid.`,
          { status: 503 },
        )
      }
      return JoseKey.fromImportable(key.importable, key.kid)
    }),
  )

  const ownerBoundStateStore = {
    async set(protocolState, value) {
      const protocol = requireOAuthProtocolState(protocolState)
      const appState = requireOpaqueReference(value?.appState, 'oauth_app_state')
      if (typeof ownerStates.bindOfficialState === 'function') {
        await ownerStates.bindOfficialState(appState, protocol, value)
        return
      }
      await stateStore.set(protocol, value)
      try {
        await ownerStates.bindProtocolState(appState, protocol)
      } catch (error) {
        await stateStore.del(protocol).catch(() => {})
        throw error
      }
    },
    get: (protocolState) => stateStore.get(protocolState),
    del: (protocolState) => stateStore.del(protocolState),
  }

  const oauthClient = new NodeOAuthClient({
    clientMetadata,
    keyset,
    stateStore: ownerBoundStateStore,
    sessionStore,
    requestLock,
    ...(fetch ? { fetch } : {}),
  })
  Object.defineProperties(oauthClient, {
    revokeWithProof: {
      value: async (did, onProviderConfirmed) => {
        const subject = requireDid(did)
        if (typeof onProviderConfirmed !== 'function') {
          throw new TypeError('provider revocation confirmation callback is required')
        }
        const session = await oauthClient.restore(subject, false)
        const tokenSet = await session.getTokenSet(false)
        const accessToken = tokenSet?.access_token
        if (typeof accessToken !== 'string' || accessToken.length === 0) {
          throw new SidecarError(
            'session_revoke_unavailable',
            'The stored AT Protocol session cannot prove provider revocation.',
            { status: 409 },
          )
        }
        if (!session.server || typeof session.server.request !== 'function') {
          throw new SidecarError(
            'sidecar_dependencies_incompatible',
            'The pinned official OAuth client cannot perform confirmed revocation.',
            { status: 503 },
          )
        }
        // OAuthServerAgent.revoke intentionally swallows endpoint failures. The
        // pinned request primitive is used inside this credential boundary so
        // only a successful provider response can create durable proof.
        await session.server.request('revocation', { token: accessToken })
        await onProviderConfirmed()
        await sessionStore.del(subject)
      },
    },
    discardSession: {
      value: async (did) => sessionStore.del(requireDid(did)),
    },
  })
  return oauthClient
}

export function buildClientMetadata(client) {
  if (!client || typeof client !== 'object') {
    throw new SidecarError(
      'sidecar_client_unconfigured',
      'AT Protocol OAuth client metadata is required.',
      { status: 503 },
    )
  }
  const clientId = requireHttpsUrl(client.clientId, 'clientId')
  const clientUri = requireHttpsUrl(client.clientUri, 'clientUri')
  const jwksUri = requireHttpsUrl(client.jwksUri, 'jwksUri')
  const redirectUri = requireHttpsUrl(client.redirectUri, 'redirectUri')
  const metadataUri = requireHttpsUrl(client.metadataUri, 'metadataUri')
  if (clientId !== metadataUri) {
    throw new SidecarError(
      'sidecar_client_id_mismatch',
      'The AT Protocol client_id must exactly match its public metadata URI.',
      { status: 503 },
    )
  }
  if (typeof client.name !== 'string' || client.name.trim().length < 2 || client.name.length > 80) {
    throw new SidecarError(
      'sidecar_client_name_invalid',
      'The AT Protocol client name is invalid.',
      { status: 503 },
    )
  }
  if (!['ES256', 'RS256'].includes(client.signingAlgorithm)) {
    throw new SidecarError(
      'sidecar_signing_algorithm_invalid',
      'The OAuth client signing algorithm must be ES256 or RS256.',
      { status: 503 },
    )
  }
  const scope = client.scope ?? 'atproto transition:generic'
  const scopeTokens = typeof scope === 'string' ? scope.trim().split(/\s+/).filter(Boolean) : []
  if (
    scopeTokens.length !== 2 ||
    new Set(scopeTokens).size !== 2 ||
    !scopeTokens.includes('atproto') ||
    !scopeTokens.includes('transition:generic')
  ) {
    throw new SidecarError(
      'sidecar_scope_invalid',
      'The AT Protocol OAuth scope must be exactly atproto transition:generic.',
      { status: 503 },
    )
  }
  return {
    client_id: clientId,
    client_name: client.name.trim(),
    client_uri: clientUri,
    redirect_uris: [redirectUri],
    grant_types: ['authorization_code', 'refresh_token'],
    scope: 'atproto transition:generic',
    response_types: ['code'],
    application_type: 'web',
    token_endpoint_auth_method: 'private_key_jwt',
    token_endpoint_auth_signing_alg: client.signingAlgorithm,
    dpop_bound_access_tokens: true,
    jwks_uri: jwksUri,
    ...(client.policyUri ? { policy_uri: requireHttpsUrl(client.policyUri, 'policyUri') } : {}),
    ...(client.termsUri ? { tos_uri: requireHttpsUrl(client.termsUri, 'termsUri') } : {}),
  }
}

function requireStore(value, name) {
  if (
    !value ||
    typeof value.set !== 'function' ||
    typeof value.get !== 'function' ||
    typeof value.del !== 'function'
  ) {
    throw new SidecarError(
      'sidecar_storage_unconfigured',
      `An injected ${name} with set/get/del is required.`,
      { status: 503 },
    )
  }
}

function requireOwnerStateStore(value) {
  if (
    !value ||
    typeof value.bound !== 'function' ||
    typeof value.deleteByAppState !== 'function' ||
    (typeof value.bindOfficialState !== 'function' &&
      typeof value.bindProtocolState !== 'function')
  ) {
    throw new SidecarError(
      'sidecar_storage_unconfigured',
      'An owner-bound OAuth transaction store is required.',
      { status: 503 },
    )
  }
}

function requireHttpsUrl(value, name) {
  try {
    const url = new URL(value)
    if (url.protocol !== 'https:' || url.username || url.password || url.hash) throw new Error()
    return url.toString()
  } catch {
    throw new SidecarError(
      'sidecar_client_url_invalid',
      `${name} must be an absolute HTTPS URL without credentials or a fragment.`,
      { status: 503 },
    )
  }
}
