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
}

const defaultParams = {
  status: null,
  search: null,
  workflow_version: null,
  workflow_version_none: false,
  active_node_key: null,
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
    error: null,
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

    expect(useJobStore.getState().error).toBe('boom')
    expect(useJobStore.getState().isLoading).toBe(false)
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
