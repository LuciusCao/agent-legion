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
// #426 review P2 → codex P2 修正：bindingStatus 只按 published 目录
// （本查询）的 settle 态计算——settle 后「published ?? draft」是终态
// （命中 published 或确认无 published 后的 draft 回落），编辑目标不会再
// 漂移；未 settle 时 agentId 可能只是 draft 回落先行（definitions 先回，
// catalog 尚在途），settle 后可能被同 capability 的 published Agent 替换，
// 故门控等待。definitions 的 pending 与门控无关：published 命中场景
// AgentEditor 按 ID 加载详情不依赖列表；draft 回落场景 definitions 已
// 返回（agentId 有值的前提），失败由 loadError 横幅兜底。

/** capability→Agent 绑定解析状态：pending=published 目录首次在途；error=
 * 目录失败且无数据（不退回可操作表单）；ready=目录已返回，「published ??
 * draft」的绑定结果（含 agentId=null 的「确认无 published」）为终态。 */
export type AgentBindingStatus = 'pending' | 'error' | 'ready'

/** 目录查询的 settle 态成绑定解析状态：pending=首次在途；error=失败且
 * 无数据；否则 ready。显式返回类型保持字面量联合。 */
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
  const pending = query.isPending
  const failed = query.isError && !query.data
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
