import type { SchemaPropertyType } from './workflowStudioYamlDraft.configSchema.helpers'

// schema 变更与 node.config 的联动（#418 独立复审 P1 + 二轮复审 P2-2），
// 从 configSchema.properties 拆出守单文件预算。后端 intake 对 config 的
// 未知键（validate_config_values 白名单）和类型失配值（_type_matches）
// 都直接 raise：孤儿键/失配值一旦随发布进入 active revision，之后所有
// 新 job 在 intake 处失败且 UI 无从清理。所以 schema 键的每次增删改都要
// 连带维护 node.config 的同名键（调用点在 properties.ts 的 schema CRUD；
// schema 侧的约束清理在 constraints.ts）。

/** node.config 落盘值（unknown：YAML 可塞任意形状）是否与新类型兼容：
 * 与后端 config_schema._type_matches 对齐（integer 只收真整数，
 * number/integer 不收 boolean，string 只收 string）。值恰好兼容
 * （如 42 → number）则保留——与 default 同策略。 */
export function isConfigValueOfType(
  value: unknown,
  type: SchemaPropertyType
): boolean {
  if (typeof value === 'string') return type === 'string'
  if (typeof value === 'boolean') return type === 'boolean'
  if (typeof value !== 'number') return false
  return type === 'number' || (type === 'integer' && Number.isInteger(value))
}

/** 已 parse 节点上的 node.config 键随 schema 键移动：rename 迁移旧键值
 * （值保留），delete 删除键；全部键清空后整段 config 删除。config 缺失
 * 或无该键时 no-op。 */
export function migrateNodeConfigKey(
  node: { config?: Record<string, unknown> },
  fromKey: string,
  toKey?: string
): void {
  const config = node.config
  if (config === undefined || !(fromKey in config)) return
  const rest: Record<string, unknown> = { ...config }
  delete rest[fromKey]
  if (toKey !== undefined) rest[toKey] = config[fromKey]
  node.config = rest
  if (Object.keys(rest).length === 0) delete node.config
}
