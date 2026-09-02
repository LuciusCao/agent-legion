import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { fetchAgentDefinitions } from '../../../api/agentDefinitions'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import { useStudioNav } from '../shared/useStudioNavState'
import type { AgentListItem } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'

// /api/agent-definitions（list_latest）含 draft 状态：与只回 published 的
// agent-catalog 互补（#387：draft-only Agent 也要有 Studio 内的编辑/发布
// 入口）。与 WorkflowNodeAgentEditor 的 invalidate 共用 agentDefinitions
// query key，保存/发布/归档后同会话即时重取。workspace 作用域（schema
// v46）：无 workspaceId 时不发请求。workspaceId 取路由参数（codex P1 on
// #391：不读全局 store——切 workspace 时 settings 异步水合有延迟窗口，
// store 值会短暂指向旧 workspace，草稿列表必须与路由同步）。
export function useAgentDefinitions(workspaceId: string | undefined) {
  const query = useQuery({
    queryKey: extraQueryKeys.agentDefinitions(workspaceId ?? ''),
    queryFn: () => fetchAgentDefinitions(workspaceId!),
    enabled: Boolean(workspaceId),
  })
  return { agents: query.data?.agents ?? [], settled: !query.isPending }
}

// #387：draft-only Agent（MCP save_agent_definition_draft 建的草稿）不在
// published 目录里，但同样要在节点详情可编辑/发布。AgentListItem 只带列表
// 摘要字段，映射为最小 AgentDefinition（工具/标签等在编辑器内由详情接口
// 补全，卡片少展示几行可接受）。
function draftAgentFromListItem(item: AgentListItem): AgentDefinition | null {
  if (item.status === 'archived') return null
  return {
    id: item.agent_id,
    capability: item.capability,
    runtime: item.runtime as AgentDefinition['runtime'],
    skill: item.skill,
  }
}

/** 按 capability 解析节点绑定的 Agent：published 目录优先，查不到时回落
 * draft 列表。openAgent 的 pendingAgentId 优先命中并在解析后清除（codex
 * P1 on #391：同 capability 允许存在多个未发布草稿，保留用户点击的草稿
 * 身份，避免打开/发布成另一个草稿）。清除绑定「数据 settle + 命中确认或
 * 证伪」：列表还在加载（缓存滞后于 turn_end 失效重取）时保留 pending，
 * 命中同 capability 草稿或数据已 settle 仍未命中（草稿已删/跨 capability）
 * 才清除，避免竞态窗口内身份丢失（subagent review P2-1 on #391）。
 * isDraft 标记回落命中（该 capability 无 published 版本）。 */
export function useCapabilityAgent(props: {
  node: { capability: string }
  agentCatalog: AgentDefinition[]
}) {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const { agents, settled } = useAgentDefinitions(workspaceId)
  const nav = useStudioNav()
  const capability = props.node.capability
  const published = props.agentCatalog.find((a) => a.capability === capability)
  // pending 为 null 时 find 不命中（undefined），等价于无偏好。
  const preferred = agents.find((a) => a.agent_id === nav.pendingAgentId)
  const preferredHit = preferred?.capability === capability
  useEffect(() => {
    // 清除绑定「数据 settle + 命中确认/证伪」，避免缓存滞后窗口丢身份。
    if (nav.pendingAgentId && (preferredHit || settled))
      nav.clearPendingAgentId()
  }, [nav, preferredHit, settled])
  const draftItem = preferredHit
    ? preferred
    : agents.find((a) => a.capability === capability)
  const draft = draftItem ? draftAgentFromListItem(draftItem) : null
  return {
    agent: published ?? draft ?? undefined,
    isDraft: !published && draftItem?.status === 'draft',
  }
}
