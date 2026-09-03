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
// #426 review P2：聚合 bindingStatus——绑定解析 = published 目录（本查询）
// + 含 draft 的 agent-definitions，任一尚无数据时节点详情里的 agentId=null
// 只是「未知」而非「未绑定」，内联编辑器要等 settle 才出新建表单；agentId
// 已解析时编辑器不经该门控（#426 codex P2，门控在 Editor 侧按 agentId 收窄）。

/** capability→Agent 绑定解析状态：pending=任一查询首次在途；error=任一
 * 查询失败且无数据（不退回可操作表单）；ready=两条查询都有数据，绑定
 * 结果（含 agentId=null 的「确认未绑定」）可信。 */
export type AgentBindingStatus = 'pending' | 'error' | 'ready'

/** 聚合两查询的 settle 态成绑定解析状态：pending=任一首次在途；
 * error=任一失败且无数据；否则 ready。显式返回类型保持字面量联合。 */
function settleBinding(failed: boolean, pending: boolean): AgentBindingStatus {
  return failed ? 'error' : pending ? 'pending' : 'ready'
}

export function useAgentCatalog(workspaceId: string | undefined) {
  const query = useQuery({
    queryKey: extraQueryKeys.studioAgentCatalog(workspaceId ?? ''),
    queryFn: () => getAgentCatalog(workspaceId!),
    enabled: Boolean(workspaceId),
  })
  const definitions = useAgentDefinitions(workspaceId)
  const pending = query.isPending || definitions.pending
  const failed = (query.isError && !query.data) || definitions.failed
  return {
    agents: query.data?.agents ?? [],
    definitions: definitions.agents,
    bindingStatus: settleBinding(failed, pending),
    // 错误横幅覆盖两条查询（目录或定义任一失败都让绑定信息不可信），重试
    // 也一起重取；有缓存数据的后台刷新失败仍计入 loadError（与既有语义
    // 一致），但 bindingStatus 保持 ready——绑定仍可解析。
    loadError: query.isError || definitions.loadError,
    retry: () => Promise.all([query.refetch(), definitions.retry()]),
  }
}
