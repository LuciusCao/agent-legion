import {
  dumpWorkflowYaml,
  parseWorkflowYaml,
} from './workflowStudioYamlDraft.parse'

// Inspector 头部徽标：type=agent 无徽标，approval 专属徽标，其余 code。
export function workflowNodeKindBadge(nodeType: string | undefined): string {
  if (nodeType === 'agent') return ''
  if (nodeType === 'approval') return 'approval'
  return 'code'
}

// 改写节点的显式执行类型（#284）：type=code 节点「切换为 Agent 执行」时
// 把草稿 YAML 的 type 改为 agent。start 节点的类型不可改写。
export function patchWorkflowNodeType(
  rawYaml: string,
  nodeKey: string,
  nodeType: 'code' | 'agent'
): string {
  const draft = parseWorkflowYaml(rawYaml)
  const node = draft.nodes?.[nodeKey]
  if (!node) throw new Error(`Node ${nodeKey} not found`)
  if (node.type === 'start') throw new Error(`Node ${nodeKey} is a start node`)
  node.type = nodeType
  return dumpWorkflowYaml(draft)
}

// 「切换为 Agent 执行」：改写草稿 YAML 的节点 type 并经 setDefinitionYaml
// 落草稿状态（自动持久化走 workflow-draft API）；改写失败返回 false，由
// 按钮侧降级提示用户手动改 YAML。
export function switchWorkflowNodeToAgent(
  rawYaml: string,
  nodeKey: string,
  setDefinitionYaml: (value: string) => void
): boolean {
  try {
    setDefinitionYaml(patchWorkflowNodeType(rawYaml, nodeKey, 'agent'))
    return true
  } catch {
    return false
  }
}
