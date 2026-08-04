import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  createRegisterToken,
  listAgentWorkers,
  listRegisterTokens,
  revokeAgentWorker,
  revokeRegisterToken,
} from './workerTokens'

const originalFetch = global.fetch

afterEach(() => {
  global.fetch = originalFetch
  vi.restoreAllMocks()
})

function mockFetchJson(response: unknown) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve(response),
    text: () => Promise.resolve(JSON.stringify(response)),
  } as Response)
}

describe('worker tokens api', () => {
  it('lists register tokens without a management header', async () => {
    const fetchMock = mockFetchJson({ tokens: [] })
    global.fetch = fetchMock

    const tokens = await listRegisterTokens()

    expect(tokens).toEqual([])
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent-register-tokens',
      expect.objectContaining({ cache: 'no-store' })
    )
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.headers).not.toHaveProperty('X-Agent-Worker-Register-Token')
  })

  it('creates a register token with label and optional workspace scope', async () => {
    const fetchMock = mockFetchJson({
      token_id: 't1',
      register_token: 'plain',
      workspace_id: 'video_knowledge',
      label: 'home-mac-mini',
    })
    global.fetch = fetchMock

    const created = await createRegisterToken({
      label: 'home-mac-mini',
      workspace_id: 'video_knowledge',
    })

    expect(created.register_token).toBe('plain')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent-register-tokens',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          label: 'home-mac-mini',
          workspace_id: 'video_knowledge',
        }),
      })
    )
  })

  it('revokes a register token', async () => {
    const fetchMock = mockFetchJson({ revoked: true })
    global.fetch = fetchMock

    await revokeRegisterToken('token/1')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent-register-tokens/token%2F1/revoke',
      expect.objectContaining({ method: 'POST' })
    )
  })

  it('lists agent workers', async () => {
    const fetchMock = mockFetchJson({ workers: [{ worker_id: 'w1' }] })
    global.fetch = fetchMock

    const workers = await listAgentWorkers()

    expect(workers).toEqual([{ worker_id: 'w1' }])
  })

  it('revokes an agent worker', async () => {
    const fetchMock = mockFetchJson({ worker_id: 'w1', revoked: true })
    global.fetch = fetchMock

    await revokeAgentWorker('w1')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent-workers/w1/revoke',
      expect.objectContaining({ method: 'POST' })
    )
  })
})
