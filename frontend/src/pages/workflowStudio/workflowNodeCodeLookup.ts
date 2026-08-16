import { api } from '../../api'
import type { components } from '../../generated/api'
import type { ExecutorDefinition } from '../../types/executorTypes'
import { findCapabilityBindings } from './WorkflowExecutorBindingList'

type NodeCodeTemplateResponse =
  components['schemas']['WorkflowNodeCodeTemplateResponse']

// A node has viewable/editable code when its capability binds to a kind="code"
// executor; the code itself is DB-published (workspace version or the global
// factory seed for demo nodes) — there is no repo path anymore (#96).
export function hasCodeCapability(
  executorCatalog: ExecutorDefinition[],
  capability: string
): boolean {
  return findCapabilityBindings(executorCatalog, capability).some(
    ({ executor }) => executor.kind === 'code'
  )
}

// Backend-owned minimal Node SDK skeleton for the「从模板新建」entry.
export function fetchNodeCodeTemplate(): Promise<NodeCodeTemplateResponse> {
  return api('/api/workflow-node-code-template')
}
