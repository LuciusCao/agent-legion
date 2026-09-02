import type { AgentListItem, WorkflowDefinitionRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'

// Studio 内「打开某 Agent」的导航通道：由页面层提供（见 useStudioNavState），
// Inspector 深层组件直接消费。Agent 管理弹窗删除后收敛为「选中绑定该
// capability 的节点」，agent 的查看/编辑在节点详情内嵌完成。
export type StudioNav = {
  openAgent: (agentId: string) => void
  /** 最近一次 openAgent 的目标草稿；节点详情解析时优先命中（codex P1
   * on #391：同 capability 允许存在多个未发布草稿，保留用户点击的草稿
   * 身份，避免打开/发布成另一个草稿）。 */
  pendingAgentId: string | null
  clearPendingAgentId: () => void
}

/** nav 落点解析：agent → capability → 绑定该 capability 的节点 key。
 * published 目录查不到时回落 agent-definitions（含 draft，#387）。 */
export function nodeKeyForAgent(
  agentId: string,
  workflow: WorkflowDefinitionRecord | null,
  agentCatalog: AgentDefinition[],
  agentDefinitions: AgentListItem[] = []
): string | null {
  const capability =
    agentCatalog.find((a) => a.id === agentId)?.capability ??
    agentDefinitions.find((a) => a.agent_id === agentId)?.capability
  const node = workflow?.nodes.find((n) => n.capability === capability)
  return node?.key ?? null
}
