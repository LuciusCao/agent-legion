import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  batchDeleteJobs,
  batchRerunJobs,
  batchRunToJobs,
  packageJobs,
} from './jobBatchApi'

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

describe('job batch api', () => {
  it('runs to a node for an id list target', async () => {
    const payload = { results: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await batchRunToJobs('ws1', 'review_key_info', {
      jobIds: ['j1'],
    })

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/jobs/batch-run-to',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          job_ids: ['j1'],
          target_node_key: 'review_key_info',
        }),
      })
    )
  })

  it('includes the start node only when provided', async () => {
    const fetchMock = mockFetchJson({ results: [] })
    global.fetch = fetchMock

    await batchRunToJobs('ws1', 'assemble', { jobIds: ['j1'] }, 'fetch_items')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/jobs/batch-run-to',
      expect.objectContaining({
        body: JSON.stringify({
          job_ids: ['j1'],
          target_node_key: 'assemble',
          start_node_key: 'fetch_items',
        }),
      })
    )
  })

  it('reruns a batch with the default from_failed_node flag', async () => {
    const fetchMock = mockFetchJson({ results: [] })
    global.fetch = fetchMock

    await batchRerunJobs('ws 1', null, { jobIds: ['j1'] })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws%201/jobs/batch-rerun',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ job_ids: ['j1'], from_failed_node: false }),
      })
    )
  })

  it('reruns from a failed node with an explicit node key', async () => {
    const fetchMock = mockFetchJson({ results: [] })
    global.fetch = fetchMock

    await batchRerunJobs(
      'ws1',
      'generate_key_info',
      {
        filter: { status: 'failed', workflow_version_none: false },
        excludeIds: ['j9'],
      },
      { fromFailedNode: true }
    )

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/jobs/batch-rerun',
      expect.objectContaining({
        body: JSON.stringify({
          filter: { status: 'failed', workflow_version_none: false },
          exclude_ids: ['j9'],
          from_failed_node: true,
          node_key: 'generate_key_info',
        }),
      })
    )
  })

  it('packages jobs for the target', async () => {
    const payload = { packages: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await packageJobs('ws1', { jobIds: ['j1'] })

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/jobs/package',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ job_ids: ['j1'] }),
      })
    )
  })

  it('deletes a batch with a DELETE body', async () => {
    const payload = { results: [] }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await batchDeleteJobs('ws1', { jobIds: ['j1', 'j2'] })

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/jobs/batch',
      expect.objectContaining({
        method: 'DELETE',
        body: JSON.stringify({ job_ids: ['j1', 'j2'] }),
      })
    )
  })
})
