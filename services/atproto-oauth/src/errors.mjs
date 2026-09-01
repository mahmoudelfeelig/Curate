export class SidecarError extends Error {
  constructor(code, detail, { status = 400, cause } = {}) {
    super(detail, { cause })
    this.name = 'SidecarError'
    this.code = code
    this.status = status
  }
}

export function asPublicError(error) {
  if (error instanceof SidecarError) {
    return {
      status: error.status,
      body: { error: error.code, detail: error.message },
    }
  }

  return {
    status: 500,
    body: {
      error: 'atproto_sidecar_error',
      detail: 'The AT Protocol credential sidecar could not complete the request.',
    },
  }
}
