import type { QueryClient } from '@tanstack/react-query'
import { queryKeys } from '../lib/queryKeys'
import type { WorkspaceStats } from '../types/workspaceTypes'

// 浅合并事件携带的 job_stats：只替换 job_stats，保留 workflow_label 等其他
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
// 发请求）。refetch 失败落在 stats 查询自身的错误状态（TanStack v5 保留
// 已有成功数据），不触碰任务列表（#1183：窗口聚焦/事件防抖都会走到这里，
// stats 失败清列表会把瞬时故障放大成整页列表销毁）。调用方均为
// fire-and-forget（void refresh()），吞掉 rejection 仅为避免 unhandled
// rejection。
export async function refreshWorkspaceEvents(
  queryClient: QueryClient,
  workspaceId: string,
  isInactive: () => boolean
) {
  if (isInactive()) return
  await queryClient
    .invalidateQueries({ queryKey: queryKeys.workspaceStats(workspaceId) })
    .catch(() => {})
}
