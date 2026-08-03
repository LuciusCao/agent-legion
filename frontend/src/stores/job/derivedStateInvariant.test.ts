import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useJobStore } from './index'
import * as api from '../../api'
import { batchDeleteJobs } from '../../api/jobApi'
import { createMockUiState, makeJob } from '../../testing/fixtures'
import { createOptionAccumulator } from './filterLogic/optionAccumulator'
import { initialJobDataState } from './initialState'
import { useUiStore } from '../uiStore'
import type { JobState } from './state'

vi.mock('../../api')
vi.mock('../../api/jobApi')
vi.mock('../../api/failureApi')
vi.mock('../../api/jobWorkflowUpgradeApi')

vi.mock('../uiStore', () => ({
  useUiStore: {
    getState: vi.fn(),
    setState: vi.fn(),
  },
}))

const mockFetchJobs = vi.mocked(api.fetchJobs)
const mockFetchJobsSnapshot = vi.mocked(api.fetchJobsSnapshot)
const mockFetchJobFacets = vi.mocked(api.fetchJobFacets)
const mockBatchDeleteJobs = vi.mocked(batchDeleteJobs)
const mockGetUiState = vi.mocked(useUiStore.getState)
const mockRefreshFirstPage = vi.fn()

function makeWsJob(id: string, overrides: Record<string, unknown> = {}) {
  return makeJob({ id, workspace_id: 'ws1', ...overrides })
}

function seedJobs(count: number) {
  return Array.from({ length: count }, (_, i) => makeWsJob(`j${i + 1}`))
}

function snapshotPage(jobs: ReturnType<typeof makeJob>[], revision: number) {
  return {
    workspace_id: 'ws1',
    revision,
    stats: {},
    total: jobs.length,
    jobs,
    next_cursor: null,
  }
}

/** Cross-check the four parallel derived collections: every job in `jobs`
 * must be addressable in `jobsById`, `jobIds` must mirror `jobs` order, and
 * `jobIndexById` must mirror array indices — with no stale extra keys. */
function expectConsistentDerivedState(state: JobState) {
  const ids = state.jobs.map((job) => job.id)
  expect(state.jobIds).toEqual(ids)
  expect(Object.keys(state.jobsById).sort()).toEqual([...ids].sort())
  expect(Object.keys(state.jobIndexById).sort()).toEqual([...ids].sort())
  state.jobs.forEach((job, index) => {
    expect(state.jobsById[job.id]).toBe(job)
    expect(state.jobIndexById[job.id]).toBe(index)
  })
}

describe('job derived state invariants', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useJobStore.setState({
      ...initialJobDataState,
      optionAccumulator: createOptionAccumulator([]),
      filterConfig: { ...initialJobDataState.filterConfig },
      selectedIds: new Set<string>(),
      jobsWorkspaceId: 'ws1',
    })
    mockGetUiState.mockReturnValue(createMockUiState({ showToast: vi.fn() }))
  })

  describe('setJobsSnapshot', () => {
    it('builds consistent derived collections', () => {
      useJobStore.getState().setJobsSnapshot('ws1', 1, seedJobs(3))
      expectConsistentDerivedState(useJobStore.getState())
    })

    it('keeps consistency when a stale snapshot is ignored', () => {
      useJobStore.getState().setJobsSnapshot('ws1', 2, seedJobs(2))
      useJobStore.getState().setJobsSnapshot('ws1', 1, [makeWsJob('j9')])
      expect(useJobStore.getState().jobIds).toEqual(['j1', 'j2'])
      expectConsistentDerivedState(useJobStore.getState())
    })
  })

  describe('appendJobsSnapshot', () => {
    it('appends only unknown jobs and stays consistent', () => {
      useJobStore.getState().setJobsSnapshot('ws1', 1, seedJobs(2))
      useJobStore
        .getState()
        .appendJobsSnapshot('ws1', [makeWsJob('j2'), makeWsJob('j3')])
      expect(useJobStore.getState().jobIds).toEqual(['j1', 'j2', 'j3'])
      expectConsistentDerivedState(useJobStore.getState())
    })
  })

  describe('applyJobPatchBatch', () => {
    it('patches existing jobs in place and stays consistent', () => {
      useJobStore.getState().setJobsSnapshot('ws1', 1, seedJobs(3))
      useJobStore
        .getState()
        .applyJobPatchBatch(
          'ws1',
          2,
          [makeWsJob('j2', { status: 'running' })],
          []
        )
      const state = useJobStore.getState()
      expect(state.jobIds).toEqual(['j1', 'j2', 'j3'])
      expect(state.jobsById.j2.status).toBe('running')
      expectConsistentDerivedState(state)
    })

    it('reorders on additions and deletions and stays consistent', () => {
      useJobStore.getState().setJobsSnapshot('ws1', 1, seedJobs(3))
      useJobStore
        .getState()
        .applyJobPatchBatch('ws1', 2, [makeWsJob('j0')], ['j2'])
      const state = useJobStore.getState()
      expect(state.jobIds).toEqual(['j0', 'j1', 'j3'])
      expectConsistentDerivedState(state)
    })

    it('keeps consistency when a stale patch is ignored', () => {
      useJobStore.getState().setJobsSnapshot('ws1', 2, seedJobs(2))
      useJobStore
        .getState()
        .applyJobPatchBatch('ws1', 2, [makeWsJob('j9')], ['j1'])
      expect(useJobStore.getState().jobIds).toEqual(['j1', 'j2'])
      expectConsistentDerivedState(useJobStore.getState())
    })
  })

  describe('fetchJobs', () => {
    it('keeps consistency after a successful fetch', async () => {
      const jobs = seedJobs(3)
      mockFetchJobs.mockResolvedValueOnce({ jobs })
      await useJobStore.getState().fetchJobs('ws1')
      expectConsistentDerivedState(useJobStore.getState())
    })

    it('clears the list consistently after a failed fetch', async () => {
      useJobStore.getState().setJobsSnapshot('ws1', 1, seedJobs(2))
      mockFetchJobs.mockRejectedValueOnce(new Error('network down'))
      await useJobStore.getState().fetchJobs('ws1')
      expect(useJobStore.getState().jobs).toEqual([])
      expectConsistentDerivedState(useJobStore.getState())
    })
  })

  describe('setJobsAndFinishLoading', () => {
    it('replaces the list consistently', () => {
      useJobStore.getState().setJobsSnapshot('ws1', 1, seedJobs(2))
      useJobStore.getState().setJobsAndFinishLoading([makeWsJob('j7')])
      expect(useJobStore.getState().jobIds).toEqual(['j7'])
      expectConsistentDerivedState(useJobStore.getState())
    })
  })

  describe('resetForWorkspace / failJobFetch', () => {
    it('clears the list consistently when switching workspaces', () => {
      useJobStore.getState().setJobsSnapshot('ws1', 1, seedJobs(2))
      useJobStore.getState().resetForWorkspace('ws2')
      expect(useJobStore.getState().jobs).toEqual([])
      expectConsistentDerivedState(useJobStore.getState())
    })

    it('clears the list consistently on fetch failure', () => {
      useJobStore.getState().setJobsSnapshot('ws1', 1, seedJobs(2))
      useJobStore.getState().failJobFetch('ws1', 'boom')
      expect(useJobStore.getState().jobs).toEqual([])
      expectConsistentDerivedState(useJobStore.getState())
    })
  })

  describe('paged loading', () => {
    it('setJobsPage stores the first page consistently', () => {
      useJobStore.getState().setJobsPage('ws1', 5, seedJobs(2), 3, 'cursor-1')
      expectConsistentDerivedState(useJobStore.getState())
    })

    it('loadMoreJobs appends the next page consistently', async () => {
      useJobStore.getState().setJobsPage('ws1', 5, seedJobs(2), 3, 'cursor-1')
      mockFetchJobsSnapshot.mockResolvedValueOnce(
        snapshotPage([makeWsJob('j3')], 5)
      )
      await useJobStore.getState().loadMoreJobs('ws1')
      expect(useJobStore.getState().jobIds).toEqual(['j1', 'j2', 'j3'])
      expectConsistentDerivedState(useJobStore.getState())
    })

    it('refreshFirstPage replaces the list consistently', async () => {
      useJobStore.getState().setJobsSnapshot('ws1', 1, seedJobs(2))
      mockFetchJobsSnapshot.mockResolvedValueOnce(
        snapshotPage([makeWsJob('j8'), makeWsJob('j9')], 2)
      )
      mockFetchJobFacets.mockResolvedValueOnce({
        workspace_id: 'ws1',
        total: 2,
        status_counts: {},
        version_counts: {},
        node_counts: {},
      })
      await useJobStore.getState().refreshFirstPage('ws1')
      expect(useJobStore.getState().jobIds).toEqual(['j8', 'j9'])
      expectConsistentDerivedState(useJobStore.getState())
    })
  })

  describe('batchDelete', () => {
    it('removes only succeeded jobs and stays consistent', async () => {
      useJobStore.getState().setJobsSnapshot('ws1', 1, seedJobs(3))
      useJobStore.setState({
        selectedIds: new Set(['j1', 'j2']),
        selectMode: true,
        refreshFirstPage: mockRefreshFirstPage,
      })
      mockRefreshFirstPage.mockResolvedValue(undefined)
      mockBatchDeleteJobs.mockResolvedValueOnce({
        results: [
          { job_id: 'j1', operation: 'delete', status: 'succeeded' },
          { job_id: 'j2', operation: 'delete', status: 'failed' },
        ],
      })
      await useJobStore.getState().batchDelete('ws1')
      expect(useJobStore.getState().jobIds).toEqual(['j2', 'j3'])
      expectConsistentDerivedState(useJobStore.getState())
    })
  })

  describe('setFilterConfig', () => {
    it('preserves consistency while narrowing the filtered view', () => {
      useJobStore
        .getState()
        .setJobsSnapshot('ws1', 1, [
          makeWsJob('j1', { status: 'pending' }),
          makeWsJob('j2', { status: 'completed' }),
        ])
      useJobStore.getState().setFilterConfig({ status: 'completed' })
      const state = useJobStore.getState()
      expect(state.filteredJobIds).toEqual(['j2'])
      expectConsistentDerivedState(state)
    })
  })
})
