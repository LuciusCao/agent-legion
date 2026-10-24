import { afterEach, describe, expect, it, vi } from 'vitest'
import { batchApprovalJobs } from './jobApprovalApi'

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

describe('batchApprovalJobs', () => {
  it('posts an explicit approval selection with an empty note', async () => {
    const payload = { results: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    await expect(
      batchApprovalJobs('ws 1', 'approve', { jobIds: ['j1', 'j2'] })
    ).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws%201/jobs/batch-approve',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ job_ids: ['j1', 'j2'], note: '' }),
      })
    )
  })

  it('posts a rejection filter and exclusions', async () => {
    const fetchMock = mockFetchJson({ results: [] })
    global.fetch = fetchMock

    await batchApprovalJobs('ws1', 'reject', {
      filter: { status: 'awaiting_approval', workflow_version_none: false },
      excludeIds: ['j9'],
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/jobs/batch-reject',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          filter: {
            status: 'awaiting_approval',
            workflow_version_none: false,
          },
          exclude_ids: ['j9'],
          note: '',
        }),
      })
    )
  })
})
