import { describe, it, expect, vi, beforeEach } from 'vitest'
import { normalizeJobs, useJobStore } from './jobStore'
import { createMockUiState, makeJob } from '../testing/fixtures'

vi.mock('../api', () => ({
  api: vi.fn(),
}))

vi.mock('../api/jobApi', () => ({
  batchRerunJobs: vi.fn(),
  batchDeleteJobs: vi.fn(),
  clearJobsPackedStatus: vi.fn(),
  packageJobs: vi.fn(),
  runToJob: vi.fn(),
  continueJob: vi.fn(),
  batchRunToJobs: vi.fn(),
}))

vi.mock('./uiStore', () => ({
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
  runToJob,
  continueJob,
  batchRunToJobs,
} from '../api/jobApi'
import { useUiStore } from './uiStore'

const mockBatchRerunJobs = vi.mocked(batchRerunJobs)
const mockBatchDeleteJobs = vi.mocked(batchDeleteJobs)
const mockClearJobsPackedStatus = vi.mocked(clearJobsPackedStatus)
const mockPackageJobs = vi.mocked(packageJobs)
const mockRunToJob = vi.mocked(runToJob)
const mockContinueJob = vi.mocked(continueJob)
const mockBatchRunToJobs = vi.mocked(batchRunToJobs)
const mockShowToast = vi.fn()
const mockGetState = vi.mocked(useUiStore.getState)
const mockRefreshFirstPage = vi.fn()

describe('jobStore', () => {
  beforeEach(() => {
    useJobStore.setState({
      ...normalizeJobs([]),
      isLoading: false,
      error: null,
      selectedIds: new Set(),
      selectionMode: 'explicit',
      selectionFilter: null,
      excludedIds: new Set(),
      selectionCount: null,
      expandedId: null,
      filterConfig: {
        status: null,
        search: '',
        workflowVersion: null,
        activeNodeKey: null,
        paused: null,
      },
      batchRunToLoading: false,
      continueLoading: false,
      refreshFirstPage: mockRefreshFirstPage,
    })
    mockRefreshFirstPage.mockReset()
    mockRefreshFirstPage.mockResolvedValue(undefined)
    mockBatchRerunJobs.mockReset()
    mockBatchDeleteJobs.mockReset()
    mockClearJobsPackedStatus.mockReset()
    mockPackageJobs.mockReset()
    mockRunToJob.mockReset()
    mockContinueJob.mockReset()
    mockBatchRunToJobs.mockReset()
    mockShowToast.mockReset()
    mockGetState.mockReturnValue(
      createMockUiState({ showToast: mockShowToast })
    )
  })

  it('toggles selection', () => {
    useJobStore.getState().toggleSelect('j1')
    expect(useJobStore.getState().selectedIds.has('j1')).toBe(true)
    useJobStore.getState().toggleSelect('j1')
    expect(useJobStore.getState().selectedIds.has('j1')).toBe(false)
  })

  it('selects all matching jobs via the current filter', () => {
    useJobStore.setState(
      normalizeJobs([
        makeJob({ id: 'j1', status: 'pending' }),
        makeJob({ id: 'j2', status: 'completed', source_id: 'Q2' }),
      ])
    )
    useJobStore.getState().selectAll()
    expect(useJobStore.getState().selectionMode).toBe('allMatching')
    expect(useJobStore.getState().selectedIds.size).toBe(0)
  })

  it('filters by status', () => {
    useJobStore.setState(
      normalizeJobs([
        makeJob({ id: 'j1', status: 'pending' }),
        makeJob({ id: 'j2', status: 'completed', source_id: 'Q2' }),
      ])
    )
    useJobStore.getState().setFilterConfig({ status: 'completed' })
    const filtered = useJobStore.getState().getFilteredJobs()
    expect(filtered).toHaveLength(1)
    expect(filtered[0].id).toBe('j2')
  })

  it('filters by search query', () => {
    useJobStore.setState(
      normalizeJobs([
        makeJob({ id: 'j1', source_id: 'Q100', title: 'Algebra' }),
        makeJob({ id: 'j2', source_id: 'Q200', title: 'Geometry' }),
      ])
    )
    useJobStore.getState().setFilterConfig({ search: 'Q100' })
    const filtered = useJobStore.getState().getFilteredJobs()
    expect(filtered).toHaveLength(1)
    expect(filtered[0].id).toBe('j1')
  })

  it('clears selection when filter changes', () => {
    useJobStore.setState({ selectedIds: new Set(['j1', 'j2']) })
    useJobStore.getState().setFilterConfig({ status: 'completed' })
    expect(useJobStore.getState().selectedIds.size).toBe(0)
  })

  it('clears selection when search query changes', () => {
    useJobStore.setState({ selectedIds: new Set(['j1']) })
    useJobStore.getState().setFilterConfig({ search: 'foo' })
    expect(useJobStore.getState().selectedIds.size).toBe(0)
  })

  it('toggles expand between id and null', () => {
    useJobStore.getState().toggleExpand('j1')
    expect(useJobStore.getState().expandedId).toBe('j1')
    useJobStore.getState().toggleExpand('j1')
    expect(useJobStore.getState().expandedId).toBeNull()
  })

  it('calls batch rerun endpoint and clears the selection afterwards', async () => {
    useJobStore.setState({
      ...normalizeJobs([
        makeJob({ id: 'j1', status: 'failed' }),
        makeJob({ id: 'j2', status: 'failed' }),
      ]),
      selectedIds: new Set(['j1', 'j2']),
      selectMode: true,
    })
    mockBatchRerunJobs.mockResolvedValueOnce({
      results: [
        { job_id: 'j1', operation: 'rerun', status: 'succeeded' },
        { job_id: 'j2', operation: 'rerun', status: 'skipped' },
      ],
    })

    await useJobStore.getState().batchRerun('ws1', 'extract')

    expect(mockBatchRerunJobs).toHaveBeenCalledWith(
      'ws1',
      'extract',
      { jobIds: ['j1', 'j2'] },
      { fromFailedNode: undefined }
    )
    expect(useJobStore.getState().selectedIds.size).toBe(0)
    expect(useJobStore.getState().selectMode).toBe(true)
    expect(mockRefreshFirstPage).toHaveBeenCalledWith('ws1')
    expect(mockShowToast).toHaveBeenCalledWith(
      '重跑完成：成功 1 项，跳过 1 项',
      'success'
    )
  })

  it('surfaces 404 on batch rerun', async () => {
    useJobStore.setState({ selectedIds: new Set(['j1']) })
    const err = Object.assign(new Error('Not Found'), { status: 404 })
    mockBatchRerunJobs.mockRejectedValueOnce(err)

    await expect(
      useJobStore.getState().batchRerun('ws1', 'extract')
    ).rejects.toThrow('Not Found')

    expect(useJobStore.getState().selectedIds.size).toBe(1)
    expect(useJobStore.getState().error).toBe('Not Found')
    expect(mockShowToast).toHaveBeenCalledWith('Not Found', 'error')
  })

  it('shows accurate toast counts on batch rerun failure', async () => {
    useJobStore.setState({
      ...normalizeJobs([makeJob({ id: 'j1', status: 'failed' })]),
      selectedIds: new Set(['j1']),
    })
    mockBatchRerunJobs.mockResolvedValueOnce({
      results: [{ job_id: 'j1', operation: 'rerun', status: 'failed' }],
    })

    await useJobStore.getState().batchRerun('ws1', 'extract')

    expect(mockShowToast).toHaveBeenCalledWith(
      '重跑完成：成功 0 项，跳过 0 项，失败 1 项',
      'error'
    )
  })

  it('calls batch delete endpoint and removes only succeeded jobs', async () => {
    useJobStore.setState({
      ...normalizeJobs([
        makeJob({ id: 'j1', status: 'completed' }),
        makeJob({ id: 'j2', status: 'completed', source_id: 'Q2' }),
        makeJob({ id: 'j3', status: 'completed', source_id: 'Q3' }),
      ]),
      selectedIds: new Set(['j1', 'j2', 'j3']),
      selectMode: true,
    })
    mockBatchDeleteJobs.mockResolvedValueOnce({
      results: [
        { job_id: 'j1', operation: 'delete', status: 'succeeded' },
        { job_id: 'j2', operation: 'delete', status: 'failed' },
        { job_id: 'j3', operation: 'delete', status: 'skipped' },
      ],
    })

    await useJobStore.getState().batchDelete('ws1')

    expect(mockBatchDeleteJobs).toHaveBeenCalledWith('ws1', {
      jobIds: ['j1', 'j2', 'j3'],
    })
    expect(useJobStore.getState().jobs).toHaveLength(2)
    expect(useJobStore.getState().jobs.some((j) => j.id === 'j1')).toBe(false)
    expect(useJobStore.getState().selectedIds.size).toBe(0)
    expect(useJobStore.getState().selectMode).toBe(true)
    expect(mockRefreshFirstPage).toHaveBeenCalledWith('ws1')
    expect(mockShowToast).toHaveBeenCalledWith(
      '删除完成：成功 1 项，跳过 1 项，失败 1 项',
      'error'
    )
  })

  it('surfaces 404 on batch delete', async () => {
    useJobStore.setState({ selectedIds: new Set(['j1']) })
    const err = Object.assign(new Error('Not Found'), { status: 404 })
    mockBatchDeleteJobs.mockRejectedValueOnce(err)

    await expect(useJobStore.getState().batchDelete('ws1')).rejects.toThrow(
      'Not Found'
    )

    expect(useJobStore.getState().selectedIds.size).toBe(1)
    expect(useJobStore.getState().error).toBe('Not Found')
    expect(mockShowToast).toHaveBeenCalledWith('Not Found', 'error')
  })

  it('opens package download URL and clears succeeded jobs from selection', async () => {
    useJobStore.setState({
      ...normalizeJobs([
        makeJob({ id: 'j1', status: 'completed' }),
        makeJob({ id: 'j2', status: 'completed', source_id: 'Q2' }),
      ]),
      selectedIds: new Set(['j1', 'j2']),
      selectMode: true,
    })
    mockPackageJobs.mockResolvedValueOnce({
      download_url: '/api/workspaces/ws1/packages/pkg.zip',
      package_filename: 'pkg.zip',
      succeeded_count: 1,
      failed_count: 1,
      results: [
        { job_id: 'j1', status: 'succeeded' },
        { job_id: 'j2', status: 'failed' },
      ],
    })

    const result = await useJobStore.getState().batchPackage('ws1')

    expect(mockPackageJobs).toHaveBeenCalledWith('ws1', {
      jobIds: ['j1', 'j2'],
    })
    expect(result.download_url).toBe('/api/workspaces/ws1/packages/pkg.zip')
    expect(useJobStore.getState().selectedIds.size).toBe(0)
    expect(useJobStore.getState().selectMode).toBe(true)
    expect(mockShowToast).toHaveBeenCalledWith(
      '打包完成：成功 1 项，失败 1 项',
      'error'
    )
  })

  it('sends every selected job to package eligibility evaluation', async () => {
    useJobStore.setState({
      ...normalizeJobs([
        makeJob({ id: 'j1', status: 'completed' }),
        makeJob({ id: 'j2', status: 'running', source_id: 'Q2' }),
      ]),
      selectedIds: new Set(['j1', 'j2']),
      selectMode: true,
    })
    mockPackageJobs.mockResolvedValueOnce({
      download_url: '/api/workspaces/ws1/packages/pkg.zip',
      package_filename: 'pkg.zip',
      succeeded_count: 1,
      failed_count: 1,
      results: [
        { job_id: 'j1', status: 'succeeded' },
        { job_id: 'j2', status: 'failed', reason_code: 'not_completed' },
      ],
    })

    await useJobStore.getState().batchPackage('ws1')

    expect(mockPackageJobs).toHaveBeenCalledWith('ws1', {
      jobIds: ['j1', 'j2'],
    })
    expect(mockShowToast).toHaveBeenCalledWith(
      '打包完成：成功 1 项，失败 1 项',
      'error'
    )
  })

  it('clears packed status, refreshes the first page and clears selection', async () => {
    useJobStore.setState({
      ...normalizeJobs([
        makeJob({ id: 'j1', status: 'completed', packed: 1 }),
        makeJob({ id: 'j2', status: 'completed', packed: 1 }),
      ]),
      selectedIds: new Set(['j1', 'j2']),
      selectMode: true,
    })
    mockClearJobsPackedStatus.mockResolvedValueOnce({
      succeeded_count: 2,
      failed_count: 0,
      results: [
        { job_id: 'j1', status: 'succeeded' },
        { job_id: 'j2', status: 'succeeded' },
      ],
    })

    await useJobStore.getState().batchClearPacked('ws1')

    expect(mockClearJobsPackedStatus).toHaveBeenCalledWith('ws1', {
      jobIds: ['j1', 'j2'],
    })
    expect(mockRefreshFirstPage).toHaveBeenCalledWith('ws1')
    expect(useJobStore.getState().selectedIds).toEqual(new Set())
    expect(mockShowToast).toHaveBeenCalledWith(
      '已清空打包状态：成功 2 项，失败 0 项',
      'success'
    )
  })

  it('does nothing when batch actions are invoked with empty selection', async () => {
    await useJobStore.getState().batchRerun('ws1', 'extract')
    await useJobStore.getState().batchDelete('ws1')
    await useJobStore.getState().batchPackage('ws1')
    await useJobStore.getState().batchClearPacked('ws1')
    expect(mockBatchRerunJobs).not.toHaveBeenCalled()
    expect(mockBatchDeleteJobs).not.toHaveBeenCalled()
    expect(mockPackageJobs).not.toHaveBeenCalled()
    expect(mockClearJobsPackedStatus).not.toHaveBeenCalled()
  })

  it('exits select mode when batch rerun succeeds for all selected jobs', async () => {
    useJobStore.setState({
      ...normalizeJobs([makeJob({ id: 'j1', status: 'failed' })]),
      selectedIds: new Set(['j1']),
      selectMode: true,
    })
    mockBatchRerunJobs.mockResolvedValueOnce({
      results: [{ job_id: 'j1', operation: 'rerun', status: 'succeeded' }],
    })

    await useJobStore.getState().batchRerun('ws1', 'extract')

    expect(useJobStore.getState().selectedIds.size).toBe(0)
    expect(useJobStore.getState().selectMode).toBe(false)
  })

  it('exits select mode when batch delete succeeds for all selected jobs', async () => {
    useJobStore.setState({
      ...normalizeJobs([makeJob({ id: 'j1', status: 'failed' })]),
      selectedIds: new Set(['j1']),
      selectMode: true,
    })
    mockBatchDeleteJobs.mockResolvedValueOnce({
      results: [{ job_id: 'j1', operation: 'delete', status: 'succeeded' }],
    })

    await useJobStore.getState().batchDelete('ws1')

    expect(useJobStore.getState().selectedIds.size).toBe(0)
    expect(useJobStore.getState().selectMode).toBe(false)
  })

  it('exits select mode when batch package succeeds for all selected jobs', async () => {
    useJobStore.setState({
      ...normalizeJobs([makeJob({ id: 'j1', status: 'completed' })]),
      selectedIds: new Set(['j1']),
      selectMode: true,
    })
    mockPackageJobs.mockResolvedValueOnce({
      download_url: '/api/workspaces/ws1/packages/pkg.zip',
      package_filename: 'pkg.zip',
      succeeded_count: 1,
      failed_count: 0,
      results: [{ job_id: 'j1', status: 'succeeded' }],
    })

    await useJobStore.getState().batchPackage('ws1')

    expect(useJobStore.getState().selectedIds.size).toBe(0)
    expect(useJobStore.getState().selectMode).toBe(false)
  })

  it('calls batch run-to endpoint and clears succeeded jobs from selection', async () => {
    useJobStore.setState({
      ...normalizeJobs([makeJob({ id: 'j1', status: 'failed' })]),
      selectedIds: new Set(['j1']),
      selectMode: true,
    })
    mockBatchRunToJobs.mockResolvedValueOnce({
      results: [{ job_id: 'j1', operation: 'run_to', status: 'succeeded' }],
    })

    await useJobStore.getState().batchRunTo('ws1', 'review', 'extract')

    expect(mockBatchRunToJobs).toHaveBeenCalledWith(
      'ws1',
      'review',
      { jobIds: ['j1'] },
      'extract'
    )
    expect(useJobStore.getState().selectedIds.size).toBe(0)
    expect(useJobStore.getState().selectMode).toBe(false)
    expect(mockShowToast).toHaveBeenCalledWith(
      '运行到完成：成功 1 项',
      'success'
    )
  })

  it('clears the selection after batch run-to partial results', async () => {
    useJobStore.setState({
      ...normalizeJobs([
        makeJob({ id: 'j1', status: 'failed' }),
        makeJob({ id: 'j2', status: 'failed', source_id: 'Q2' }),
      ]),
      selectedIds: new Set(['j1', 'j2']),
      selectMode: true,
    })
    mockBatchRunToJobs.mockResolvedValueOnce({
      results: [
        { job_id: 'j1', operation: 'run_to', status: 'succeeded' },
        { job_id: 'j2', operation: 'run_to', status: 'skipped' },
      ],
    })

    await useJobStore.getState().batchRunTo('ws1', 'review')

    expect(useJobStore.getState().selectedIds.size).toBe(0)
    expect(useJobStore.getState().selectMode).toBe(true)
  })

  it('shows accurate toast counts on batch run-to failure', async () => {
    useJobStore.setState({
      ...normalizeJobs([makeJob({ id: 'j1', status: 'failed' })]),
      selectedIds: new Set(['j1']),
      selectMode: true,
    })
    mockBatchRunToJobs.mockResolvedValueOnce({
      results: [{ job_id: 'j1', operation: 'run_to', status: 'failed' }],
    })

    await useJobStore.getState().batchRunTo('ws1', 'review')

    expect(mockShowToast).toHaveBeenCalledWith(
      '运行到完成：成功 0 项，跳过 0 项，失败 1 项',
      'error'
    )
  })

  it('refreshes the first page immediately after batch run-to', async () => {
    useJobStore.setState({
      ...normalizeJobs([makeJob({ id: 'j1', status: 'failed' })]),
      selectedIds: new Set(['j1']),
      selectMode: true,
    })
    mockBatchRunToJobs.mockResolvedValueOnce({
      results: [{ job_id: 'j1', operation: 'run_to', status: 'succeeded' }],
    })

    await useJobStore.getState().batchRunTo('ws1', 'review')

    expect(mockRefreshFirstPage).toHaveBeenCalledWith('ws1')
  })

  it('does nothing when batch run-to is invoked with empty selection', async () => {
    await useJobStore.getState().batchRunTo('ws1', 'review')
    expect(mockBatchRunToJobs).not.toHaveBeenCalled()
  })

  it('calls continue endpoint for a paused target-reached job', async () => {
    mockContinueJob.mockResolvedValueOnce({
      job_id: 'j1',
      operation: 'continue',
      status: 'succeeded',
    })

    const result = await useJobStore.getState().continueJob('j1')

    expect(mockContinueJob).toHaveBeenCalledWith('j1')
    expect(result.status).toBe('succeeded')
    expect(mockShowToast).toHaveBeenCalledWith('继续完整流程成功', 'success')
  })

  it('surfaces continue endpoint errors', async () => {
    mockContinueJob.mockRejectedValueOnce(new Error('not paused'))

    await expect(useJobStore.getState().continueJob('j1')).rejects.toThrow(
      'not paused'
    )

    expect(mockShowToast).toHaveBeenCalledWith('not paused', 'error')
  })
})
