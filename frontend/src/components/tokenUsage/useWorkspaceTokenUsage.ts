import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { fetchWorkspaceTokenUsage } from '../../api/tokenUsage'
import { extraQueryKeys } from '../../lib/queryKeysExtra'

export interface TokenUsageFilters {
  nodeKey: string
  model: string
  skillVersion: string
}

/**
 * Workspace token 用量聚合查询。切换过滤条件时保留上一批数据，
 * 避免摘要卡/表格闪空（同原 useAsync 行为）。
 */
export function useWorkspaceTokenUsage(
  workspaceId: string,
  groupBy: string,
  filters: TokenUsageFilters
) {
  return useQuery({
    queryKey: extraQueryKeys.workspaceTokenUsage(workspaceId, {
      groupBy,
      ...filters,
    }),
    queryFn: () => {
      const params = new URLSearchParams({ group_by: groupBy })
      if (filters.nodeKey) params.set('node_key', filters.nodeKey)
      if (filters.model) params.set('model', filters.model)
      if (filters.skillVersion)
        params.set('skill_version', filters.skillVersion)
      return fetchWorkspaceTokenUsage(workspaceId, params)
    },
    placeholderData: keepPreviousData,
  })
}
