import {
  dumpWorkflowYaml,
  parseWorkflowYaml,
} from './workflowStudioYamlDraft.parse'
import type { SwitchableNodeType } from './workflowStudioYamlDraft.nodeType'

// 画布「添加节点」的入参（#392 Phase 3）：类型 + key（必填、唯一）；
// label 缺省 = key；code/agent 需要非空 capability（loader 契约），缺省
// = key；approval 无 capability。新节点默认不接线（after: []）——边的
// 接线走 YAML 编辑器或「依赖关系」，添加流程的提示文案会说明（approval
// 在 validate 时会要求可执行入边，属预期引导）。
export type AppendWorkflowNodeInput = {
  nodeType: SwitchableNodeType
  key: string
  label?: string
  capability?: string
}

export class WorkflowNodeAppendError extends Error {}

// 追加节点进草稿 YAML。校验全部在写入前完成（AGENTS.md L88）：key 为
// 合法 YAML 标量、不与既有节点/合成 _start 冲突；code/agent 的
// capability 非空。失败抛 WorkflowNodeAppendError，草稿不动。
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
  const draft = parseWorkflowYaml(rawYaml)
  if (draft.nodes?.[key] !== undefined) {
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
    ...(draft.nodes ?? {}),
    [key]: {
      type: input.nodeType,
      label: input.label?.trim() || key,
      ...(input.nodeType === 'approval' ? {} : { capability }),
      after: [] as string[],
    },
  }
  return dumpWorkflowYaml({ ...draft, nodes: next })
}
