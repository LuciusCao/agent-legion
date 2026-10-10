import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useJobStore } from '../index'
import * as api from '../../../api'
import { createJobSummary } from './testHelpers'
import type { JobFilterConfig } from '../state'
import type { JobFacetsResponse } from '../../../types/jobTypes'

vi.mock('../../../api')

const mockFetchJobsSnapshot = vi.mocked(api.fetchJobsSnapshot)
const mockFetchJobFacets = vi.mocked(api.fetchJobFacets)

const defaultFilter: JobFilterConfig = {
  status: null,
  search: '',
  workflowVersion: null,
  activeNodeKey: null,
  paused: null,
}

const defaultParams = {
  status: null,
  search: null,
  workflow_version: null,
  workflow_version_none: false,
  active_node_key: null,
  paused: null,
}

const sampleFacets: JobFacetsResponse = {
  workspace_id: 'ws1',
  total: 3,
  status_counts: { pending: 2, running: 1 },
  version_counts: { '1': 2, none: 1 },
  node_counts: { extract: 2, '': 1 },
}

function resetJobListState(filterConfig: Partial<JobFilterConfig> = {}) {
  useJobStore.setState({
    jobs: [],
    jobsById: {},
    jobIds: [],
    jobIndexById: {},
    revision: 0,
    filteredJobIds: [],
    jobsWorkspaceId: 'ws1',
    isLoading: false,
    listLoadError: null,
    nextCursor: null,
    hasMore: false,
    totalJobs: null,
    facets: null,
    loadingMore: false,
    filterConfig: { ...defaultFilter, ...filterConfig },
  })
}

function page(
  jobs: ReturnType<typeof createJobSummary>[],
  overrides: Record<string, unknown> = {}
) {
  return {
    workspace_id: 'ws1',
    revision: 1,
    stats: {},
    total: jobs.length,
    jobs,
    next_cursor: null,
    ...overrides,
  }
}

describe('paginationActions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    resetJobListState()
  })

  it('setJobsPage stores the first page with total and cursor', () => {
    useJobStore
      .getState()
      .setJobsPage('ws1', 5, [createJobSummary({ id: 'j1' })], 42, 'cursor-1')

    const state = useJobStore.getState()
    expect(state.jobIds).toEqual(['j1'])
    expect(state.totalJobs).toBe(42)
    expect(state.nextCursor).toBe('cursor-1')
    expect(state.hasMore).toBe(true)
    expect(state.isLoading).toBe(false)
  })

  it('setJobsPage drops a snapshot superseded by a higher-revision patch (#1189 P1-a)', () => {
    // 水位与内容绑定：快照在途期间 job_patch_batch（更高 revision）先落地，
    // 旧快照不再被 Math.max 抬水位整页覆盖——否则服务端采样 revision 先于
    // 读 jobs 的窗口内产生的 patch 会被 revision 守卫永久丢弃。陈旧快照
    // 整体丢弃，列表保持 patch 落地后的内容、水位不前进。
    useJobStore
      .getState()
      .setJobsPage('ws1', 1, [createJobSummary({ id: 'j1' })], 1, null)
    useJobStore
      .getState()
      .applyJobPatchBatch(
        'ws1',
        3,
        [createJobSummary({ id: 'j2', status: 'running' })],
        []
      )
    useJobStore
      .getState()
      .setJobsPage(
        'ws1',
        2,
        [createJobSummary({ id: 'j1' }), createJobSummary({ id: 'j2' })],
        2,
        null
      )

    const state = useJobStore.getState()
    expect(state.jobIds).toEqual(['j2', 'j1'])
    expect(state.totalJobs).toBe(1)
    expect(state.revision).toBe(3)
    expect(state.isLoading).toBe(false)
  })

  it('loadMoreJobs fetches the cursor page and appends it', async () => {
    useJobStore
      .getState()
      .setJobsPage('ws1', 1, [createJobSummary({ id: 'j1' })], 2, 'cursor-1')
    mockFetchJobsSnapshot.mockResolvedValueOnce(
      page([createJobSummary({ id: 'j2' })], { next_cursor: null })
    )

    await useJobStore.getState().loadMoreJobs('ws1')

    expect(mockFetchJobsSnapshot).toHaveBeenCalledWith(
      'ws1',
      500,
      'cursor-1',
      defaultParams
    )
    const state = useJobStore.getState()
    expect(state.jobIds).toEqual(['j1', 'j2'])
    expect(state.hasMore).toBe(false)
    expect(state.nextCursor).toBeNull()
    expect(state.loadingMore).toBe(false)
  })

  it('loadMoreJobs is a no-op without hasMore or while a load is in flight', async () => {
    useJobStore.setState({ hasMore: false, nextCursor: null })
    await useJobStore.getState().loadMoreJobs('ws1')

    useJobStore.setState({
      hasMore: true,
      nextCursor: 'cursor-1',
      loadingMore: true,
    })
    await useJobStore.getState().loadMoreJobs('ws1')

    expect(mockFetchJobsSnapshot).not.toHaveBeenCalled()
  })

  it('loadMoreJobs drops the page when the cursor moved on mid-flight', async () => {
    useJobStore
      .getState()
      .setJobsPage('ws1', 1, [createJobSummary({ id: 'j1' })], 2, 'cursor-1')
    let resolvePage!: (value: ReturnType<typeof page>) => void
    mockFetchJobsSnapshot.mockImplementationOnce(
      () => new Promise((resolve) => (resolvePage = resolve))
    )

    const pending = useJobStore.getState().loadMoreJobs('ws1')
    // A filter refetch resets the list and cursor while the page is loading.
    useJobStore.setState({ nextCursor: 'cursor-2', loadingMore: false })
    resolvePage(page([createJobSummary({ id: 'j2' })]))
    await pending

    expect(useJobStore.getState().jobIds).toEqual(['j1'])
  })

  it('refreshFirstPage resets the list and refetches with the filter', async () => {
    resetJobListState({
      status: 'failed',
      search: 'q1',
      workflowVersion: 'none',
      activeNodeKey: 'extract',
    })
    useJobStore
      .getState()
      .setJobsPage('ws1', 1, [createJobSummary({ id: 'old' })], 1, null)
    const expectedParams = {
      status: 'failed',
      search: 'q1',
      workflow_version: null,
      workflow_version_none: true,
      active_node_key: 'extract',
      paused: null,
    }
    mockFetchJobsSnapshot.mockResolvedValueOnce(
      page([createJobSummary({ id: 'j1', status: 'failed' })], {
        revision: 2,
        total: 7,
        next_cursor: 'cursor-2',
      })
    )
    mockFetchJobFacets.mockResolvedValueOnce(sampleFacets)

    await useJobStore.getState().refreshFirstPage('ws1')

    expect(mockFetchJobsSnapshot).toHaveBeenCalledWith(
      'ws1',
      500,
      undefined,
      expectedParams
    )
    expect(mockFetchJobFacets).toHaveBeenCalledWith('ws1', expectedParams)
    const state = useJobStore.getState()
    expect(state.jobIds).toEqual(['j1'])
    expect(state.totalJobs).toBe(7)
    expect(state.hasMore).toBe(true)
    expect(state.facets).toEqual(sampleFacets)
    expect(state.isLoading).toBe(false)
  })

  it('refreshFirstPage drops the response when the filter changed mid-flight', async () => {
    let resolvePage!: (value: ReturnType<typeof page>) => void
    mockFetchJobsSnapshot.mockImplementationOnce(
      () => new Promise((resolve) => (resolvePage = resolve))
    )

    const pending = useJobStore.getState().refreshFirstPage('ws1')
    expect(useJobStore.getState().isLoading).toBe(true)
    useJobStore.getState().setFilterConfig({ status: 'running' })
    resolvePage(page([createJobSummary({ id: 'j1' })]))
    await pending

    expect(useJobStore.getState().jobIds).toEqual([])
    expect(useJobStore.getState().isLoading).toBe(true)
  })

  it('refreshFirstPage surfaces fetch errors', async () => {
    mockFetchJobsSnapshot.mockRejectedValueOnce(new Error('boom'))

    await useJobStore.getState().refreshFirstPage('ws1')

    expect(useJobStore.getState().listLoadError).toBe('boom')
    expect(useJobStore.getState().isLoading).toBe(false)
  })

  it('refreshFirstPage re-pulls when the response is staler than an in-flight patch (#1189 P1-a)', async () => {
    // 快照在途期间 patch 落地抬了水位：低 revision 响应不应用、直接重拉，
    // 最终列表来自第二次响应——旧内容整页覆盖新内容的回归钉住。
    let resolveFirst!: (value: ReturnType<typeof page>) => void
    mockFetchJobsSnapshot
      .mockImplementationOnce(
        () => new Promise((resolve) => (resolveFirst = resolve))
      )
      .mockResolvedValueOnce(
        page([createJobSummary({ id: 'j1' }), createJobSummary({ id: 'j2' })], {
          revision: 3,
          total: 2,
        })
      )
    mockFetchJobFacets.mockResolvedValueOnce(sampleFacets)

    const pending = useJobStore.getState().refreshFirstPage('ws1')
    useJobStore
      .getState()
      .applyJobPatchBatch(
        'ws1',
        2,
        [createJobSummary({ id: 'j9', status: 'running' })],
        []
      )
    resolveFirst(page([createJobSummary({ id: 'old' })], { revision: 1 }))
    await pending

    expect(mockFetchJobsSnapshot).toHaveBeenCalledTimes(2)
    const state = useJobStore.getState()
    expect(state.jobIds).toEqual(['j1', 'j2'])
    expect(state.revision).toBe(3)
    expect(state.listLoadError).toBeNull()
    expect(state.isLoading).toBe(false)
  })

  it('refreshFirstPage keeps the loaded list when the facets fetch fails (#1189 P1-b)', async () => {
    mockFetchJobsSnapshot.mockResolvedValueOnce(
      page([createJobSummary({ id: 'j1' })], { revision: 1 })
    )
    mockFetchJobFacets.mockRejectedValueOnce(new Error('facets down'))

    await useJobStore.getState().refreshFirstPage('ws1')

    const state = useJobStore.getState()
    expect(state.jobIds).toEqual(['j1'])
    expect(state.listLoadError).toBeNull()
    expect(state.facets).toBeNull()
    expect(state.isLoading).toBe(false)
    // patch 守卫未被冻结，后续 patch 正常落地。
    useJobStore
      .getState()
      .applyJobPatchBatch(
        'ws1',
        2,
        [createJobSummary({ id: 'j1', status: 'running' })],
        []
      )
    expect(useJobStore.getState().jobsById.j1.status).toBe('running')
  })
})

describe('applyJobPatchBatch with server-side filters', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    resetJobListState({ status: 'failed' })
    useJobStore
      .getState()
      .setJobsPage(
        'ws1',
        1,
        [createJobSummary({ id: 'j1', status: 'failed' })],
        1,
        null
      )
  })

  it('inserts a new job matching the filter at the top', () => {
    useJobStore
      .getState()
      .applyJobPatchBatch(
        'ws1',
        2,
        [createJobSummary({ id: 'j2', status: 'failed' })],
        []
      )

    const state = useJobStore.getState()
    expect(state.jobIds[0]).toBe('j2')
    expect(state.filteredJobIds).toEqual(['j2', 'j1'])
    expect(state.jobsById.j2.status).toBe('failed')
  })

  it('skips a new job that does not match the filter', () => {
    useJobStore
      .getState()
      .applyJobPatchBatch(
        'ws1',
        2,
        [createJobSummary({ id: 'j3', status: 'running' })],
        []
      )

    const state = useJobStore.getState()
    expect(state.jobIds).toEqual(['j1'])
    expect(state.filteredJobIds).toEqual(['j1'])
    expect(state.jobsById.j3).toBeUndefined()
    expect(state.revision).toBe(2)
  })

  it('removes a loaded job whose patch moves it out of the filter', () => {
    useJobStore
      .getState()
      .applyJobPatchBatch(
        'ws1',
        2,
        [createJobSummary({ id: 'j1', status: 'completed' })],
        []
      )

    const state = useJobStore.getState()
    expect(state.jobsById.j1.status).toBe('completed')
    expect(state.filteredJobIds).toEqual([])
  })
})
