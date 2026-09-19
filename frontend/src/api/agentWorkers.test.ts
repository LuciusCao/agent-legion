import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  deleteAgentWorker,
  fetchAgentWorkers,
  listAgentWorkers,
} from './agentWorkers'

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

  it('fetches the full response including the console url', async () => {
    const fetchMock = mockFetchJson({
      workers: [],
      console_url: 'http://127.0.0.1:8789',
    })
    global.fetch = fetchMock

    const data = await fetchAgentWorkers('ws 1')

    expect(data.console_url).toBe('http://127.0.0.1:8789')
    expect(data.workers).toEqual([])
    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/agent-workers?workspace_id=ws%201'
    )
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
