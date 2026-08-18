import { useMemo } from 'react'
import type { WorkflowDefinitionRecord } from '../../types'
import type { AgentDefinition } from '../../types/executorTypes'
import { buildDagEdges, buildDagNodes } from './workflowStudioDag'

// DAG 节点路由摘要的数据组装：agents 来自 workspace 级 catalog（P-0.5：无
// Agent 路由的节点一律进入隐含 code 池，不再有 executor 绑定）。
export function useStudioDag(
  workflow: WorkflowDefinitionRecord | null,
  agentCatalog: AgentDefinition[]
) {
  return useMemo(
    () => ({
      nodes: buildDagNodes(workflow, { agents: agentCatalog }),
      edges: buildDagEdges(workflow),
    }),
    [workflow, agentCatalog]
  )
}
