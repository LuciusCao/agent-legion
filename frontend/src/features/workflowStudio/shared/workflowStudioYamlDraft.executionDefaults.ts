import {
  parseWorkflowYaml,
  type WorkflowYamlNode,
} from './workflowStudioYamlDraft.parse'

/** 顶层 execution 默认块（provider/model/thinking/runtime 子集——prompt/
 * prompt_mode 仅节点级 #513；runtime 默认只达 agent 节点 #933，对齐后端
 * loader.merge_execution_defaults）。 */
export type WorkflowYamlExecutionDefaults = Pick<
  NonNullable<WorkflowYamlNode['execution']>,
  'provider' | 'model' | 'thinking' | 'runtime'
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
