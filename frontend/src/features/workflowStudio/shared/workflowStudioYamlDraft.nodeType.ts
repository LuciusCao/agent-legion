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
// capabilityChannel 是 approval→code/agent 的原子补能力通道（#405）：
// approval 按契约无 capability、结构化 UI 又对该类型隐藏能力 Key 输入，
// 「先在基本设置补 capability 再切」不可达；切换弹窗随本次提交写入。
// 通道先 trim 再参与校验与写入——空白串等同未提供（不得绕过前置校验）；
// 节点已有非空 capability 时优先既有值（弹窗不经该路径，防御直通）。
export function patchWorkflowNodeType(
  rawYaml: string,
  nodeKey: string,
  nodeType: SwitchableNodeType,
  capabilityChannel?: string
): string {
  const draft = parseWorkflowYaml(rawYaml)
  const node = draft.nodes?.[nodeKey]
  if (!node) throw new Error(`Node ${nodeKey} not found`)
  if (node.type === 'start') throw new Error(`Node ${nodeKey} is a start node`)
  const capability = capabilityChannel?.trim() || undefined
  validateNodeTypeSwitch(draft, node, nodeKey, nodeType, capability)
  const sourceType = node.type ?? 'code'
  node.type = nodeType
  if (capability && !node.capability) node.capability = capability
  sanitizeNodeForType(node, sourceType, nodeType)
  return dumpWorkflowYaml(draft)
}
