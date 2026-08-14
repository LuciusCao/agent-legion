import { useQuery } from '@tanstack/react-query'
import { getExecutorCatalog } from '../../api/executorApi'
import { extraQueryKeys } from '../../lib/queryKeysExtra'

// Studio 的 executor/agent 目录走 react-query 共享缓存：Agent/Executor 面板
// 发布、归档、回滚后 invalidate 同一 key，DAG 绑定摘要与绑定编辑器同会话即时
// 重取；加载失败保留 error 态（loadError + retry），不再静默成空目录。
export function useExecutorCatalog() {
  const query = useQuery({
    queryKey: extraQueryKeys.studioExecutorCatalog(),
    queryFn: getExecutorCatalog,
  })
  return {
    executors: query.data?.executors ?? [],
    agents: query.data?.agents ?? [],
    loadError: query.isError,
    retry: query.refetch,
  }
}
