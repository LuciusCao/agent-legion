import {
  parseWorkflowYaml,
  type WorkflowYamlNode,
} from './workflowStudioYamlDraft.parse'

/** 顶层 execution 默认块（provider/model/thinking 子集，prompt 仅节点级）。 */
export type WorkflowYamlExecutionDefaults = Pick<
  NonNullable<WorkflowYamlNode['execution']>,
  'provider' | 'model' | 'thinking'
>

/**
 * 顶层 execution 默认块；YAML 解析失败或缺失时返回空对象。
 * WorkflowYamlObject 不携带 execution 字段（自 parse 模块拆出，文件预算），
 * 这里按原始 mapping 形状窄化读取。
 */
export function parseWorkflowExecutionDefaults(
  rawYaml: string
): WorkflowYamlExecutionDefaults {
  try {
    const draft = parseWorkflowYaml(rawYaml) as {
      execution?: WorkflowYamlExecutionDefaults
    }
    return draft.execution ?? {}
  } catch {
    return {}
  }
}
