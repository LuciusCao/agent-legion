import type { WorkflowNodeRecord } from '../../types'
import type {
  AgentDefinition,
  ExecutorDefinition,
  WorkspaceExecutorConfiguration,
} from '../../types/executorTypes'
import type { ExecutorKind } from '../../types/jobTypes'

export type StudioNodeBinding =
  WorkspaceExecutorConfiguration['bindings'][number]

export type StudioNodeRouting = {
  bindings: StudioNodeBinding[]
  agents: AgentDefinition[]
}

export type ResolvedNodeRouting = {
  agentId: string | null
  executorId: string | null
  executorKind: ExecutorKind | undefined
  executorUnbound: boolean
}

// 与 dispatch 侧语义对齐：capability 恰有一个 published Agent 时走 Agent 路由；
// 否则看 workspace 节点绑定；两者皆无则标记未绑定（dispatch 会因缺少绑定失败）。
export function resolveStudioNodeRouting(
  workflowKey: string,
  node: WorkflowNodeRecord,
  executors: ExecutorDefinition[],
  routing?: StudioNodeRouting
): ResolvedNodeRouting {
  const agent = routing?.agents.find(
    (definition) => definition.capability === node.capability
  )
  if (agent) {
    return {
      agentId: agent.id,
      executorId: null,
      executorKind: undefined,
      executorUnbound: false,
    }
  }
  const binding = routing?.bindings.find(
    (entry) => entry.workflow_key === workflowKey && entry.node_key === node.key
  )
  const executorKind = executors.find((executor) =>
    binding
      ? executor.id === binding.executor_id
      : executor.capabilities.includes(node.capability)
  )?.kind
  return {
    agentId: null,
    executorId: binding?.executor_id ?? null,
    executorKind,
    executorUnbound: routing !== undefined && binding === undefined,
  }
}
