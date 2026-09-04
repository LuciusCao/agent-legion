import type { ConfigSchema, ConfigSchemaProperty } from '../../../types'
import type { SchemaPropertyType } from './workflowStudioYamlDraft.configSchema.helpers'
import { stripTypeIncompatibleConstraints } from './workflowStudioYamlDraft.configSchema.constraints'
import {
  isConfigValueOfType,
  migrateNodeConfigKey,
} from './workflowStudioYamlDraft.configSchema.configLink'
import {
  dumpWorkflowYaml,
  parseWorkflowYaml,
} from './workflowStudioYamlDraft.parse'

// code 节点 config_schema 属性级 CRUD（#418 面板），从
// workflowStudioYamlDraft.configSchema 拆出以守单文件预算。读 → 改 →
// 整体写回；写侧在 parse 成功后 mutate 同一 draft，保持与整体替换
// patchWorkflowNodeConfigSchema 相同的往返语义（未知键保留）。
// 属性改名/删除/删整段与类型切换都会连带维护 node.config 的同名键
// （迁移/判定函数在 configSchema.configLink）：后端 intake 对 config 的
// 未知键与类型失配值都直接 raise，孤儿键/失配值随发布进入 active
// revision 后所有新 job 在 intake 处失败且 UI 无从清理（#428 复审 P1
// + 二轮复审 P2-2）。

export type SchemaPropertyPatch = {
  type?: SchemaPropertyType
  description?: string
  default?: string | number | boolean | undefined
  runtimeMutable?: boolean
}

/** parse + 定位节点（各 CRUD 的公共前置），节点缺失直接 throw。 */
function draftNodeOf(rawYaml: string, nodeKey: string) {
  const draft = parseWorkflowYaml(rawYaml)
  const node = draft.nodes?.[nodeKey]
  if (!node) throw new Error(`Node ${nodeKey} not found`)
  return { draft, node }
}

/**
 * 更新一个已有 schema 属性的 type/description/default/runtime_mutable。
 * default 用 'in' 区分「删除」（{ default: undefined }）与「未提供」——
 * 空串/undefined 都删键（#428 codex P2-A）。type 变化的连带清理在
 * configSchema.typeSwitch（#428 二轮复审 P2-2）。
 */
export function patchWorkflowNodeSchemaProperty(
  rawYaml: string,
  nodeKey: string,
  propKey: string,
  patch: SchemaPropertyPatch
): string {
  const { draft, node } = draftNodeOf(rawYaml, nodeKey)
  const schema = node.config_schema
  const prop = schema?.properties?.[propKey]
  if (!schema || !prop) throw new Error(`Property ${propKey} not found`)
  const next = structuredClone(schema)
  const nextProp = next.properties![propKey] as ConfigSchemaProperty
  if (patch.type) {
    nextProp.type = patch.type
    if (patch.type !== prop.type) {
      stripTypeIncompatibleConstraints(nextProp, patch.type)
      const configValue = node.config?.[propKey]
      const stale =
        configValue !== undefined &&
        !isConfigValueOfType(configValue, patch.type)
      if (stale) migrateNodeConfigKey(node, propKey)
    }
  }
  if (patch.description !== undefined) {
    if (patch.description.trim()) nextProp.description = patch.description
    else delete nextProp.description
  }
  if ('default' in patch) {
    if (patch.default === undefined || patch.default === '')
      delete nextProp.default
    else nextProp.default = patch.default
  }
  if (patch.runtimeMutable !== undefined) {
    if (patch.runtimeMutable) nextProp.runtime_mutable = true
    else delete nextProp.runtime_mutable
  }
  node.config_schema = next
  return dumpWorkflowYaml(draft)
}

/** 新增属性；type 默认 string。重名/保留键在写入前应已校验。 */
export function addWorkflowNodeSchemaProperty(
  rawYaml: string,
  nodeKey: string,
  propKey: string,
  type: SchemaPropertyType = 'string'
): string {
  const { draft, node } = draftNodeOf(rawYaml, nodeKey)
  // 无 schema 时补建骨架（type: object + properties）。
  const schema: ConfigSchema = node.config_schema ?? {
    type: 'object',
    properties: {},
  }
  const next = structuredClone(schema)
  next.properties ??= {}
  next.properties[propKey] = { type }
  node.config_schema = next
  return dumpWorkflowYaml(draft)
}

/** 改名属性（删旧插新在队尾）；required 引用与 node.config 旧键同步迁移。 */
export function renameWorkflowNodeSchemaProperty(
  rawYaml: string,
  nodeKey: string,
  propKey: string,
  nextKey: string
): string {
  const { draft, node } = draftNodeOf(rawYaml, nodeKey)
  const schema = node.config_schema
  const prop = schema?.properties?.[propKey]
  if (!schema || !prop) throw new Error(`Property ${propKey} not found`)
  const next = structuredClone(schema)
  const properties = next.properties!
  delete properties[propKey]
  properties[nextKey] = prop
  if (next.required)
    next.required = next.required.map((name) =>
      name === propKey ? nextKey : name
    )
  node.config_schema = next
  migrateNodeConfigKey(node, propKey, nextKey)
  return dumpWorkflowYaml(draft)
}

/** 删除属性及 required 引用；最后一个属性删掉后整段 schema 与 config 同步移除。 */
export function removeWorkflowNodeSchemaProperty(
  rawYaml: string,
  nodeKey: string,
  propKey: string
): string {
  const { draft, node } = draftNodeOf(rawYaml, nodeKey)
  const schema = node.config_schema
  if (!schema?.properties?.[propKey])
    throw new Error(`Property ${propKey} not found`)
  const next = structuredClone(schema)
  delete next.properties![propKey]
  if (next.required) {
    next.required = next.required.filter((name) => name !== propKey)
    if (next.required.length === 0) delete next.required
  }
  if (Object.keys(next.properties ?? {}).length === 0) delete node.config_schema
  else node.config_schema = next
  migrateNodeConfigKey(node, propKey)
  return dumpWorkflowYaml(draft)
}
