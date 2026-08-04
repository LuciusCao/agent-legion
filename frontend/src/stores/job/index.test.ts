import { vi, describe, it, expect, beforeEach } from 'vitest'
import { useJobStore } from './index'
import { createJobSummary } from './actions/testHelpers'
import { createOptionAccumulator } from './filterLogic/optionAccumulator'
import { upgradeJobWorkflow } from '../../api/jobWorkflowUpgradeApi'

vi.mock('../../api/jobWorkflowUpgradeApi', () => ({
  upgradeJobWorkflow: vi.fn(),
}))

describe('useJobStore', () => {
  it('exposes resetForWorkspace', () => {
    expect(typeof useJobStore.getState().resetForWorkspace).toBe('function')
  })

  describe('normalized job state', () => {
    beforeEach(() => {
      useJobStore.setState({
        jobs: [],
        jobsById: {},
        jobIds: [],
        revision: 0,
        optionAccumulator: createOptionAccumulator([]),
        jobsWorkspaceId: null,
        isLoading: false,
        error: null,
        selectedIds: new Set(),
        expandedId: null,
      })
    })

    it('normalizes jobs after snapshot load', () => {
      const store = useJobStore.getState()
      store.setJobsSnapshot('ws1', 7, [
        createJobSummary({ id: 'j1', workspace_id: 'ws1', status: 'pending' }),
        createJobSummary({ id: 'j2', workspace_id: 'ws1', status: 'running' }),
      ])

      const state = useJobStore.getState()
      expect(state.jobsById.j1.status).toBe('pending')
      expect(state.jobsById.j2.status).toBe('running')
      expect(state.jobIds).toEqual(['j1', 'j2'])
      expect(state.revision).toBe(7)
    })

    it('appends later snapshot pages without replacing existing jobs', () => {
      const store = useJobStore.getState()
      store.setJobsSnapshot('ws1', 7, [
        createJobSummary({ id: 'j1', workspace_id: 'ws1', status: 'pending' }),
      ])
      store.appendJobsSnapshot('ws1', [
        createJobSummary({ id: 'j2', workspace_id: 'ws1', status: 'running' }),
      ])

      const state = useJobStore.getState()
      expect(state.jobsById.j1.status).toBe('pending')
      expect(state.jobsById.j2.status).toBe('running')
      expect(state.jobIds).toEqual(['j1', 'j2'])
    })

    it('merges patch jobs without replacing unchanged jobs', () => {
      const store = useJobStore.getState()
      store.setJobsSnapshot('ws1', 1, [
        createJobSummary({ id: 'j1', workspace_id: 'ws1', status: 'pending' }),
        createJobSummary({ id: 'j2', workspace_id: 'ws1', status: 'pending' }),
      ])

      store.applyJobPatchBatch(
        'ws1',
        2,
        [
          createJobSummary({
            id: 'j2',
            workspace_id: 'ws1',
            status: 'running',
          }),
        ],
        []
      )

      const state = useJobStore.getState()
      expect(state.jobsById.j1.status).toBe('pending')
      expect(state.jobsById.j2.status).toBe('running')
      expect(state.jobIds).toEqual(['j1', 'j2'])
      expect(state.revision).toBe(2)
    })

    it('updates jobs immutably on update-only patches without rebuilding unchanged job objects', () => {
      const store = useJobStore.getState()
      store.setJobsSnapshot('ws1', 1, [
        createJobSummary({ id: 'j1', workspace_id: 'ws1', status: 'pending' }),
        createJobSummary({ id: 'j2', workspace_id: 'ws1', status: 'pending' }),
      ])

      const beforeJobs = useJobStore.getState().jobs
      const beforeJ2 = useJobStore.getState().jobsById.j2

      store.applyJobPatchBatch(
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

      const state = useJobStore.getState()
      expect(state.jobs).not.toBe(beforeJobs)
      expect(state.jobs[0].status).toBe('running')
      expect(state.jobs[1]).toBe(beforeJ2)
      expect(state.jobIndexById).toEqual({ j1: 0, j2: 1 })
    })
  })

  it('resets state for a workspace', () => {
    useJobStore.setState({
      jobs: [createJobSummary({ id: 'j1', workspace_id: 'ws1' })],
      jobsWorkspaceId: 'ws1',
      isLoading: false,
      selectedIds: new Set(['j1']),
    })

    useJobStore.getState().resetForWorkspace('ws2')

    const state = useJobStore.getState()
    expect(state.jobs).toEqual([])
    expect(state.isLoading).toBe(true)
    expect(state.jobsWorkspaceId).toBe('ws2')
    expect(state.selectedIds).toEqual(new Set())
  })
})

describe('useJobStore batchUpgradeWorkflow', () => {
  beforeEach(() => {
    const jobs = [
      createJobSummary({
        id: 'j1',
        status: 'completed',
        is_workflow_outdated: true,
      }),
      createJobSummary({
        id: 'j2',
        status: 'pending',
        is_workflow_outdated: true,
      }),
    ]
    useJobStore.setState({
      jobs,
      optionAccumulator: createOptionAccumulator(jobs),
      selectedIds: new Set(['j1', 'j2']),
      selectionMode: 'explicit' as const,
      selectionFilter: null,
      excludedIds: new Set<string>(),
      selectionCount: null,
      jobsWorkspaceId: 'ws1',
      refreshFirstPage: vi.fn(),
    })
    vi.mocked(upgradeJobWorkflow).mockReset()
  })

  it('calls upgrade for each provided job id and removes succeeded from selection', async () => {
    vi.mocked(upgradeJobWorkflow)
      .mockResolvedValueOnce({
        job_id: 'j1',
        operation: 'upgrade_workflow',
        status: 'succeeded',
      })
      .mockResolvedValueOnce({
        job_id: 'j2',
        operation: 'upgrade_workflow',
        status: 'succeeded',
      })

    const result = await useJobStore
      .getState()
      .batchUpgradeWorkflow('ws1', ['j1', 'j2'])

    expect(upgradeJobWorkflow).toHaveBeenCalledTimes(2)
    expect(upgradeJobWorkflow).toHaveBeenNthCalledWith(1, 'j1')
    expect(upgradeJobWorkflow).toHaveBeenNthCalledWith(2, 'j2')
    expect(result.results).toHaveLength(2)
    expect(useJobStore.getState().selectedIds).toEqual(new Set())
    expect(useJobStore.getState().selectMode).toBe(false)
  })

  it('reports per-job failures and clears the selection afterwards', async () => {
    vi.mocked(upgradeJobWorkflow)
      .mockResolvedValueOnce({
        job_id: 'j1',
        operation: 'upgrade_workflow',
        status: 'succeeded',
      })
      .mockResolvedValueOnce({
        job_id: 'j2',
        operation: 'upgrade_workflow',
        status: 'failed',
        message: 'boom',
      })

    const result = await useJobStore
      .getState()
      .batchUpgradeWorkflow('ws1', ['j1', 'j2'])

    expect(result.results[1].status).toBe('failed')
    expect(useJobStore.getState().selectedIds).toEqual(new Set())
  })
})
