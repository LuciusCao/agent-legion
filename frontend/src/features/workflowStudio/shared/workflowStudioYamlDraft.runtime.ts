import {
  parseWorkflowYaml,
  type WorkflowYamlNode,
  type WorkflowYamlObject,
} from './workflowStudioYamlDraft.parse'

/**
 * #935（#440 P3）：agent 节点必须自含执行档案——`execution.runtime`（节点
 * 级，或 workflow 顶层 `execution.runtime` 默认）缺失即发布门禁报错。
 *
 * 新建 / 切换成 agent 的节点没有顶层默认可继承时，写入的默认 runtime 取
 * velites：它是平台默认 runtime（#408，`AgentDefinition` 的默认 tools 也
 * 按 velites 目录给出），不依赖异步加载的 runtime 目录，写路径保持同步、
 * 确定。作者随后可在检查器的 runtime 下拉里改成 pi。
 */
export const DEFAULT_AGENT_RUNTIME = 'velites'

function topLevelRuntime(draft: WorkflowYamlObject): string {
  const execution = (draft as { execution?: unknown }).execution
  if (!execution || typeof execution !== 'object') return ''
  const runtime = (execution as { runtime?: unknown }).runtime
  return typeof runtime === 'string' ? runtime : ''
}

function nodeRuntime(node: WorkflowYamlNode): string {
  const runtime = node.execution?.runtime
  return typeof runtime === 'string' ? runtime : ''
}

/** 节点的生效 runtime：节点级优先，其次 workflow 顶层默认；皆空 = ''。 */
export function effectiveNodeRuntime(
  draft: WorkflowYamlObject,
  node: WorkflowYamlNode
): string {
  return nodeRuntime(node) || topLevelRuntime(draft)
}

/** workflow 顶层 `execution.runtime` 默认（未声明 = ''）。 */
export function workflowDefaultRuntime(draft: WorkflowYamlObject): string {
  return topLevelRuntime(draft)
}

/** agent 节点无生效 runtime 时写入默认 runtime（就地修改）。 */
export function ensureAgentNodeRuntime(
  draft: WorkflowYamlObject,
  node: WorkflowYamlNode
): void {
  if (effectiveNodeRuntime(draft, node)) return
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
  /** 节点级 `execution.runtime`（'' = 未声明）。 */
  nodeRuntime: string
  /** workflow 顶层 `execution.runtime` 默认（'' = 无）。 */
  defaultRuntime: string
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
    return {
      nodeRuntime: nodeRuntime(node),
      defaultRuntime: topLevelRuntime(draft),
    }
  } catch {
    return fallback
  }
}
