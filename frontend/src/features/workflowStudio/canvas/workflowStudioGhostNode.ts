import type { components } from '../../../generated/api'
import type { WorkflowNodeRecord } from '../../../types'
import type { SelectedWorkflowNodeDetails } from '../shared/workflowStudioModel'
import {
  parseWorkflowEdgeConditions,
  parseWorkflowNode,
} from '../shared/workflowStudioYamlDraft.parse'

type WorkflowEdgeResponse = components['schemas']['WorkflowEdgeResponse']

/**
 * 基线 workflow 里不存在的节点（compare 叠加的 ghost 节点，典型场景是新
 * workspace 的空态模板）：从草稿 YAML 还原详情，让 draft-only 节点在
 * inspector 里可编辑。YAML 驱动的段（基本设置/执行/数据契约/依赖）直接
 * 可用；节点代码段走后端（draft-only 节点已放开）；节点配置段无 schema
 * 时自动隐藏。
 */
export function ghostDraftNodeDetails(
  definitionYaml: string,
  selectedNodeKey: string | null
): SelectedWorkflowNodeDetails | null {
  if (!selectedNodeKey) return null
  const parsed = parseWorkflowNode(definitionYaml, selectedNodeKey)
  if (!parsed) return null
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
    capability: parsed.capability ?? '',
    after: parsed.after ?? [],
    inputs: parsed.inputs ?? [],
    outputs: parsed.outputs ?? [],
    ...(parsed.type === 'start'
      ? {
          node_type: 'start',
          accepted_item_types: parsed.accepted_item_types ?? [],
        }
      : {}),
  }
  return {
    node,
    incoming: edges.filter((edge) => edge.target === selectedNodeKey),
    outgoing: edges.filter((edge) => edge.source === selectedNodeKey),
  }
}
