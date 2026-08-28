import { createContext, useContext } from 'react'
import type { WorkflowDefinitionRecord } from '../../types'
import type { AgentDefinition } from '../../types/agentCatalogTypes'

// Studio 内「打开某 Agent」的导航通道：由页面层提供，Inspector 深层组件
// 直接消费。Agent 管理弹窗删除后收敛为「选中绑定该 capability 的节点」，
// agent 的查看/编辑在节点详情内嵌完成。
export type StudioNav = {
  openAgent: (agentId: string) => void
}

/** nav 落点解析：agent → capability → 绑定该 capability 的节点 key。 */
export function nodeKeyForAgent(
  agentId: string,
  workflow: WorkflowDefinitionRecord | null,
  agentCatalog: AgentDefinition[]
): string | null {
  const capability = agentCatalog.find((a) => a.id === agentId)?.capability
  const node = workflow?.nodes.find((n) => n.capability === capability)
  return node?.key ?? null
}

const defaultNav: StudioNav = { openAgent: () => {} }

export const StudioNavContext = createContext<StudioNav>(defaultNav)

export function useStudioNav(): StudioNav {
  return useContext(StudioNavContext)
}
