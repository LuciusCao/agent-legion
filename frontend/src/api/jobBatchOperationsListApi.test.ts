import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  archiveJobBatchOperation,
  listJobBatchOperations,
} from './jobBatchOperationsListApi'

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

describe('jobBatchOperationsListApi（#946 M61 列表/归档面）', () => {
  it('lists a view with keyset cursor params', async () => {
    const payload = { operations: [], next_cursor: 'abc', active_count: 2 }
    const fetchMock = mockFetchJson(payload)
    global.fetch = fetchMock

    const result = await listJobBatchOperations('ws1', {
      view: 'ended',
      limit: 12,
      cursor: 'abc',
    })

    expect(result).toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/job-batch-operations?view=ended&limit=12&cursor=abc',
      expect.anything()
    )
  })

  it('defaults to the active view without extra params', async () => {
    const fetchMock = mockFetchJson({
      operations: [],
      next_cursor: null,
      active_count: 0,
    })
    global.fetch = fetchMock

    await listJobBatchOperations('ws1', { view: 'active' })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/job-batch-operations?view=active',
      expect.anything()
    )
  })

  it('archives an operation with POST', async () => {
    const fetchMock = mockFetchJson({ operation: { id: 'op1' } })
    global.fetch = fetchMock

    await archiveJobBatchOperation('ws1', 'op1')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/workspaces/ws1/job-batch-operations/op1/archive',
      expect.objectContaining({ method: 'POST' })
    )
  })
})
