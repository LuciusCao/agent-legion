import { computeDirty, type SettingStoreSet } from '../state'

// P-0.5：executor 概念退役后，工作区级执行配置只剩节点并发上限与 Agent 容量。
export function executorActions(set: SettingStoreSet) {
  return {
    setNodeLimit(workflowKey: string, nodeKey: string, limit: number | null) {
      set((state) => {
        const node_limits = state.executorConfiguration.node_limits.filter(
          (l) => !(l.workflow_key === workflowKey && l.node_key === nodeKey)
        )
        if (limit !== null) {
          node_limits.push({
            workflow_key: workflowKey,
            node_key: nodeKey,
            concurrency_limit: limit,
          })
        }
        const nextConfiguration = {
          ...state.executorConfiguration,
          node_limits,
        }
        const nextState = { ...state, executorConfiguration: nextConfiguration }
        return { ...nextState, isDirty: computeDirty(nextState) }
      })
    },
    setAgentCapacity(capacity: number) {
      set((state) => {
        const executorConfiguration = {
          ...state.executorConfiguration,
          agent_capacity: capacity,
        }
        const nextState = { ...state, executorConfiguration }
        return { ...nextState, isDirty: computeDirty(nextState) }
      })
    },
  }
}
