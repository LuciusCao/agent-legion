import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchFailedNodeRuns, rerunJobsByFailure } from './failureApi'

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

describe('failure api', () => {
  it('fetches failed node runs without a query by default', async () => {
    const payload = { runs: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await fetchFailedNodeRuns('ws 1')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws%201/failed-node-runs',
      expect.anything()
    )
  })

  it('appends the workflow key filter when provided', async () => {
    const fetchMock = mockFetchJson({ runs: [] })
    global.fetch = fetchMock

    await fetchFailedNodeRuns('ws1', { workflowKey: 'video knowledge' })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/failed-node-runs?workflow_key=video+knowledge',
      expect.anything()
    )
  })

  it('reruns jobs by failure with a POST body', async () => {
    const payload = { results: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock
    const body = {
      category: 'technical',
      strategy: 'auto',
      job_ids: ['j1'],
    } as {
      category: 'technical'
      strategy: 'auto'
      job_ids: string[]
    }

    const result = await rerunJobsByFailure('ws1', body)

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/jobs/rerun-by-failure',
      expect.objectContaining({ method: 'POST', body: JSON.stringify(body) })
    )
  })
})
