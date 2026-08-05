import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { fetchWorkspaces } from '../api'
import { queryKeys } from '../lib/queryKeys'
import type { WorkspaceRecord } from '../types'

/**
 * Workspace 列表查询。列表数据只经 react-query 缓存共享，
 * 派生态（如当前路由对应的 workspace）由 useCurrentWorkspace 计算，
 * 不再写回任何全局 store。
 */
export function useWorkspaces() {
  return useQuery({
    queryKey: queryKeys.workspaces(),
    queryFn: async () => (await fetchWorkspaces()).workspaces,
  })
}

/** 当前路由 workspaceId 对应的 WorkspaceRecord；列表未加载或 id 不存在时为 null。 */
export function useCurrentWorkspace(): WorkspaceRecord | null {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const { data: workspaces } = useWorkspaces()
  return workspaces?.find((w) => w.id === workspaceId) ?? null
}
