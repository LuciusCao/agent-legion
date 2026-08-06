import type { QueryClient } from '@tanstack/react-query'
import { useJobStore } from '../stores/jobStore'
import { queryKeys } from '../lib/queryKeys'
import type { WorkspaceStats } from '../types/workspaceTypes'

// 浅合并事件携带的 job_stats：只替换 job_stats，保留 workflow_key 等其他
// 字段；old 为 undefined 时与原 store 展开 undefined 的行为一致（cast 是
// 因为展开 undefined 会让必填字段变可选）。
export function mergeWorkspaceEventStats(
  queryClient: QueryClient,
  workspaceId: string,
  stats: Record<string, number>
) {
  queryClient.setQueryData<WorkspaceStats | undefined>(
    queryKeys.workspaceStats(workspaceId),
    (old) => ({ ...old, job_stats: stats }) as WorkspaceStats
  )
}

// 失效该 workspace 的 stats 查询，由活跃观察者触发 refetch（无观察者时不
// 发请求）；throwOnError 让 refetch 失败继续走 failJobFetch 副作用。
export async function refreshWorkspaceEvents(
  queryClient: QueryClient,
  workspaceId: string,
  isInactive: () => boolean
) {
  if (isInactive()) return
  try {
    await queryClient.invalidateQueries(
      { queryKey: queryKeys.workspaceStats(workspaceId) },
      { throwOnError: true }
    )
  } catch (err) {
    const message =
      err instanceof Error ? err.message : 'Failed to refresh jobs'
    useJobStore.getState().failJobFetch(workspaceId, message)
  }
}
