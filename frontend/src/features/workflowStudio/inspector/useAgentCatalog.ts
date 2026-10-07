import { useQuery } from '@tanstack/react-query'
import { getAgentCatalog } from '../../../api/agentCatalogApi'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'

// Studio 的（legacy）Agent 目录走 react-query 共享缓存：只读服务 DAG 路由
// 摘要与 legacy 节点的 skill 预览兜底；加载失败保留 error 态（loadError +
// retry），不静默成空目录。P-0.5：executors 半区已退役，catalog 只剩
// agents。agent 半区是 workspace 作用域（schema v46）：无 workspaceId 时
// 不发请求。#1079（#440 P3b）：节点详情不再内嵌 Agent 编辑器，draft-only
// Agent 的回落解析（agent-definitions）与 #426 的编辑器门控 settle 信号
// 一并删除。
export function useAgentCatalog(workspaceId: string | undefined) {
  const query = useQuery({
    queryKey: extraQueryKeys.studioAgentCatalog(workspaceId ?? ''),
    queryFn: () => getAgentCatalog(workspaceId!),
    enabled: Boolean(workspaceId),
  })
  return {
    agents: query.data?.agents ?? [],
    loadError: query.isError,
    retry: query.refetch,
  }
}
