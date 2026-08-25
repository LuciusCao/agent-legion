import type { components } from '../../generated/api'
import type { WorkflowDefinitionRecord, WorkflowNodeRecord } from '../../types'
import type { ChangeSummaryViewModel } from './workflowStudioChanges'
import { selectedNodeDetails } from './workflowStudioModel'
import type { SelectedWorkflowNodeDetails } from './workflowStudioModel'
import { ghostDraftNodeDetails } from './workflowStudioGhostNode'

type WorkflowEdgeResponse = components['schemas']['WorkflowEdgeResponse']

/**
 * inspector 的节点详情解析链：基线 workflow → 草稿 YAML（draft-only ghost
 * 节点）→ compareSummary（YAML 里没有 start 节点时，后端 loader 注入的合成
 * start 被 compare 画成 added ghost，但草稿文本里查不到）。三级都落空返回
 * null，由 inspector 显示空态/「未加载 workflow」。
 */
export function inspectorNodeDetails(
  source: {
    workflow: WorkflowDefinitionRecord | null
    definitionYaml: string
    compareSummary?: ChangeSummaryViewModel | null
  },
  selectedNodeKey: string | null
): SelectedWorkflowNodeDetails | null {
  return (
    selectedNodeDetails(source.workflow, selectedNodeKey) ??
    ghostDraftNodeDetails(source.definitionYaml, selectedNodeKey) ??
    compareGhostNodeDetails(source.compareSummary ?? null, selectedNodeKey)
  )
}

/**
 * compare 兜底：仅当 compare 里该 key 的 nodeType 是 'start' 时合成 start
 * 节点详情——accepted_item_types 用 DEFAULT 契约 ['material','ref']，与
 * acceptedItemTypes() 的 fallback 语义一致（EXEC-WORKFLOW-START-001）；
 * nodeType 为 'node' 或查不到返回 null（理论上不会发生）。incoming/outgoing
 * 从 edgeChanges 里该 key 的 added 边还原（condition 是展示用字符串，不还原）。
 */
export function compareGhostNodeDetails(
  compareSummary: ChangeSummaryViewModel | null,
  selectedNodeKey: string | null
): SelectedWorkflowNodeDetails | null {
  if (!compareSummary || !selectedNodeKey) return null
  const change = compareSummary.nodeChanges.find(
    (entry) => entry.nodeKey === selectedNodeKey
  )
  if (change?.nodeType !== 'start') return null
  const edges: WorkflowEdgeResponse[] = compareSummary.edgeChanges
    .filter((edge) => edge.type === 'added')
    .map((edge) => ({ source: edge.source, target: edge.target }))
  const node: WorkflowNodeRecord = {
    key: selectedNodeKey,
    label: change.label,
    capability: '',
    after: [],
    inputs: [],
    outputs: [],
    node_type: 'start',
    accepted_item_types: ['material', 'ref'],
  }
  return {
    node,
    incoming: edges.filter((edge) => edge.target === selectedNodeKey),
    outgoing: edges.filter((edge) => edge.source === selectedNodeKey),
  }
}
