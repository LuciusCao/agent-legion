import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  getJobBatchOperation,
  isJobBatchAccepted,
  listJobBatchOperationResults,
  type JobBatchOperationAccepted,
} from './jobBatchOperationsApi'
import {
  cancelJobBatchOperation,
  pauseJobBatchOperation,
  resumeJobBatchOperation,
} from './jobBatchOperationControlApi'

afterEach(() => {
  vi.restoreAllMocks()
})

function mockFetchJson(response: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status,
    json: () => Promise.resolve(response),
    text: () => Promise.resolve(JSON.stringify(response)),
  } as Response)
}

function accepted(opId = 'a1b2c3d4e5f6'): JobBatchOperationAccepted {
  return {
    operation: {
      id: opId,
      workspace_id: 'ws1',
      kind: 'rerun',
      status: 'pending',
      items_total: 2400,
      processed_count: 0,
      succeeded_count: 0,
      skipped_count: 0,
      failed_count: 0,
      reason_counts: {},
      output: {},
      chunk_errors: [],
      error_message: '',
      created_by: 'tester',
      created_at: '2026-10-22T10:00:00+00:00',
      updated_at: null,
      finished_at: null,
    },
  }
}

describe('isJobBatchAccepted（#946）', () => {
  it('recognizes the 202 accepted envelope', () => {
    expect(isJobBatchAccepted(accepted())).toBe(true)
  })

  it('rejects sync batch results and malformed shapes', () => {
    expect(isJobBatchAccepted({ results: [] })).toBe(false)
    expect(isJobBatchAccepted(null)).toBe(false)
    expect(isJobBatchAccepted('operation')).toBe(false)
    // 逐 job 结果项里的 operation 字段是字符串，不是句柄对象。
    expect(isJobBatchAccepted({ operation: 'rerun' })).toBe(false)
    expect(isJobBatchAccepted({ operation: { id: 1 } })).toBe(false)
  })
})

describe('jobBatchOperationsApi polling surface（#946）', () => {
  it('gets a single operation handle', async () => {
    const payload = accepted().operation
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await getJobBatchOperation('ws1', 'op9')

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/job-batch-operations/op9',
      expect.anything()
    )
  })

  it('pages results with the keyset cursor', async () => {
    const payload = {
      results: [{ seq: 3, result: { job_id: 'j1', status: 'succeeded' } }],
      next_cursor: '3',
    }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await listJobBatchOperationResults('ws1', 'op9', {
      limit: 100,
      cursor: '2',
    })

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/job-batch-operations/op9/results?limit=100&cursor=2',
      expect.anything()
    )
  })

  it('omits empty query params on the first page', async () => {
    const fetchMock = mockFetchJson({ results: [], next_cursor: null })
    global.fetch = fetchMock

    await listJobBatchOperationResults('ws1', 'op9')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/job-batch-operations/op9/results',
      expect.anything()
    )
  })
})

describe('jobBatchOperationsApi control surface（#1397）', () => {
  const controls = [
    ['pause', pauseJobBatchOperation],
    ['resume', resumeJobBatchOperation],
    ['cancel', cancelJobBatchOperation],
  ] as const

  it.each(controls)(
    'posts %s and returns the operation',
    async (action, control) => {
      const payload = accepted().operation
      const fetchMock = mockFetchJson(payload)
      global.fetch = fetchMock

      await expect(control('ws 1', 'op/9')).resolves.toEqual(payload)
      expect(fetchMock).toHaveBeenCalledWith(
        `/api/workspaces/ws%201/job-batch-operations/op%2F9/${action}`,
        expect.objectContaining({ method: 'POST' })
      )
    }
  )
})
