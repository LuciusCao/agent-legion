import { useQuery } from '@tanstack/react-query'
import { getExecutorCatalog } from '../../api/executorApi'
import { extraQueryKeys } from '../../lib/queryKeysExtra'

// Studio 的 executor/agent 目录走 react-query 共享缓存：Agent/Executor 面板
// 发布、归档、回滚后 invalidate 同一 key，DAG 绑定摘要与绑定编辑器同会话即时
// 重取；加载失败保留 error 态（loadError + retry），不再静默成空目录。
// agent 半区是 workspace 作用域（schema v46）：无 workspaceId 时不发请求。
export function useExecutorCatalog(workspaceId: string | undefined) {
  const query = useQuery({
    queryKey: extraQueryKeys.studioExecutorCatalog(workspaceId ?? ''),
    queryFn: () => getExecutorCatalog(workspaceId!),
    enabled: Boolean(workspaceId),
  })
  return {
    executors: query.data?.executors ?? [],
    agents: query.data?.agents ?? [],
    loadError: query.isError,
    retry: query.refetch,
  }
}
