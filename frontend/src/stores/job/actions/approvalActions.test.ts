import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useJobStore } from '../index'
import { batchApprovalJobs } from '../../../api/jobApprovalApi'
import type { JobBatchOperation } from '../../../api/jobBatchOperationsApi'
import { createMockUiState } from '../../../testing/fixtures'
import { useTaskCenterStore } from '../../taskCenterStore'
import { useUiStore } from '../../uiStore'

vi.mock('../../../api/jobApprovalApi', () => ({
  batchApprovalJobs: vi.fn(),
}))
vi.mock('../../uiStore', () => ({
  useUiStore: { getState: vi.fn(), setState: vi.fn() },
}))

const mockBatchApprovalJobs = vi.mocked(batchApprovalJobs)
const mockShowToast = vi.fn()
const mockRefreshFirstPage = vi.fn()

describe('batch approval actions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useJobStore.setState({
      selectedIds: new Set(['j1', 'j2']),
      selectionMode: 'explicit',
      selectionFilter: null,
      excludedIds: new Set(),
      selectionCount: null,
      selectMode: true,
      batchApprovalLoading: false,
      refreshFirstPage: mockRefreshFirstPage,
    })
    mockRefreshFirstPage.mockResolvedValue(undefined)
    vi.mocked(useUiStore.getState).mockReturnValue(
      createMockUiState({ showToast: mockShowToast })
    )
    useTaskCenterStore.getState().clearAcceptedNotice()
  })

  it('submits synchronous approvals, refreshes and clears the selection', async () => {
    mockBatchApprovalJobs.mockResolvedValue({
      results: [
        { job_id: 'j1', operation: 'approve', status: 'succeeded' },
        {
          job_id: 'j2',
          operation: 'approve',
          status: 'skipped',
          reason_code: 'multiple_awaiting_gates',
        },
      ],
    })

    await useJobStore.getState().batchApproval('ws1', 'approve')

    expect(mockBatchApprovalJobs).toHaveBeenCalledWith('ws1', 'approve', {
      jobIds: ['j1', 'j2'],
    })
    expect(mockShowToast).toHaveBeenCalledWith(
      '通过完成：成功 1 项，跳过 1 项',
      'success'
    )
    expect(useJobStore.getState().selectedIds.size).toBe(0)
    expect(mockRefreshFirstPage).toHaveBeenCalledWith('ws1')
    expect(useJobStore.getState().batchApprovalLoading).toBe(false)
  })

  it('routes accepted rejections to the task center', async () => {
    const operation = {
      id: 'op1',
      kind: 'reject',
      items_total: 1200,
    } as JobBatchOperation
    mockBatchApprovalJobs.mockResolvedValue({ operation })

    await useJobStore.getState().batchApproval('ws1', 'reject')

    expect(useTaskCenterStore.getState().acceptedNotice).toMatchObject({
      workspaceId: 'ws1',
      opId: 'op1',
      opKind: 'reject',
      itemsTotal: 1200,
    })
    expect(mockShowToast).not.toHaveBeenCalled()
    expect(mockRefreshFirstPage).not.toHaveBeenCalled()
  })

  it('does not submit an empty selection', async () => {
    useJobStore.setState({ selectedIds: new Set() })
    await useJobStore.getState().batchApproval('ws1', 'approve')
    expect(mockBatchApprovalJobs).not.toHaveBeenCalled()
    expect(useJobStore.getState().batchApprovalLoading).toBe(false)
  })

  it('restores loading and surfaces request failures', async () => {
    mockBatchApprovalJobs.mockRejectedValue(new Error('network down'))
    await expect(
      useJobStore.getState().batchApproval('ws1', 'reject')
    ).rejects.toThrow('network down')
    expect(mockShowToast).toHaveBeenCalledWith('network down', 'error')
    expect(useJobStore.getState().batchApprovalLoading).toBe(false)
  })
})
