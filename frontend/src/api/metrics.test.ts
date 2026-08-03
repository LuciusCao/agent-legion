import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchOpsMetrics } from './metrics'

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

describe('ops metrics api', () => {
  it('fetches metrics with only a granularity', async () => {
    const payload = { granularity: '6h', buckets: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await fetchOpsMetrics({ granularity: '6h' })

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/metrics/overview?granularity=6h',
      expect.anything()
    )
  })

  it('appends the worker id when provided', async () => {
    const fetchMock = mockFetchJson({ granularity: '24h', buckets: [] })
    global.fetch = fetchMock

    await fetchOpsMetrics({ granularity: '24h', worker_id: 'w/1' })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/metrics/overview?granularity=24h&worker_id=w%2F1',
      expect.anything()
    )
  })
})
