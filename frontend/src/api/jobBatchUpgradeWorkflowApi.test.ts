import { afterEach, describe, expect, it, vi } from 'vitest'

import { batchUpgradeJobsWorkflow } from './jobBatchUpgradeWorkflowApi'

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

describe('batch upgrade jobs workflow api', () => {
  it('posts an explicit id list target to the batch endpoint', async () => {
    const payload = { results: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await batchUpgradeJobsWorkflow('ws1', {
      jobIds: ['j1', 'j2'],
    })

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/jobs/batch-upgrade-workflow',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ job_ids: ['j1', 'j2'] }),
      })
    )
  })

  it('posts a filter target with exclusions to the batch endpoint', async () => {
    const fetchMock = mockFetchJson({ results: [] })
    global.fetch = fetchMock

    await batchUpgradeJobsWorkflow('ws 1', {
      filter: { status: 'pending', workflow_version_none: false },
      excludeIds: ['j9'],
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws%201/jobs/batch-upgrade-workflow',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          filter: { status: 'pending', workflow_version_none: false },
          exclude_ids: ['j9'],
        }),
      })
    )
  })
})
