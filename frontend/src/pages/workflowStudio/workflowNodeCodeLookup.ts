import { api } from '../../api'
import type { components } from '../../generated/api'
import type { ExecutorDefinition } from '../../types/executorTypes'
import { findCapabilityBindings } from './WorkflowExecutorBindingList'

type NodeCodeTemplateResponse =
  components['schemas']['WorkflowNodeCodeTemplateResponse']

// A node has viewable/editable code when its capability binds to a kind="code"
// executor — with or without a builtin path (pathless = custom-code only).
export function hasCodeCapability(
  executorCatalog: ExecutorDefinition[],
  capability: string
): boolean {
  return findCapabilityBindings(executorCatalog, capability).some(
    ({ executor }) => executor.kind === 'code'
  )
}

// Repo path of the builtin file serving the capability; null when pathless.
export function findNodeCodePath(
  executorCatalog: ExecutorDefinition[],
  capability: string
): string | null {
  const binding = findCapabilityBindings(executorCatalog, capability).find(
    ({ executor, detail }) => executor.kind === 'code' && Boolean(detail.path)
  )
  return binding?.detail.path ?? null
}

// Backend-owned minimal Node SDK skeleton for the「从模板新建」entry.
export function fetchNodeCodeTemplate(): Promise<NodeCodeTemplateResponse> {
  return api('/api/workflow-node-code-template')
}
