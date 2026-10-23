import type { WorkflowNodeRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { NodeExecutionType } from '../../../components/dag/dagNodeTypes'

export type StudioNodeRouting = {
  agents: AgentDefinition[]
}

export type ResolvedNodeRouting = {
  agentId: string | null
  executionType: NodeExecutionType | null
  executionRuntime: string | null
  executorUnbound: boolean
}

// #284：节点路由由显式 node_type 判定——type=agent 走 Agent 路由（agent
// 目录仅用于按 capability 找 Agent 定义拿 id）；其余一律进入隐含 code 池
// （executor 绑定概念已随 schema v47 退役）。
// #1180：展示层同步显式 type 词汇——类型徽标只表达节点声明的执行类型
// （code/agent，agent 附 execution.runtime），不再填 'code' 池键占位；
// start/approval 不 dispatch，没有执行类型。
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
      executionType: 'agent',
      // 空串归一为 null（草稿记录的 execution 可能携带归一后的空值键）。
      executionRuntime: node.execution?.runtime || null,
      executorUnbound: false,
    }
  }
  if (node.node_type === 'start' || node.node_type === 'approval') {
    return {
      agentId: null,
      executionType: null,
      executionRuntime: null,
      executorUnbound: false,
    }
  }
  return {
    agentId: null,
    executionType: 'code',
    executionRuntime: null,
    executorUnbound: false,
  }
}
