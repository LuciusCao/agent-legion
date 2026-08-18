import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useJobStore } from '../index'
import { createMockUiState } from '../../../testing/fixtures'
import { normalizeJobs } from './fetch'
import type { JobListFilterParams } from '../../../types/jobTypes'
import { createJobSummary } from './testHelpers'

vi.mock('../../../api/jobApi', () => ({
  batchRerunJobs: vi.fn(),
  batchDeleteJobs: vi.fn(),
  clearJobsPackedStatus: vi.fn(),
  packageJobs: vi.fn(),
  runToJob: vi.fn(),
  continueJob: vi.fn(),
  batchRunToJobs: vi.fn(),
}))

vi.mock('../../../api/failureApi', () => ({
  rerunJobsByFailure: vi.fn(),
}))

vi.mock('../../../api/jobWorkflowUpgradeApi', () => ({
  upgradeJobWorkflow: vi.fn(),
}))

vi.mock('../../../api/jobBatchUpgradeWorkflowApi', () => ({
  batchUpgradeJobsWorkflow: vi.fn(),
}))

vi.mock('../../uiStore', () => ({
  useUiStore: {
    getState: vi.fn(),
    setState: vi.fn(),
  },
}))

import {
  batchRerunJobs,
  batchDeleteJobs,
  clearJobsPackedStatus,
  packageJobs,
  batchRunToJobs,
} from '../../../api/jobApi'
import { rerunJobsByFailure } from '../../../api/failureApi'
import { upgradeJobWorkflow } from '../../../api/jobWorkflowUpgradeApi'
import { batchUpgradeJobsWorkflow } from '../../../api/jobBatchUpgradeWorkflowApi'
import { useUiStore } from '../../uiStore'

const mockBatchRerunJobs = vi.mocked(batchRerunJobs)
const mockBatchDeleteJobs = vi.mocked(batchDeleteJobs)
const mockClearJobsPackedStatus = vi.mocked(clearJobsPackedStatus)
const mockPackageJobs = vi.mocked(packageJobs)
const mockBatchRunToJobs = vi.mocked(batchRunToJobs)
const mockRerunJobsByFailure = vi.mocked(rerunJobsByFailure)
const mockBatchUpgradeJobsWorkflow = vi.mocked(batchUpgradeJobsWorkflow)
const mockUpgradeJobWorkflow = vi.mocked(upgradeJobWorkflow)
const mockRefreshFirstPage = vi.fn()

const SELECTION_FILTER: JobListFilterParams = {
  status: 'failed',
  search: null,
  workflow_version: null,
  workflow_version_none: false,
  active_node_key: null,
}

function enterAllMatching() {
  useJobStore.setState({
    selectionMode: 'allMatching',
    selectionFilter: SELECTION_FILTER,
    excludedIds: new Set(['j9']),
    selectedIds: new Set(),
    selectionCount: 10,
  })
}

const mutationOk = {
  results: [
    {
      job_id: 'j1',
      operation: 'rerun' as const,
      status: 'succeeded' as const,
    },
  ],
}

describe('batch actions in allMatching selection mode', () => {
  beforeEach(() => {
    useJobStore.setState({
      ...normalizeJobs([createJobSummary({ id: 'j1', status: 'failed' })]),
      jobsWorkspaceId: 'ws1',
      isLoading: false,
      error: null,
      selectedIds: new Set(),
      selectionMode: 'explicit',
      selectionFilter: null,
      excludedIds: new Set(),
      selectionCount: null,
      refreshFirstPage: mockRefreshFirstPage,
    })
    vi.clearAllMocks()
    mockRefreshFirstPage.mockResolvedValue(undefined)
    vi.mocked(useUiStore.getState).mockReturnValue(
      createMockUiState({ showToast: vi.fn() })
    )
    mockBatchRerunJobs.mockResolvedValue(mutationOk)
    mockBatchDeleteJobs.mockResolvedValue(mutationOk)
    mockPackageJobs.mockResolvedValue({
      download_url: null,
      package_filename: null,
      succeeded_count: 1,
      failed_count: 0,
      results: [],
    })
    mockClearJobsPackedStatus.mockResolvedValue({
      succeeded_count: 1,
      failed_count: 0,
      results: [],
    })
    mockBatchRunToJobs.mockResolvedValue(mutationOk)
    mockRerunJobsByFailure.mockResolvedValue({ results: [] })
    mockBatchUpgradeJobsWorkflow.mockResolvedValue(mutationOk)
    mockUpgradeJobWorkflow.mockResolvedValue({
      job_id: 'j1',
      operation: 'upgrade_workflow',
      status: 'succeeded',
    })
  })

  it('batchRerun sends the filter payload with exclusions', async () => {
    enterAllMatching()

    await useJobStore.getState().batchRerun('ws1', null, true)

    expect(mockBatchRerunJobs).toHaveBeenCalledWith(
      'ws1',
      null,
      { filter: SELECTION_FILTER, excludeIds: ['j9'] },
      { fromFailedNode: true }
    )
  })

  it('batchRerun prefers explicit job ids over the selection filter', async () => {
    enterAllMatching()

    await useJobStore.getState().batchRerun('ws1', 'extract', false, ['j1'])

    expect(mockBatchRerunJobs).toHaveBeenCalledWith(
      'ws1',
      'extract',
      { jobIds: ['j1'] },
      { fromFailedNode: false }
    )
  })

  it('batchDelete sends the filter payload with exclusions', async () => {
    enterAllMatching()

    await useJobStore.getState().batchDelete('ws1')

    expect(mockBatchDeleteJobs).toHaveBeenCalledWith('ws1', {
      filter: SELECTION_FILTER,
      excludeIds: ['j9'],
    })
  })

  it('batchPackage sends the filter payload with exclusions', async () => {
    enterAllMatching()

    await useJobStore.getState().batchPackage('ws1')

    expect(mockPackageJobs).toHaveBeenCalledWith('ws1', {
      filter: SELECTION_FILTER,
      excludeIds: ['j9'],
    })
  })

  it('batchClearPacked sends the filter payload with exclusions', async () => {
    enterAllMatching()

    await useJobStore.getState().batchClearPacked('ws1')

    expect(mockClearJobsPackedStatus).toHaveBeenCalledWith('ws1', {
      filter: SELECTION_FILTER,
      excludeIds: ['j9'],
    })
  })

  it('batchRunTo sends the filter payload with exclusions', async () => {
    enterAllMatching()

    await useJobStore.getState().batchRunTo('ws1', 'review')

    expect(mockBatchRunToJobs).toHaveBeenCalledWith(
      'ws1',
      'review',
      { filter: SELECTION_FILTER, excludeIds: ['j9'] },
      undefined
    )
  })

  it('batchUpgradeWorkflow sends the filter payload with exclusions', async () => {
    enterAllMatching()

    await useJobStore.getState().batchUpgradeWorkflow('ws1')

    expect(mockBatchUpgradeJobsWorkflow).toHaveBeenCalledWith('ws1', {
      filter: SELECTION_FILTER,
      excludeIds: ['j9'],
    })
    expect(mockRefreshFirstPage).toHaveBeenCalledWith('ws1')
    expect(useJobStore.getState().selectionMode).toBe('explicit')
  })

  it('batchUpgradeWorkflow keeps per-job calls for explicit ids', async () => {
    enterAllMatching()

    await useJobStore.getState().batchUpgradeWorkflow('ws1', ['j1'])

    expect(mockBatchUpgradeJobsWorkflow).not.toHaveBeenCalled()
    expect(mockUpgradeJobWorkflow).toHaveBeenCalledWith('j1')
  })

  it('rerunByFailureCategory sends the filter payload with exclusions', async () => {
    enterAllMatching()

    await useJobStore.getState().rerunByFailureCategory('ws1', {
      category: 'technical',
    })

    expect(mockRerunJobsByFailure).toHaveBeenCalledWith('ws1', {
      category: 'technical',
      strategy: 'auto',
      filter: SELECTION_FILTER,
      exclude_ids: ['j9'],
    })
  })

  it('refreshes the first page and clears the selection after success', async () => {
    enterAllMatching()

    await useJobStore.getState().batchDelete('ws1')

    expect(mockRefreshFirstPage).toHaveBeenCalledWith('ws1')
    const state = useJobStore.getState()
    expect(state.selectionMode).toBe('explicit')
    expect(state.selectionFilter).toBeNull()
    expect(state.excludedIds).toEqual(new Set())
  })

  it('keeps the selection when the operation fails', async () => {
    enterAllMatching()
    mockBatchDeleteJobs.mockRejectedValueOnce(new Error('boom'))

    await expect(useJobStore.getState().batchDelete('ws1')).rejects.toThrow(
      'boom'
    )

    expect(mockRefreshFirstPage).not.toHaveBeenCalled()
    expect(useJobStore.getState().selectionMode).toBe('allMatching')
  })

  it('explicit mode still sends job id lists', async () => {
    useJobStore.setState({ selectedIds: new Set(['j1']) })

    await useJobStore.getState().batchDelete('ws1')

    expect(mockBatchDeleteJobs).toHaveBeenCalledWith('ws1', {
      jobIds: ['j1'],
    })
  })
})
