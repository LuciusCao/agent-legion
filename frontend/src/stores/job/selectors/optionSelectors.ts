import type { JobState } from '../state'
import type { NodeCatalog } from '../../../lib/nodeCatalog'
import type {
  JobFilterNodeOption,
  WorkflowVersionOptions,
} from '../filterLogic/types'
import {
  nodeKeysFromFacets,
  versionOptionsFromFacets,
} from '../filterLogic/facets'

export function makeSelectNodeOptions(workflowDefinition: NodeCatalog | null) {
  const cache = new Map<Set<string>, JobFilterNodeOption[]>()
  const defined = new Map<string, string>()
  for (const node of workflowDefinition?.nodes ?? []) {
    defined.set(node.key, node.label)
  }

  return function selectNodeOptions(state: JobState): JobFilterNodeOption[] {
    const nodeKeys = state.facets
      ? nodeKeysFromFacets(state.facets)
      : state.optionAccumulator.nodeKeys
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
  return state.facets
    ? versionOptionsFromFacets(state.facets)
    : state.optionAccumulator.workflowVersionOptions
}
