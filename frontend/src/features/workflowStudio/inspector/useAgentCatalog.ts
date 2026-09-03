import { useQuery } from '@tanstack/react-query'
import { getAgentCatalog } from '../../../api/agentCatalogApi'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import { useAgentDefinitions } from './useAgentDefinitions'

// Studio 的 Agent 目录走 react-query 共享缓存：Agent 面板发布、归档、回滚
// 后 invalidate 同一 key，DAG 路由摘要同会话即时重取；加载失败保留 error 态
// （loadError + retry），不再静默成空目录。P-0.5：executors 半区已退役，
// catalog 只剩 agents。agent 半区是 workspace 作用域（schema v46）：无
// workspaceId 时不发请求。#387：同时取 agent-definitions（含 draft）——
// draft-only Agent 的节点解析与导航回落靠它（useAgentDefinitions）。
// #426 codex 终轮 P2：本 hook 是 workspace 级，不感知 capability 是否命中
// published，故只把两份查询的 settle 信号（settle 嵌套对象，形状见
// agentBindingStatus.AgentCatalogSettle，消费方经该模块的门控函数按
// capability 组合）下发，不在这里预折叠成单一 bindingStatus。类型与门控
// 计算都在 agentBindingStatus.ts。
// #426 codex 终轮复审 P2：settle = !isFetching（不是 !isPending）——
// invalidate 触发的后台重取期间 isPending 仍 false（有缓存），旧缓存里
// 已归档/已变更的 Agent 若继续放行，编辑器可操作已归档条目；空 catalog
// 旧缓存还会抢先放出新建表单、重取后切换真实 draft 重挂丢输入。备选的
// setQueryData 在调用方同步改写缓存需为归档/发布/草稿保存各猜一个列表
// 终态，猜错即静默漂移，且聊天的 turn_end 失效（studioChatInvalidation）
// 无对应事件负载可同步——isFetching 让「缓存值可能过期」这一事实本身
// 驱动门控，语义正确且改动集中在数据源。refetchOnWindowFocus 默认开启
// （queryClient.ts）+ staleTime 30s：聚焦重取只在数据过期时发生，
// 编辑器占位不频繁闪烁；归档等 mutation 后的重取会短暂出占位，正是
// 「旧值不可信」的窗口。

export function useAgentCatalog(workspaceId: string | undefined) {
  const query = useQuery({
    queryKey: extraQueryKeys.studioAgentCatalog(workspaceId ?? ''),
    queryFn: () => getAgentCatalog(workspaceId!),
    enabled: Boolean(workspaceId),
  })
  const definitions = useAgentDefinitions(workspaceId)
  return {
    agents: query.data?.agents ?? [],
    definitions: definitions.agents,
    settle: {
      catalogSettled: !query.isFetching,
      catalogFailed: query.isError && !query.data,
      definitionsSettled: !definitions.pending,
      definitionsFailed: definitions.failed,
    },
    // 错误横幅覆盖两条查询（目录或定义任一失败都让绑定信息不可信），重试
    // 也一起重取；有缓存数据的后台刷新失败仍计入 loadError（与既有语义
    // 一致），但 settle 信号不回退——绑定仍可按缓存解析。
    loadError: query.isError || definitions.loadError,
    retry: () => Promise.all([query.refetch(), definitions.retry()]),
  }
}
