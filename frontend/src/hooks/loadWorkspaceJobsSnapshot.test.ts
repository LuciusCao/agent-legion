import { describe, it, expect, vi, beforeEach } from 'vitest'
import { loadWorkspaceJobsSnapshot } from './loadWorkspaceJobsSnapshot'
import { createJobSummary, useJobStore } from '../stores/jobStore'
import { createTestQueryClient } from '../testing/testQueryClient'
import { fetchJobFacets, fetchJobsSnapshot } from '../api'

vi.mock('../api', () => ({
  fetchJobsSnapshot: vi.fn(),
  fetchJobFacets: vi.fn(),
}))

const mockFetchJobsSnapshot = vi.mocked(fetchJobsSnapshot)
const mockFetchJobFacets = vi.mocked(fetchJobFacets)

const sampleFacets = {
  workspace_id: 'ws1',
  total: 1,
  status_counts: { running: 1 },
  version_counts: {},
  node_counts: {},
}

function resetListState() {
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
    facets: null,
    filterConfig: {
      status: null,
      search: '',
      workflowVersion: null,
      activeNodeKey: null,
      paused: null,
    },
  })
}

describe('loadWorkspaceJobsSnapshot', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    resetListState()
  })

  it('loads the first page and facets into the store', async () => {
    mockFetchJobsSnapshot.mockResolvedValueOnce({
      workspace_id: 'ws1',
      revision: 1,
      stats: { running: 1 },
      total: 1,
      jobs: [createJobSummary({ id: 'j1', workspace_id: 'ws1' })],
      next_cursor: null,
    })
    mockFetchJobFacets.mockResolvedValueOnce(sampleFacets)

    await loadWorkspaceJobsSnapshot(createTestQueryClient(), 'ws1', () => false)

    const state = useJobStore.getState()
    expect(state.jobIds).toEqual(['j1'])
    expect(state.revision).toBe(1)
    expect(state.facets).toEqual(sampleFacets)
  })

  it('keeps the loaded list when the facets fetch fails (#1189 P1-b)', async () => {
    // facets 只是计数面板：失败独立降级，不抛出——此前抛出会被
    // createLoadSnapshot 的 catch 当整页失败调 failJobFetch 清空刚写入
    // 的列表，瞬时 facets 故障即整页不可用。
    mockFetchJobsSnapshot.mockResolvedValueOnce({
      workspace_id: 'ws1',
      revision: 1,
      stats: {},
      total: 1,
      jobs: [createJobSummary({ id: 'j1', workspace_id: 'ws1' })],
      next_cursor: null,
    })
    mockFetchJobFacets.mockRejectedValueOnce(new Error('facets down'))

    await expect(
      loadWorkspaceJobsSnapshot(createTestQueryClient(), 'ws1', () => false)
    ).resolves.toBeUndefined()

    const state = useJobStore.getState()
    expect(state.jobIds).toEqual(['j1'])
    expect(state.listLoadError).toBeNull()
    expect(state.facets).toBeNull()
    // patch 守卫未被冻结，后续 patch 正常落地。
    useJobStore.getState().applyJobPatchBatch(
      'ws1',
      2,
      [
        createJobSummary({
          id: 'j1',
          workspace_id: 'ws1',
          status: 'running',
        }),
      ],
      []
    )
    expect(useJobStore.getState().jobsById.j1.status).toBe('running')
  })
})
