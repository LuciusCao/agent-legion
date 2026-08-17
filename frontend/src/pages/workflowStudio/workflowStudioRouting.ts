import type { WorkflowNodeRecord } from '../../types'
import type { AgentDefinition } from '../../types/executorTypes'
import type { ExecutorKind } from '../../types/jobTypes'

export type StudioNodeRouting = {
  agents: AgentDefinition[]
}

export type ResolvedNodeRouting = {
  agentId: string | null
  executorId: string | null
  executorKind: ExecutorKind | undefined
  executorUnbound: boolean
}

// P-0.5：capability 恰有一个 published Agent 时走 Agent 路由；其余一律进入
// 隐含 code 池（executor 绑定概念已随 schema v47 退役）。
export function resolveStudioNodeRouting(
  node: WorkflowNodeRecord,
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
  return {
    agentId: null,
    executorId: 'code',
    executorKind: 'code',
    executorUnbound: false,
  }
}
