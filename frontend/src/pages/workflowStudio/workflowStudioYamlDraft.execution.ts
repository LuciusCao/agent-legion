import {
  dumpWorkflowYaml,
  parseWorkflowYaml,
  type WorkflowYamlNode,
} from './workflowStudioYamlDraft.parse'

export function patchWorkflowNodeExecution(
  rawYaml: string,
  nodeKey: string,
  field: 'provider' | 'model' | 'thinking' | 'prompt',
  value: string
): string {
  const draft = parseWorkflowYaml(rawYaml)
  const node = draft.nodes?.[nodeKey]
  if (!node) throw new Error(`Node ${nodeKey} not found`)
  updateExecution(node, field, value)
  return dumpWorkflowYaml(draft)
}

function updateExecution(
  node: WorkflowYamlNode,
  field: 'provider' | 'model' | 'thinking' | 'prompt',
  value: string
) {
  const execution = { ...(node.execution ?? {}), [field]: value }
  for (const key of Object.keys(execution) as Array<keyof typeof execution>) {
    if (!execution[key]?.trim()) delete execution[key]
  }
  if (Object.keys(execution).length === 0) delete node.execution
  else node.execution = execution
}
