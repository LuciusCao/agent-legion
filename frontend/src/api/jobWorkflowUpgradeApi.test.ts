import { afterEach, describe, expect, it, vi } from 'vitest'

import { upgradeJobWorkflow } from './jobWorkflowUpgradeApi'

const originalFetch = global.fetch

afterEach(() => {
  global.fetch = originalFetch
  vi.restoreAllMocks()
})

describe('job workflow upgrade api', () => {
  it('posts to the upgrade endpoint with an encoded job id and default mode', async () => {
    const result = {
      job_id: 'job/1',
      operation: 'upgrade_workflow',
      status: 'succeeded',
      node_key: null,
      reason_code: null,
      message: null,
    }
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(result),
      text: () => Promise.resolve(JSON.stringify(result)),
    } as Response)
    global.fetch = fetchMock

    const response = await upgradeJobWorkflow('job/1')

    expect(response).toEqual(result)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/jobs/job%2F1/upgrade-workflow',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ mode: 'clean' }),
      })
    )
  })

  it('passes the inherit mode through to the endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ results: [] }),
      text: () => Promise.resolve('{}'),
    } as Response)
    global.fetch = fetchMock

    await upgradeJobWorkflow('job-1', 'inherit')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/jobs/job-1/upgrade-workflow',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ mode: 'inherit' }),
      })
    )
  })

  it('propagates request failures to the caller', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: () => Promise.resolve({ detail: 'Job is already current' }),
      text: () =>
        Promise.resolve(JSON.stringify({ detail: 'Job is already current' })),
    } as Response)
    global.fetch = fetchMock

    await expect(upgradeJobWorkflow('job-1')).rejects.toThrow()
  })
})
