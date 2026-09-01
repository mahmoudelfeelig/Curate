import { SidecarError } from './errors.mjs'

export async function createOfficialOAuthClient({
  client,
  stateStore,
  sessionStore,
  requestLock,
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

  return new NodeOAuthClient({
    clientMetadata,
    keyset,
    stateStore,
    sessionStore,
    requestLock,
    ...(fetch ? { fetch } : {}),
  })
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
