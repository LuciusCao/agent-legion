import { useQuery } from '@tanstack/react-query'
import { fetchWorkspaceStats } from '../api'
import { queryKeys } from '../lib/queryKeys'

/**
 * 单个 workspace 的 stats 查询。SSE 事件写方（dashboard 批推、workspace 事件
 * 浅合并、jobs 快照）直接写同一 queryKey 的缓存；事件驱动的刷新走
 * invalidateQueries，只在本 hook 有活跃观察者时真正 refetch。
 * 拉取失败时 data 保持 undefined，调用方按原行为渲染 fallback。
 */
export function useWorkspaceStats(workspaceId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.workspaceStats(workspaceId ?? ''),
    queryFn: () => fetchWorkspaceStats(workspaceId as string),
    enabled: !!workspaceId,
  })
}
