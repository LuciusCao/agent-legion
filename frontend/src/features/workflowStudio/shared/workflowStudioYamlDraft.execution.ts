import {
  dumpWorkflowYaml,
  parseWorkflowYaml,
  type WorkflowYamlNode,
} from './workflowStudioYamlDraft.parse'

/** 草稿 YAML 只承诺形状不承诺类型（`provider: 1`、`model: true` 这类合法
 * YAML 非法契约值）：execution 值非字符串一律按未配置（空串）处理——
 * 渲染与编辑路径都不得因 .trim() 抛异常（codex P1 缺陷族；画布求值器
 * workflowStudioExecutionWarnings 共用本归一）。 */
export function asConfigValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

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
    // 非字符串 junk 值按空处理并删除该键（codex P1 缺陷族，reviewer-m4 r2）。
    if (!asConfigValue(execution[key]).trim()) delete execution[key]
  }
  if (Object.keys(execution).length === 0) delete node.execution
  else node.execution = execution
}
