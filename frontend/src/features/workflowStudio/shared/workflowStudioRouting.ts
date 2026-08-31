import type { WorkflowNodeRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { ExecutorKind } from '../../../types/jobTypes'

export type StudioNodeRouting = {
  agents: AgentDefinition[]
}

export type ResolvedNodeRouting = {
  agentId: string | null
  executorId: string | null
  executorKind: ExecutorKind | undefined
  executorUnbound: boolean
}

// #284：节点路由由显式 node_type 判定——type=agent 走 Agent 路由（agent
// 目录仅用于按 capability 找 Agent 定义拿 id）；其余一律进入隐含 code 池
// （executor 绑定概念已随 schema v47 退役）。
export function resolveStudioNodeRouting(
  node: WorkflowNodeRecord,
  routing?: StudioNodeRouting
): ResolvedNodeRouting {
  if (node.node_type === 'agent') {
    const agent = routing?.agents.find(
      (definition) => definition.capability === node.capability
    )
    return {
      agentId: agent?.id ?? null,
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
