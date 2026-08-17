import { useQuery } from '@tanstack/react-query'
import { getExecutorCatalog } from '../../api/executorApi'
import { extraQueryKeys } from '../../lib/queryKeysExtra'

// Studio 的 Agent 目录走 react-query 共享缓存：Agent 面板发布、归档、回滚
// 后 invalidate 同一 key，DAG 路由摘要同会话即时重取；加载失败保留 error 态
// （loadError + retry），不再静默成空目录。P-0.5：executors 半区已退役，
// catalog 只剩 agents。agent 半区是 workspace 作用域（schema v46）：无
// workspaceId 时不发请求。
export function useExecutorCatalog(workspaceId: string | undefined) {
  const query = useQuery({
    queryKey: extraQueryKeys.studioExecutorCatalog(workspaceId ?? ''),
    queryFn: () => getExecutorCatalog(workspaceId!),
    enabled: Boolean(workspaceId),
  })
  return {
    agents: query.data?.agents ?? [],
    loadError: query.isError,
    retry: query.refetch,
  }
}
