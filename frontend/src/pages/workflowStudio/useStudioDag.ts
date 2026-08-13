import { useMemo } from 'react'
import { useWorkspaceSettingsQuery } from '../../hooks/useWorkspaceSettingsQuery'
import type { WorkflowDefinitionRecord } from '../../types'
import type {
  AgentDefinition,
  ExecutorDefinition,
} from '../../types/executorTypes'
import { buildDagEdges, buildDagNodes } from './workflowStudioDag'
import type { StudioNodeRouting } from './workflowStudioRouting'

const EMPTY_BINDINGS: StudioNodeRouting['bindings'] = []

// DAG 节点绑定摘要的数据组装：bindings 来自 workspace 设置快照（与设置页共享
// react-query 缓存，绑定保存后失效自动刷新），agents 来自 executor catalog。
export function useStudioDag(
  workspaceId: string | undefined,
  workflow: WorkflowDefinitionRecord | null,
  executorCatalog: ExecutorDefinition[],
  agentCatalog: AgentDefinition[]
) {
  const { data: snapshot } = useWorkspaceSettingsQuery(workspaceId)
  const bindings = snapshot?.executorConfiguration.bindings ?? EMPTY_BINDINGS
  return useMemo(
    () => ({
      nodes: buildDagNodes(workflow, executorCatalog, {
        bindings,
        agents: agentCatalog,
      }),
      edges: buildDagEdges(workflow),
    }),
    [workflow, executorCatalog, agentCatalog, bindings]
  )
}
