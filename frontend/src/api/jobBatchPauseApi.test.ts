import { afterEach, describe, expect, it, vi } from 'vitest'

import { batchPauseJobs, batchResumeJobs } from './jobBatchPauseApi'

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

describe('job batch pause api', () => {
  it('pauses jobs with an optional reason', async () => {
    const payload = { results: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await batchPauseJobs('ws1', { jobIds: ['j1'] }, 'smoke hold')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/jobs/batch-pause',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ job_ids: ['j1'], reason: 'smoke hold' }),
      })
    )
  })

  it('omits the pause reason when blank', async () => {
    const fetchMock = mockFetchJson({ results: [] })
    global.fetch = fetchMock

    await batchPauseJobs('ws1', {
      filter: { paused: false, workflow_version_none: false },
      excludeIds: ['j9'],
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/jobs/batch-pause',
      expect.objectContaining({
        body: JSON.stringify({
          filter: { paused: false, workflow_version_none: false },
          exclude_ids: ['j9'],
        }),
      })
    )
  })

  it('resumes jobs for the target', async () => {
    const payload = { results: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await batchResumeJobs('ws1', { jobIds: ['j1', 'j2'] })

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/jobs/batch-resume',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ job_ids: ['j1', 'j2'] }),
      })
    )
  })
})
