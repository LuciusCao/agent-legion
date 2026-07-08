import type { JobState } from '../state'
import type { WorkflowDefinitionRecord } from '../../../types'
import type {
  JobFilterNodeOption,
  WorkflowVersionOptions,
} from '../filterLogic/types'

export function makeSelectNodeOptions(
  workflowDefinition: WorkflowDefinitionRecord | null
) {
  const cache = new Map<Set<string>, JobFilterNodeOption[]>()
  const defined = new Map<string, string>()
  for (const node of workflowDefinition?.nodes ?? []) {
    defined.set(node.key, node.label)
  }

  return function selectNodeOptions(state: JobState): JobFilterNodeOption[] {
    const nodeKeys = state.optionAccumulator.nodeKeys
    const cached = cache.get(nodeKeys)
    if (cached) return cached

    const options: JobFilterNodeOption[] = []
    for (const key of nodeKeys) {
      options.push({ key, label: defined.get(key) ?? key })
    }
    options.sort((a, b) => a.key.localeCompare(b.key))
    cache.set(nodeKeys, options)
    return options
  }
}

export function selectWorkflowVersionOptions(
  state: JobState
): WorkflowVersionOptions {
  return state.optionAccumulator.workflowVersionOptions
}
