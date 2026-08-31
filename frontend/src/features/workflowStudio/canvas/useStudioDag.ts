import { useMemo } from 'react'
import type { WorkflowDefinitionRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'
import { buildDagEdges, buildDagNodes } from './workflowStudioDag'
import { applyCompareChanges } from './workflowStudioDagChanges'

// DAG 节点路由摘要的数据组装：agents 来自 workspace 级 catalog（P-0.5：无
// Agent 路由的节点一律进入隐含 code 池，不再有 executor 绑定）。
// overlaySummary（草稿 diff）只在草稿模式传入：added 不在基线的节点以幽灵
// 节点/边补入或打幽灵样式，modified/removed 打角标；revision 模式传 null，
// 画布干净渲染被查看版本。
export function useStudioDag(
  workflow: WorkflowDefinitionRecord | null,
  agentCatalog: AgentDefinition[],
  overlaySummary: ChangeSummaryViewModel | null = null
) {
  return useMemo(() => {
    const nodes = buildDagNodes(workflow, { agents: agentCatalog })
    const edges = buildDagEdges(workflow)
    return applyCompareChanges(nodes, edges, overlaySummary)
  }, [workflow, agentCatalog, overlaySummary])
}
