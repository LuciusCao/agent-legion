import type { components } from '../../../generated/api'
import type { WorkflowNodeRecord } from '../../../types'
import type { SelectedWorkflowNodeDetails } from '../shared/workflowStudioModel'
import {
  parseWorkflowNode,
  parseWorkflowYaml,
  normalizeStringList,
} from '../shared/workflowStudioYamlDraft.parse'
import { normalizeTextInput } from '../shared/workflowStudioYamlDraft.textInput'
import { readDraftStartTypes } from '../shared/workflowStudioTextInputEditable'
import { materializeDraftEdges } from './workflowStudioDraftEdges'

type WorkflowEdgeResponse = components['schemas']['WorkflowEdgeResponse']

/**
 * 基线 workflow 里不存在的节点（compare 叠加的 ghost 节点，典型场景是新
 * workspace 的空态模板）：从草稿 YAML 还原详情，让 draft-only 节点在
 * inspector 里可编辑。YAML 驱动的段（基本设置/执行/数据契约/依赖）直接
 * 可用；节点代码段走后端（draft-only 节点已放开）；节点配置段无 schema
 * 时自动隐藏。incoming/outgoing 经 materializeDraftEdges 归一化——与
 * loader._load_edges 同构：v1 草稿（schema_version 缺省/1）物化节点
 * after，v2+ 只认顶层 edges（#1258 codex P2）。
 */
export function ghostDraftNodeDetails(
  definitionYaml: string,
  selectedNodeKey: string | null
): SelectedWorkflowNodeDetails | null {
  if (!selectedNodeKey) return null
  const parsed = parseWorkflowNode(definitionYaml, selectedNodeKey)
  if (!parsed) return null
  const textInput = normalizeTextInput(parsed.text_input)
  if (
    parsed.type === 'start' &&
    (textInput === undefined ||
      readDraftStartTypes(definitionYaml, selectedNodeKey) === null)
  )
    return null
  // 语法非法的草稿在上面的 parseWorkflowNode 已落空返回；这里是读侧防御
  // （inspector 链另由形状守卫整体回退，#405 codex P1）。
  let edges: WorkflowEdgeResponse[]
  try {
    edges = materializeDraftEdges(parseWorkflowYaml(definitionYaml))
  } catch {
    edges = []
  }
  const node: WorkflowNodeRecord = {
    key: selectedNodeKey,
    label: parsed.label ?? selectedNodeKey,
    capability: parsed.capability ?? '',
    // 数组字段渲染期归一（#1258 codex 四轮 P1，与 record 映射同一 helper）。
    after: normalizeStringList(parsed.after),
    inputs: normalizeStringList(parsed.inputs),
    outputs: normalizeStringList(parsed.outputs),
    // 显式类型还原（#284 + EXEC-APPROVAL-001）：start/approval 各自还原；
    // 其余节点还原 code|agent，遗留 `type: node` 与缺失一律按 code（与
    // 后端 loader 归一化一致）。
    ...(parsed.type === 'start'
      ? {
          node_type: 'start',
          accepted_item_types: normalizeStringList(parsed.accepted_item_types),
          text_input: textInput ?? null,
        }
      : {
          node_type:
            parsed.type === 'agent'
              ? 'agent'
              : parsed.type === 'approval'
                ? 'approval'
                : 'code',
        }),
  }
  return {
    node,
    incoming: edges.filter((edge) => edge.target === selectedNodeKey),
    outgoing: edges.filter((edge) => edge.source === selectedNodeKey),
  }
}
