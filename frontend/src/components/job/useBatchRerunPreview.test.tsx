import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useBatchRerunPreview } from './useBatchRerunPreview'
import { TestQueryProvider } from '../../testing/testQueryClient'
import { useJobStore } from '../../stores/jobStore'

vi.mock('../../api/jobRerunPreviewApi', () => ({
  previewBatchRerunJobs: vi.fn(),
}))

import { previewBatchRerunJobs } from '../../api/jobRerunPreviewApi'

const mockPreview = vi.mocked(previewBatchRerunJobs)

const SELECTION_FILTER = {
  status: 'failed',
  search: null,
  workflow_version: null,
  workflow_version_none: false,
  active_node_key: null,
  packed: null,
}

function enterAllMatching() {
  useJobStore.setState({
    selectionMode: 'allMatching',
    selectionFilter: SELECTION_FILTER,
    excludedIds: new Set(['j9']),
    selectedIds: new Set(),
  })
}

describe('useBatchRerunPreview', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockPreview.mockResolvedValue({ total_count: 10, eligible_count: 3 })
  })

  it('sends the selection filter target with the node mode body', async () => {
    enterAllMatching()
    const { result } = renderHook(
      () =>
        useBatchRerunPreview('ws1', true, {
          kind: 'node',
          nodeKey: 'generate',
        }),
      { wrapper: TestQueryProvider }
    )

    await waitFor(() => expect(result.current.data?.eligible_count).toBe(3))
    // 与 confirm 的 batch-rerun 载荷同源（selection filter + exclusions）。
    expect(mockPreview).toHaveBeenCalledWith(
      'ws1',
      { filter: SELECTION_FILTER, excludeIds: ['j9'] },
      { nodeKey: 'generate' }
    )
  })

  it('maps failed-node and category modes to the request body', async () => {
    enterAllMatching()
    const { result: failedNode } = renderHook(
      () => useBatchRerunPreview('ws1', true, { kind: 'failedNode' }),
      { wrapper: TestQueryProvider }
    )
    await waitFor(() => expect(failedNode.current.data).toBeDefined())
    expect(mockPreview).toHaveBeenLastCalledWith('ws1', expect.anything(), {
      fromFailedNode: true,
    })

    const { result: category } = renderHook(
      () =>
        useBatchRerunPreview('ws1', true, {
          kind: 'category',
          category: 'technical',
        }),
      { wrapper: TestQueryProvider }
    )
    await waitFor(() => expect(category.current.data).toBeDefined())
    expect(mockPreview).toHaveBeenLastCalledWith('ws1', expect.anything(), {
      failureCategory: 'technical',
    })
  })

  it('stays disabled when closed or without workspace', () => {
    enterAllMatching()
    renderHook(
      () => useBatchRerunPreview(undefined, true, { kind: 'failedNode' }),
      {
        wrapper: TestQueryProvider,
      }
    )
    renderHook(
      () => useBatchRerunPreview('ws1', false, { kind: 'failedNode' }),
      {
        wrapper: TestQueryProvider,
      }
    )
    expect(mockPreview).not.toHaveBeenCalled()
  })
})
