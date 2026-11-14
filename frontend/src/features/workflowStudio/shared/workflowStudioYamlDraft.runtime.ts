import {
  parseWorkflowYaml,
  type WorkflowYamlNode,
  type WorkflowYamlObject,
} from './workflowStudioYamlDraft.parse'

/**
 * agent 节点必须自含执行档案：节点 `execution.runtime` 缺失即由 legacy
 * 补档界面承接，不从 workflow 顶层补值。无遗留块的新建 / 转型节点仍写入
 * 平台默认 velites；有遗留 runtime 时不另造 velites，保留 legacy 补档。
 */
export const DEFAULT_AGENT_RUNTIME = 'velites'

function nodeRuntime(node: WorkflowYamlNode): string {
  const runtime = node.execution?.runtime
  return typeof runtime === 'string' ? runtime : ''
}

/** 节点的生效 runtime 只来自节点自身；缺失 = ''（legacy 补档）。 */
export function effectiveNodeRuntime(
  _draft: WorkflowYamlObject,
  node: WorkflowYamlNode
): string {
  return nodeRuntime(node)
}

function hasLegacyTopLevelRuntime(draft: WorkflowYamlObject): boolean {
  const execution = (draft as { execution?: unknown }).execution
  if (!execution || typeof execution !== 'object') return false
  const runtime = (execution as { runtime?: unknown }).runtime
  return typeof runtime === 'string' && runtime !== ''
}

/** 无遗留 runtime 的新 agent 写默认值；legacy 缺档不另造 velites。 */
export function ensureAgentNodeRuntime(
  draft: WorkflowYamlObject,
  node: WorkflowYamlNode
): void {
  if (effectiveNodeRuntime(draft, node) || hasLegacyTopLevelRuntime(draft))
    return
  node.execution = { ...(node.execution ?? {}), runtime: DEFAULT_AGENT_RUNTIME }
}

/** 非 agent 节点剥离 agent 专属档案字段（loader 禁止其出现在非 agent 节点）。 */
export function stripAgentProfileFields(node: WorkflowYamlNode): void {
  delete node.requires_labels
  delete node.tools
  if (!node.execution || !('runtime' in node.execution)) return
  const execution = { ...node.execution }
  delete execution.runtime
  if (Object.keys(execution).length === 0) delete node.execution
  else node.execution = execution
}

export type NodeRuntimeInfo = {
  /** 节点级 `execution.runtime`（'' = 未声明，显示 legacy 补档）。 */
  nodeRuntime: string
}

/** 从草稿 YAML 读节点的 runtime 声明；草稿解析失败 / 节点缺失时回落 *fallback*。 */
export function readNodeRuntimeInfo(
  rawYaml: string,
  nodeKey: string,
  fallback: NodeRuntimeInfo
): NodeRuntimeInfo {
  try {
    const draft = parseWorkflowYaml(rawYaml)
    const node = draft.nodes?.[nodeKey]
    if (!node) return fallback
    return { nodeRuntime: nodeRuntime(node) }
  } catch {
    return fallback
  }
}
