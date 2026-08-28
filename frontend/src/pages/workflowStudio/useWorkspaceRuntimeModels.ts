import { useQuery } from '@tanstack/react-query'
import { fetchWorkspaceRuntimeModels } from '../../api'
import { extraQueryKeys } from '../../lib/queryKeysExtra'

/**
 * 在线 Worker 上报的 {runtime: {provider: [models]}} 聚合（schema v63 起
 * workspace Agent 默认配置退役，Studio 节点执行下拉的唯一数据源）。
 * Worker 上下线会改变聚合结果，30s staleTime + 窗口聚焦 refetch。
 */
export function useWorkspaceRuntimeModels(workspaceId: string | undefined) {
  return useQuery({
    queryKey: extraQueryKeys.workspaceRuntimeModels(workspaceId ?? ''),
    queryFn: () => fetchWorkspaceRuntimeModels(workspaceId!),
    enabled: !!workspaceId,
    staleTime: 30_000,
  })
}
