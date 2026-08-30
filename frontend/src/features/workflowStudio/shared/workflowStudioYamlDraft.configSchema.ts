import type { ConfigSchema } from '../../../types'
import {
  dumpWorkflowYaml,
  parseWorkflowYaml,
} from './workflowStudioYamlDraft.parse'

// 整体替换节点的 config_schema；传 undefined 或空对象时删除该字段。
// schema 对象按原样写回（parse→mutate→dump 往返保留未知键）。
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
  } else {
    node.config_schema = schema
  }
  return dumpWorkflowYaml(draft)
}
