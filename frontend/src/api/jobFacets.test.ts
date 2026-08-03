import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchJobFacets } from './jobFacets'

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

describe('job facets api', () => {
  it('fetches facets without a query when no filter is given', async () => {
    const payload = { facets: {} }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await fetchJobFacets('ws1')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/jobs/facets',
      expect.anything()
    )
  })

  it('appends filter params to the query', async () => {
    const fetchMock = mockFetchJson({ facets: {} })
    global.fetch = fetchMock

    await fetchJobFacets('ws 1', {
      status: 'failed',
      packed: 0,
      workflow_version_none: false,
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws%201/jobs/facets?status=failed&packed=0',
      expect.anything()
    )
  })
})
