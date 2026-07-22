import { computeDirty, type SettingStoreSet } from '../state'

export function executorActions(set: SettingStoreSet) {
  return {
    setExecutorAllocation(executorId: string, limit: number) {
      set((state) => {
        const allocations = state.executorConfiguration.allocations.filter(
          (a) => a.executor_id !== executorId
        )
        allocations.push({
          executor_id: executorId,
          workspace_id: state.workspaceId ?? '',
          concurrency_limit: limit,
        })
        const nextConfiguration = {
          ...state.executorConfiguration,
          allocations,
        }
        const nextState = { ...state, executorConfiguration: nextConfiguration }
        return { ...nextState, isDirty: computeDirty(nextState) }
      })
    },

    requestExecutorRemoval(executorId: string) {
      set({ pendingAllocationRemoval: executorId })
    },

    confirmExecutorRemoval() {
      set((state) => {
        const executorId = state.pendingAllocationRemoval
        if (!executorId) return state
        const allocations = state.executorConfiguration.allocations.filter(
          (a) => a.executor_id !== executorId
        )
        const bindings = state.executorConfiguration.bindings.filter(
          (b) => b.executor_id !== executorId
        )
        const removedBindingKeys = new Set(
          state.executorConfiguration.bindings
            .filter((b) => b.executor_id === executorId)
            .map((b) => `${b.workflow_key}:${b.node_key}`)
        )
        const node_limits = state.executorConfiguration.node_limits.filter(
          (l) => !removedBindingKeys.has(`${l.workflow_key}:${l.node_key}`)
        )
        const nextConfiguration = {
          ...state.executorConfiguration,
          allocations,
          bindings,
          node_limits,
        }
        const nextState = {
          ...state,
          executorConfiguration: nextConfiguration,
          pendingAllocationRemoval: null,
        }
        return { ...nextState, isDirty: computeDirty(nextState) }
      })
    },

    cancelExecutorRemoval() {
      set({ pendingAllocationRemoval: null })
    },

    setNodeBinding(
      workflowKey: string,
      nodeKey: string,
      executorId: string | null
    ) {
      set((state) => {
        const bindings = state.executorConfiguration.bindings.filter(
          (b) => !(b.workflow_key === workflowKey && b.node_key === nodeKey)
        )
        let node_limits = state.executorConfiguration.node_limits
        if (executorId === null) {
          node_limits = node_limits.filter(
            (l) => !(l.workflow_key === workflowKey && l.node_key === nodeKey)
          )
        }
        if (executorId !== null) {
          bindings.push({
            workflow_key: workflowKey,
            node_key: nodeKey,
            executor_id: executorId,
          })
        }
        const nextConfiguration = {
          ...state.executorConfiguration,
          bindings,
          node_limits,
        }
        const nextState = { ...state, executorConfiguration: nextConfiguration }
        return { ...nextState, isDirty: computeDirty(nextState) }
      })
    },

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
