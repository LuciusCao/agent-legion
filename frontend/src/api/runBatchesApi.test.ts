import { afterEach, describe, expect, it, vi } from 'vitest'

import { createRunAsync } from './runBatchesApi'

afterEach(() => {
  vi.restoreAllMocks()
})

function mockFetchJson(response: unknown) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 202,
    json: () => Promise.resolve(response),
    text: () => Promise.resolve(JSON.stringify(response)),
  } as Response)
}

describe('runBatchesApi（#1157 phase3）', () => {
  it('posts mode=async for the async accept path', async () => {
    const payload = {
      run: { id: 'r1' },
      batch: { run_id: 'r1', status: 'pending' },
    }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await createRunAsync('ws1', {
      items: [{ type: 'ref', connection_key: 'cms', external_id: 'q1' }],
    })

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/runs',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          items: [{ type: 'ref', connection_key: 'cms', external_id: 'q1' }],
          mode: 'async',
        }),
      })
    )
  })
})
