import { afterEach, describe, expect, it, vi } from 'vitest'

import { deleteAgentWorker, listAgentWorkers } from './agentWorkers'

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

describe('agent workers api', () => {
  it('lists agent workers', async () => {
    const fetchMock = mockFetchJson({ workers: [{ worker_id: 'w1' }] })
    global.fetch = fetchMock

    const workers = await listAgentWorkers()

    expect(workers).toEqual([{ worker_id: 'w1' }])
  })

  it('deletes an agent worker', async () => {
    const fetchMock = mockFetchJson({ worker_id: 'w1', deleted: true })
    global.fetch = fetchMock

    await deleteAgentWorker('w/1')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent-workers/w%2F1',
      expect.objectContaining({ method: 'DELETE' })
    )
  })
})
