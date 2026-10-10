import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  mergeWorkspaceEventStats,
  refreshWorkspaceEvents,
} from './workspaceEventRefresh'
import { createJobSummary, useJobStore } from '../stores/jobStore'
import { createTestQueryClient } from '../testing/testQueryClient'
import { queryKeys } from '../lib/queryKeys'

describe('mergeWorkspaceEventStats', () => {
  it('merges job_stats into the cached workspace stats', () => {
    const queryClient = createTestQueryClient()
    queryClient.setQueryData(queryKeys.workspaceStats('ws1'), {
      workspace_id: 'ws1',
      name: 'WS One',
      job_stats: { pending: 1 },
    })

    mergeWorkspaceEventStats(queryClient, 'ws1', { pending: 2, running: 3 })

    expect(queryClient.getQueryData(queryKeys.workspaceStats('ws1'))).toEqual({
      workspace_id: 'ws1',
      name: 'WS One',
      job_stats: { pending: 2, running: 3 },
    })
  })
})

describe('refreshWorkspaceEvents', () => {
  beforeEach(() => {
    useJobStore.setState({
      jobs: [createJobSummary({ id: 'j1', workspace_id: 'ws1' })],
      jobIds: ['j1'],
      jobsById: { j1: createJobSummary({ id: 'j1', workspace_id: 'ws1' }) },
      jobsWorkspaceId: 'ws1',
      isLoading: false,
      listLoadError: null,
    })
  })

  it('swallows stats refresh failure without touching the job list (#1183)', async () => {
    // #1183：stats 失效/重取失败只落在 stats 查询自身的错误状态。此前这里
    // 会调 failJobFetch 清空任务列表——配合 refetchOnWindowFocus 把后端
    // 瞬时故障放大成整页列表销毁，再被渲染成「开始使用 Workspace」引导。
    const queryClient = createTestQueryClient()
    const invalidate = vi
      .spyOn(queryClient, 'invalidateQueries')
      .mockRejectedValue(new Error('backend down'))

    await expect(
      refreshWorkspaceEvents(queryClient, 'ws1', () => false)
    ).resolves.toBeUndefined()

    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryKeys.workspaceStats('ws1'),
    })
    const state = useJobStore.getState()
    expect(state.jobIds).toEqual(['j1'])
    expect(state.listLoadError).toBeNull()
  })

  it('skips the refresh when the caller reports inactive', async () => {
    const queryClient = createTestQueryClient()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')

    await refreshWorkspaceEvents(queryClient, 'ws1', () => true)

    expect(invalidate).not.toHaveBeenCalled()
  })
})
