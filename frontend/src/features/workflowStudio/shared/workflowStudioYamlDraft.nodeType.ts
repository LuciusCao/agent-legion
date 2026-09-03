import {
  dumpWorkflowYaml,
  parseWorkflowYaml,
} from './workflowStudioYamlDraft.parse'
import {
  sanitizeNodeForType,
  validateNodeTypeSwitch,
} from './workflowStudioYamlDraft.nodeTypeSwitch'

// 可切换的节点显式类型（#392）。start 是契约入口（每 DAG 恰一个、由
// loader 保证），不进选择器也不可切入/切出；读侧遗留 `node` 已在 parse
// 层归一化为 code。
export type SwitchableNodeType = 'code' | 'agent' | 'approval'

export { WorkflowNodeTypeSwitchError } from './workflowStudioYamlDraft.nodeTypeSwitch'

// 改写节点的显式执行类型（#284 → #392 通用化）：先做目标类型的前置
// 校验（capability / 入边，见 nodeTypeSwitch），再切换 type 并按目标类型
// 清洗字段，保证改写后的草稿不违反 loader 的类型禁令（否则下一次
// validate/publish 即被拒）。start 节点的类型不可改写。
export function patchWorkflowNodeType(
  rawYaml: string,
  nodeKey: string,
  nodeType: SwitchableNodeType
): string {
  const draft = parseWorkflowYaml(rawYaml)
  const node = draft.nodes?.[nodeKey]
  if (!node) throw new Error(`Node ${nodeKey} not found`)
  if (node.type === 'start') throw new Error(`Node ${nodeKey} is a start node`)
  validateNodeTypeSwitch(draft, node, nodeType)
  const sourceType = node.type ?? 'code'
  node.type = nodeType
  sanitizeNodeForType(node, sourceType, nodeType)
  return dumpWorkflowYaml(draft)
}
