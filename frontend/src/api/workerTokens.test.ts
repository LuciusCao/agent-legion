import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  createRegisterToken,
  deleteRegisterToken,
  listRegisterTokens,
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
      workspace_id: 'demo_video_workflow',
      label: 'home-mac-mini',
    })
    global.fetch = fetchMock

    const created = await createRegisterToken({
      label: 'home-mac-mini',
      workspace_id: 'demo_video_workflow',
    })

    expect(created.register_token).toBe('plain')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent-register-tokens',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          label: 'home-mac-mini',
          workspace_id: 'demo_video_workflow',
        }),
      })
    )
  })

  it('deletes a register token', async () => {
    const fetchMock = mockFetchJson({ token_id: 't1', deleted: true })
    global.fetch = fetchMock

    await deleteRegisterToken('token/1')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent-register-tokens/token%2F1',
      expect.objectContaining({ method: 'DELETE' })
    )
  })
})
