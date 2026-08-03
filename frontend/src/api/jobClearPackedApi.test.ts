import { afterEach, describe, expect, it, vi } from 'vitest'

import { clearJobsPackedStatus } from './jobClearPackedApi'

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

describe('clear packed status api', () => {
  it('posts an explicit id list target', async () => {
    const payload = { reset_count: 2 }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await clearJobsPackedStatus('ws1', { jobIds: ['j1', 'j2'] })

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/jobs/clear-packed',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ job_ids: ['j1', 'j2'] }),
      })
    )
  })

  it('posts a filter target with exclusions', async () => {
    const fetchMock = mockFetchJson({ reset_count: 1 })
    global.fetch = fetchMock

    await clearJobsPackedStatus('ws 1', {
      filter: { status: 'completed', workflow_version_none: false },
      excludeIds: ['j9'],
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws%201/jobs/clear-packed',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          filter: { status: 'completed', workflow_version_none: false },
          exclude_ids: ['j9'],
        }),
      })
    )
  })
})
