import assert from 'node:assert/strict'
import { generateKeyPairSync } from 'node:crypto'
import test from 'node:test'

import { createOfficialOAuthClient } from '../src/official-client.mjs'
import { assertPublicJwks } from '../src/validation.mjs'

function memoryStore() {
  const values = new Map()
  return {
    async set(key, value) {
      values.set(key, value)
    },
    async get(key) {
      return values.get(key)
    },
    async del(key) {
      values.delete(key)
    },
  }
}

test('pinned official packages construct the confidential DPoP client without network access', async () => {
  const { privateKey } = generateKeyPairSync('ec', {
    namedCurve: 'P-256',
    privateKeyEncoding: { type: 'pkcs8', format: 'pem' },
    publicKeyEncoding: { type: 'spki', format: 'pem' },
  })
  let lockCalls = 0
  const requestLock = async (_name, work) => {
    lockCalls += 1
    return work()
  }
  const client = await createOfficialOAuthClient({
    client: {
      clientId: 'https://passport.example/oauth/atproto/client-metadata.json',
      metadataUri: 'https://passport.example/oauth/atproto/client-metadata.json',
      clientUri: 'https://passport.example/',
      jwksUri: 'https://passport.example/oauth/atproto/jwks.json',
      redirectUri: 'https://passport.example/oauth/atproto/callback',
      name: 'Feed Passport',
      signingAlgorithm: 'ES256',
      scope: 'atproto transition:generic',
    },
    stateStore: memoryStore(),
    sessionStore: memoryStore(),
    requestLock,
    privateKeys: [{ kid: 'ephemeral-test-key', importable: privateKey }],
    fetch: async () => {
      throw new Error('client construction must not make a network request')
    },
  })

  assert.equal(client.clientMetadata.client_id, 'https://passport.example/oauth/atproto/client-metadata.json')
  assert.equal(client.clientMetadata.token_endpoint_auth_method, 'private_key_jwt')
  assert.equal(client.clientMetadata.token_endpoint_auth_signing_alg, 'ES256')
  const publicJwks = assertPublicJwks(client.jwks)
  assert.equal(publicJwks.keys.length, 1)
  assert.equal(publicJwks.keys[0].kid, 'ephemeral-test-key')
  assert.equal('d' in publicJwks.keys[0], false)
  assert.equal(typeof client.authorize, 'function')
  assert.equal(typeof client.callback, 'function')
  assert.equal(typeof client.restore, 'function')
  assert.equal(lockCalls, 0)
})
