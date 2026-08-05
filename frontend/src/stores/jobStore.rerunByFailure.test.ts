import { describe, it, expect, vi, beforeEach } from 'vitest'
import { normalizeJobs, useJobStore } from './jobStore'
import { createMockUiState, makeJob } from '../testing/fixtures'

vi.mock('../api', () => ({
  fetchJobs: vi.fn(),
  api: vi.fn(),
}))

vi.mock('../api/failureApi', () => ({
  rerunJobsByFailure: vi.fn(),
}))

vi.mock('./uiStore', () => ({
  useUiStore: {
    getState: vi.fn(),
    setState: vi.fn(),
  },
}))

import { fetchJobs } from '../api'
import { rerunJobsByFailure } from '../api/failureApi'
import { useUiStore } from './uiStore'

const mockFetchJobs = vi.mocked(fetchJobs)
const mockRerunJobsByFailure = vi.mocked(rerunJobsByFailure)
const mockShowToast = vi.fn()
const mockGetState = vi.mocked(useUiStore.getState)
const mockRefreshFirstPage = vi.fn()

describe('jobStore rerunByFailureCategory', () => {
  beforeEach(() => {
    useJobStore.setState({
      ...normalizeJobs([
        makeJob({ id: 'j1', status: 'failed' }),
        makeJob({ id: 'j2', status: 'failed' }),
      ]),
      isLoading: false,
      error: null,
      selectedIds: new Set(['j1', 'j2']),
      selectionMode: 'explicit',
      selectionFilter: null,
      excludedIds: new Set(),
      selectionCount: null,
      selectMode: true,
      refreshFirstPage: mockRefreshFirstPage,
    })
    mockFetchJobs.mockReset()
    mockFetchJobs.mockResolvedValue({ jobs: [] })
    mockRefreshFirstPage.mockReset()
    mockRefreshFirstPage.mockResolvedValue(undefined)
    mockRerunJobsByFailure.mockReset()
    mockShowToast.mockReset()
    mockGetState.mockReturnValue(
      createMockUiState({ showToast: mockShowToast })
    )
  })

  it('posts the category request and refreshes jobs on success', async () => {
    mockRerunJobsByFailure.mockResolvedValue({
      results: [
        {
          job_id: 'j1',
          operation: 'rerun',
          status: 'succeeded',
          node_key: 'extract',
          rerun_nodes: ['extract'],
        },
        {
          job_id: 'j2',
          operation: 'rerun',
          status: 'skipped',
          node_key: null,
          reason_code: 'no_matching_failure',
        },
      ],
    })

    const data = await useJobStore.getState().rerunByFailureCategory('ws1', {
      category: 'technical',
      jobIds: ['j1', 'j2'],
    })

    expect(mockRerunJobsByFailure).toHaveBeenCalledWith('ws1', {
      category: 'technical',
      strategy: 'auto',
      job_ids: ['j1', 'j2'],
    })
    expect(data.results).toHaveLength(2)
    expect(mockShowToast).toHaveBeenCalledWith(
      '重跑完成：成功 1 项，跳过 1 项',
      'success'
    )
    expect(useJobStore.getState().selectedIds.size).toBe(0)
    expect(mockRefreshFirstPage).toHaveBeenCalledWith('ws1')
    expect(mockFetchJobs).not.toHaveBeenCalled()
  })

  it('mentions upstream reruns in the toast when rerun_nodes exceed the failed node', async () => {
    mockRerunJobsByFailure.mockResolvedValue({
      results: [
        {
          job_id: 'j1',
          operation: 'rerun',
          status: 'succeeded',
          node_key: 'review',
          rerun_nodes: ['generate', 'review'],
        },
      ],
    })

    await useJobStore.getState().rerunByFailureCategory('ws1', {
      category: 'business',
      jobIds: ['j1'],
    })

    expect(mockShowToast).toHaveBeenCalledWith(
      '重跑完成：成功 1 项，含上游节点重跑',
      'success'
    )
  })

  it('surfaces errors via toast and rethrows', async () => {
    mockRerunJobsByFailure.mockRejectedValue(new Error('server down'))

    await expect(
      useJobStore.getState().rerunByFailureCategory('ws1', {
        category: 'unknown',
        jobIds: ['j1'],
      })
    ).rejects.toThrow('server down')

    expect(mockShowToast).toHaveBeenCalledWith('server down', 'error')
    expect(useJobStore.getState().error).toBe('server down')
    expect(mockFetchJobs).not.toHaveBeenCalled()
  })

  it('includes from_node_key in the request when a start node is given', async () => {
    mockRerunJobsByFailure.mockResolvedValue({
      results: [
        {
          job_id: 'j1',
          operation: 'rerun',
          status: 'succeeded',
          node_key: 'extract',
          rerun_nodes: ['extract'],
        },
      ],
    })

    await useJobStore.getState().rerunByFailureCategory('ws1', {
      category: 'technical',
      jobIds: ['j1'],
      fromNodeKey: 'extract',
    })

    expect(mockRerunJobsByFailure).toHaveBeenCalledWith('ws1', {
      category: 'technical',
      strategy: 'auto',
      job_ids: ['j1'],
      from_node_key: 'extract',
    })
  })

  it('does nothing when there are no job ids', async () => {
    useJobStore.setState({ selectedIds: new Set() })

    const data = await useJobStore.getState().rerunByFailureCategory('ws1', {
      category: 'technical',
    })

    expect(data).toEqual({ results: [] })
    expect(mockRerunJobsByFailure).not.toHaveBeenCalled()
  })
})
