import { createNodeRequestListener, createSidecarHandler } from './http.mjs'
import { createOfficialOAuthClient } from './official-client.mjs'
import { createOfficialAgentExecutor } from './official-executor.mjs'
import { AtprotoOAuthService } from './service.mjs'

export async function createAtprotoSidecarRuntime({
  client,
  privateKeys,
  oauthStateStore,
  oauthSessionStore,
  requestLock,
  ownerStates,
  connections,
  sessionLeases,
  authenticate,
  appCallbackUri,
  fetch,
  origin,
  clock,
}) {
  const oauthClient = await createOfficialOAuthClient({
    client,
    privateKeys,
    stateStore: oauthStateStore,
    sessionStore: oauthSessionStore,
    requestLock,
    ownerStates,
    fetch,
  })
  if (typeof sessionLeases.setSessionRestorer === 'function') {
    sessionLeases.setSessionRestorer((did) => oauthClient.restore(did))
  }
  const operationExecutor = await createOfficialAgentExecutor()
  const service = new AtprotoOAuthService({
    oauthClient,
    ownerStates,
    connections,
    sessionLeases,
    operationExecutor,
    ...(clock ? { now: clock } : {}),
  })
  const fetchHandler = createSidecarHandler({ service, authenticate, appCallbackUri })
  return Object.freeze({
    service,
    fetchHandler,
    nodeRequestListener: createNodeRequestListener(fetchHandler, { origin }),
  })
}
