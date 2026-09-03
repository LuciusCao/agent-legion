import type { ConfigSchema, ConfigSchemaProperty } from '../../../types'
import {
  dumpWorkflowYaml,
  parseWorkflowYaml,
} from './workflowStudioYamlDraft.parse'

// code 节点 revision 作用域的 config 值读写（#418 后半）。node `config`
// 是三层解析链（schema defaults → node config → workspace 覆盖）的中间
// 层，随 workflow revision 版本化、发布时进入新版本（publish 的
// _structural_payload 覆盖它）。与「运行时覆盖」通道（workspace
// node_config，经 settings/nodes PATCH 写 live 设置）区分。

/** 读侧防御：YAML 编辑中途的非法文本吞错返回空（仓库纪律同
 * readApprovalNodeConfig），编辑合法后自然恢复真实值。 */
export function readNodeConfig(
  rawYaml: string,
  nodeKey: string
): Record<string, unknown> {
  try {
    const node = parseWorkflowYaml(rawYaml).nodes?.[nodeKey]
    return node?.config ?? {}
  } catch {
    return {}
  }
}

/** 读节点声明的 config_schema（无/非法 YAML 返回 undefined）。 */
export function readNodeConfigSchema(
  rawYaml: string,
  nodeKey: string
): ConfigSchema | undefined {
  try {
    return parseWorkflowYaml(rawYaml).nodes?.[nodeKey]?.config_schema
  } catch {
    return undefined
  }
}

/** 单键写入（undefined = 删除键）；全部键清空后整段 config 删除。 */
export function patchWorkflowNodeConfigValue(
  rawYaml: string,
  nodeKey: string,
  key: string,
  value: unknown
): string {
  const draft = parseWorkflowYaml(rawYaml)
  const node = draft.nodes?.[nodeKey]
  if (!node) throw new Error(`Node ${nodeKey} not found`)
  const config: Record<string, unknown> = { ...(node.config ?? {}) }
  if (value === undefined || value === '' || value === null) {
    delete config[key]
  } else {
    config[key] = value
  }
  if (Object.keys(config).length === 0) delete node.config
  else node.config = config
  return dumpWorkflowYaml(draft)
}

/** 表单输入串 → schema 类型的值；空串 = 未填（undefined）。 */
export function parseConfigValue(
  raw: string,
  prop: ConfigSchemaProperty
): string | number | boolean | undefined {
  const trimmed = raw.trim()
  if (!trimmed) return undefined
  switch (prop.type) {
    case 'boolean':
      return trimmed === 'true'
    case 'integer':
    case 'number': {
      const num = Number(trimmed)
      return Number.isNaN(num) ? undefined : num
    }
    default:
      return raw
  }
}

/** 值 → 表单展示串（boolean 用 'true'/'false'，其余 String()）。 */
export function formatConfigValue(value: unknown): string {
  if (value === undefined || value === null) return ''
  return String(value)
}
