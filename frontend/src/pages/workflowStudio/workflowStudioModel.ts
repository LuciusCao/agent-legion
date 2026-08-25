import type { components } from '../../generated/api'
import type { WorkflowDefinitionRecord, WorkflowNodeRecord } from '../../types'
import {
  parseWorkflowEdgeConditions,
  parseWorkflowNode,
} from './workflowStudioYamlDraft.parse'
type WorkflowConditionResponse =
  components['schemas']['WorkflowConditionResponse']
type WorkflowEdgeResponse = components['schemas']['WorkflowEdgeResponse']

export type ValidationGroups = {
  yaml: string[]
  schema: string[]
  structure: string[]
  executor: string[]
  revision: string[]
}

export type SelectedWorkflowNodeDetails = {
  node: WorkflowNodeRecord
  incoming: WorkflowEdgeResponse[]
  outgoing: WorkflowEdgeResponse[]
}

export function conditionLabel(
  condition: WorkflowConditionResponse | null | undefined
): string {
  if (!condition) return ''
  return `${condition.path} == ${JSON.stringify(condition.equals)}`
}

export { groupValidationErrors } from './workflowStudioValidationGroups'

export function isDefinitionDirty(
  originalDefinition: string,
  currentDefinition: string
): boolean {
  return (
    originalDefinition.replace(/\r\n/g, '\n') !==
    currentDefinition.replace(/\r\n/g, '\n')
  )
}

export function selectedNodeDetails(
  workflow: WorkflowDefinitionRecord | null,
  selectedNodeKey: string | null
): SelectedWorkflowNodeDetails | null {
  if (!workflow || !selectedNodeKey) return null
  const node = workflow.nodes.find(
    (candidate) => candidate.key === selectedNodeKey
  )
  if (!node) return null
  return {
    node,
    incoming: workflow.edges.filter((edge) => edge.target === selectedNodeKey),
    outgoing: workflow.edges.filter((edge) => edge.source === selectedNodeKey),
  }
}

/**
 * 基线 workflow 里不存在的节点（compare 叠加的 ghost 节点，典型场景是新
 * workspace 的空态模板）：start 节点是只读入口契约，可从草稿 YAML 还原
 * 详情展示；其他 ghost 节点返回 null —— 它们的 inspector 段依赖服务端已
 * 发布资源（node code 等），未发布节点不能渲染那些编辑器。
 */
export function ghostStartNodeDetails(
  definitionYaml: string,
  selectedNodeKey: string | null
): SelectedWorkflowNodeDetails | null {
  if (!selectedNodeKey) return null
  const parsed = parseWorkflowNode(definitionYaml, selectedNodeKey)
  if (parsed?.type !== 'start') return null
  const edges: WorkflowEdgeResponse[] = parseWorkflowEdgeConditions(
    definitionYaml
  )
    .filter((edge) => edge.source && edge.target)
    .map((edge) => ({
      source: edge.source as string,
      target: edge.target as string,
      condition: edge.condition?.path
        ? {
            artifact: edge.condition.artifact ?? '',
            path: edge.condition.path,
            equals: edge.condition.equals,
          }
        : null,
    }))
  const node: WorkflowNodeRecord = {
    key: selectedNodeKey,
    label: parsed.label ?? selectedNodeKey,
    capability: '',
    after: parsed.after ?? [],
    inputs: parsed.inputs ?? [],
    outputs: parsed.outputs ?? [],
    node_type: 'start',
    accepted_item_types: parsed.accepted_item_types ?? [],
  }
  return {
    node,
    incoming: edges.filter((edge) => edge.target === selectedNodeKey),
    outgoing: edges.filter((edge) => edge.source === selectedNodeKey),
  }
}
