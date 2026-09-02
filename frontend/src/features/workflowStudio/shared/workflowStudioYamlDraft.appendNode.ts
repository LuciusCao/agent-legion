import {
  dumpWorkflowYaml,
  parseWorkflowYamlStrictNodes,
} from './workflowStudioYamlDraft.parse'
import type { SwitchableNodeType } from './workflowStudioYamlDraft.nodeType'

// 画布「添加节点」的入参（#392 Phase 3）。新节点默认不接线（after: []）
// ——接线只能走 YAML 编辑器（「依赖关系」段是只读展示）。
export type AppendWorkflowNodeInput = {
  nodeType: SwitchableNodeType
  key: string
  label?: string
  capability?: string
}

export class WorkflowNodeAppendError extends Error {}

// 追加节点进草稿 YAML。校验全部在写入前完成（AGENTS.md L88）：key 为
// 合法 YAML 标量且不与既有节点/合成 _start 冲突；草稿经
// parseWorkflowYamlStrictNodes 结构守卫（nodes 数组/字符串或某节点非
// mapping 的草稿拒绝追加——对象展开会把索引当节点键、覆盖保存时不可逆
// 破坏草稿）；code/agent 的 capability 非空（label 与 capability 缺省
// = key，approval 无 capability）。失败抛 WorkflowNodeAppendError，
// 草稿不动。
export function appendWorkflowNode(
  rawYaml: string,
  input: AppendWorkflowNodeInput
): string {
  const key = input.key.trim()
  if (!key || /[:\s]/.test(key)) {
    throw new WorkflowNodeAppendError(
      '节点 Key 必须是非空且不含空格/冒号的字符串'
    )
  }
  let draft: ReturnType<typeof parseWorkflowYamlStrictNodes>
  try {
    draft = parseWorkflowYamlStrictNodes(rawYaml)
  } catch {
    throw new WorkflowNodeAppendError('草稿结构异常；请先在 YAML 编辑器修正')
  }
  const nodes = draft.nodes ?? {}
  if (nodes[key] !== undefined) {
    throw new WorkflowNodeAppendError(`节点 Key「${key}」已存在`)
  }
  const capability =
    input.nodeType === 'approval' ? '' : input.capability?.trim() || key
  if (input.nodeType !== 'approval' && !capability) {
    throw new WorkflowNodeAppendError(
      `切换为 ${input.nodeType} 的节点需要非空 capability`
    )
  }
  const next = {
    ...nodes,
    [key]: {
      type: input.nodeType,
      label: input.label?.trim() || key,
      ...(input.nodeType === 'approval' ? {} : { capability }),
      after: [] as string[],
    },
  }
  return dumpWorkflowYaml({ ...draft, nodes: next })
}
