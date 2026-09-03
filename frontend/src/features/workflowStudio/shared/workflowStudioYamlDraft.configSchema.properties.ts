import type { ConfigSchema, ConfigSchemaProperty } from '../../../types'
import type { SchemaPropertyType } from './workflowStudioYamlDraft.configSchema.helpers'
import { defaultValueMatchesType } from './workflowStudioYamlDraft.configSchema.helpers'
import {
  dumpWorkflowYaml,
  parseWorkflowYaml,
} from './workflowStudioYamlDraft.parse'

// code 节点 config_schema 属性级 CRUD（#418 面板），从
// workflowStudioYamlDraft.configSchema 拆出以守单文件预算。读 → 改 →
// 整体写回；写侧在 parse 成功后 mutate 同一 draft，保持与整体替换
// patchWorkflowNodeConfigSchema 相同的往返语义（未知键保留）。

export type SchemaPropertyPatch = {
  type?: SchemaPropertyType
  description?: string
  default?: string | number | boolean | undefined
  runtimeMutable?: boolean
}

/**
 * 更新一个已有 schema 属性的 type/description/default/runtime_mutable。
 * default 传 undefined = 删除默认值键；空描述 = 删除 description。
 * type 变化会连带删除与新类型不兼容的 default（loader 校验 default
 * 必须过 _check_value，遗留会让整份草稿被拒）。
 */
export function patchWorkflowNodeSchemaProperty(
  rawYaml: string,
  nodeKey: string,
  propKey: string,
  patch: SchemaPropertyPatch
): string {
  const draft = parseWorkflowYaml(rawYaml)
  const node = draft.nodes?.[nodeKey]
  if (!node) throw new Error(`Node ${nodeKey} not found`)
  const schema = node.config_schema
  const prop = schema?.properties?.[propKey]
  if (!schema || !prop) throw new Error(`Property ${propKey} not found`)
  const next = structuredClone(schema)
  const nextProp = next.properties![propKey] as ConfigSchemaProperty
  if (patch.type) {
    nextProp.type = patch.type
    if (
      typeof nextProp.default !== 'undefined' &&
      !defaultValueMatchesType(nextProp.default, patch.type)
    ) {
      delete nextProp.default
    }
  }
  if (patch.description !== undefined) {
    if (patch.description.trim()) nextProp.description = patch.description
    else delete nextProp.description
  }
  if (patch.default !== undefined) {
    if (patch.default === '') delete nextProp.default
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
  const draft = parseWorkflowYaml(rawYaml)
  const node = draft.nodes?.[nodeKey]
  if (!node) throw new Error(`Node ${nodeKey} not found`)
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

/**
 * 改名属性（保持属性声明顺序中相对位置：删旧插新在队尾）。
 * required 列表里的旧名同步替换（loader 要求 required 引用存在的属性）。
 */
export function renameWorkflowNodeSchemaProperty(
  rawYaml: string,
  nodeKey: string,
  propKey: string,
  nextKey: string
): string {
  const draft = parseWorkflowYaml(rawYaml)
  const node = draft.nodes?.[nodeKey]
  if (!node) throw new Error(`Node ${nodeKey} not found`)
  const schema = node.config_schema
  const prop = schema?.properties?.[propKey]
  if (!schema || !prop) throw new Error(`Property ${propKey} not found`)
  const next = structuredClone(schema)
  const properties = next.properties!
  delete properties[propKey]
  properties[nextKey] = prop
  if (next.required) {
    next.required = next.required.map((name) =>
      name === propKey ? nextKey : name
    )
  }
  node.config_schema = next
  return dumpWorkflowYaml(draft)
}

/** 删除属性；required 里的引用一并清掉；最后一个属性删掉后整段 schema 移除。 */
export function removeWorkflowNodeSchemaProperty(
  rawYaml: string,
  nodeKey: string,
  propKey: string
): string {
  const draft = parseWorkflowYaml(rawYaml)
  const node = draft.nodes?.[nodeKey]
  if (!node) throw new Error(`Node ${nodeKey} not found`)
  const schema = node.config_schema
  if (!schema?.properties?.[propKey])
    throw new Error(`Property ${propKey} not found`)
  const next = structuredClone(schema)
  delete next.properties![propKey]
  if (next.required) {
    next.required = next.required.filter((name) => name !== propKey)
    if (next.required.length === 0) delete next.required
  }
  if (Object.keys(next.properties ?? {}).length === 0) {
    delete node.config_schema
  } else {
    node.config_schema = next
  }
  return dumpWorkflowYaml(draft)
}
