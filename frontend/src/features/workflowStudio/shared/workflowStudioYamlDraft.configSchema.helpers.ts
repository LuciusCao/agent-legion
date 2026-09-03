// code 节点 config_schema 结构化编辑的校验与类型转换（#418 面板），从
// workflowStudioYamlDraft.configSchema 拆出以守单文件预算。后端子集
// （server/app/config_schema.py）：属性键白名单
// type/description/default/enum/minimum/maximum/secret/secret_ref/
// runtime_mutable，类型四选一；平台保留键 timeout_seconds/
// sandbox_network 不得在节点 schema 里声明（loader 拒绝）。

export const SCHEMA_PROPERTY_TYPES = [
  'string',
  'integer',
  'number',
  'boolean',
] as const

export type SchemaPropertyType = (typeof SCHEMA_PROPERTY_TYPES)[number]

// 平台保留执行键（server/app/workflows/node_config_schema.py
// RESERVED_EXECUTION_KEYS）：编辑器拒绝新增同名属性。
export const RESERVED_EXECUTION_KEYS = ['timeout_seconds', 'sandbox_network']

/** 校验属性名：非空、无空白/点号/冒号（YAML mapping 键安全）、不撞保留键。 */
export function validateSchemaPropertyName(
  name: string,
  existing: string[]
): string | null {
  const trimmed = name.trim()
  if (!trimmed) return '属性名不能为空'
  if (/[\s.:]+/.test(trimmed)) return '属性名不能包含空格、点号或冒号'
  if (RESERVED_EXECUTION_KEYS.includes(trimmed))
    return `${trimmed} 是平台保留执行键，不能在节点 config_schema 声明`
  if (existing.includes(trimmed)) return `属性 ${trimmed} 已存在`
  return null
}

/** 校验属性名是否可作为改名目标（允许保持原名不变）。 */
export function validateSchemaPropertyRename(
  name: string,
  propKey: string,
  otherKeys: string[]
): string | null {
  if (name.trim() === propKey) return null
  return validateSchemaPropertyName(name, otherKeys)
}

export function defaultValueMatchesType(
  value: string | number | boolean,
  type: SchemaPropertyType
): boolean {
  switch (type) {
    case 'string':
      return typeof value === 'string'
    case 'boolean':
      return typeof value === 'boolean'
    case 'integer':
      return typeof value === 'number' && Number.isInteger(value)
    case 'number':
      return typeof value === 'number'
  }
}

/** 输入框字符串 → schema default 值；空串返回 undefined（= 删除键）。 */
export function parseSchemaDefaultValue(
  raw: string,
  type: SchemaPropertyType
): string | number | boolean | undefined {
  const trimmed = raw.trim()
  if (!trimmed) return undefined
  if (type === 'string') return raw
  if (type === 'boolean') return trimmed === 'true'
  const num = Number(trimmed)
  if (Number.isNaN(num)) return undefined
  return num
}
