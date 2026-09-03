import type { ConfigSchema } from '../../../types'
import {
  dumpWorkflowYaml,
  parseWorkflowYaml,
} from './workflowStudioYamlDraft.parse'

// 整体替换节点的 config_schema；传 undefined 或空对象时删除该字段。
// schema 对象按原样写回（parse→mutate→dump 往返保留未知键）。
// 结构化编辑的细粒度操作（属性增删改/校验/类型转换，#418 面板）拆在
// configSchema.helpers / configSchema.properties，受单文件预算约束。
//
// 删整段 schema 时连带清空 node.config：三层层级里 schema 没了 config
// 就全是孤儿键，后端 intake 的白名单校验会直接拒绝新 job（#428 独立
// 复审 P1）。
export function patchWorkflowNodeConfigSchema(
  rawYaml: string,
  nodeKey: string,
  schema: ConfigSchema | undefined
): string {
  const draft = parseWorkflowYaml(rawYaml)
  const node = draft.nodes?.[nodeKey]
  if (!node) throw new Error(`Node ${nodeKey} not found`)
  if (!schema || Object.keys(schema).length === 0) {
    delete node.config_schema
    delete node.config
  } else {
    node.config_schema = schema
  }
  return dumpWorkflowYaml(draft)
}
