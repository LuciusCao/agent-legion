import type { ConfigSchemaProperty } from '../../../types'
import { defaultValueMatchesType } from './workflowStudioYamlDraft.configSchema.helpers'

// code 节点 config_schema 的约束语义（enum/minimum/maximum/secret，#428
// codex 二轮），从 configSchema.helpers 拆出守单文件预算。三类消费方：
// - schema 编辑器：类型切换时清理不再可信的约束（P2-B）；
// - 版本值表单：secret 属性排除出表单（P1-A）、enum/边界值校验（P1-B）；
// - 存量值渲染：YAML 塞进来的非法值行内提示不阻塞显示（二轮复审 P3-1）。

/** 类型切换的连带清理（P2-B）：enum/minimum/maximum 的值类型与旧
 * type 绑定，切换后不再可信（number→string 留 numeric minimum 会被
 * loader 拒）——全清让用户按新类型重设，比精细保留安全。与新类型
 * 不兼容的 default 一并删除（loader 同因拒绝）。 */
export function stripTypeIncompatibleConstraints(
  prop: {
    default?: string | number | boolean
    enum?: unknown
    minimum?: unknown
    maximum?: unknown
  },
  nextType: ConfigSchemaProperty['type']
): void {
  delete prop.enum
  delete prop.minimum
  delete prop.maximum
  if (
    prop.default !== undefined &&
    !defaultValueMatchesType(prop.default, nextType)
  ) {
    delete prop.default
  }
}

/** 版本值表单是否应渲染该属性的输入框（P1-A）：secret 属性的值走
 * vault 支撑的运行时覆盖通道，绝不写进草稿/revision（VAULT-SECRET-001
 * ——draft 保存路径不经过 settings PATCH 的 apply_node_secret_fields，
 * 明文会进 revision 与 intake 冻结数据）。 */
export function isSecretConfigProperty(prop: ConfigSchemaProperty): boolean {
  return prop.secret === true
}

/**
 * 版本值提交前的 enum/边界校验（P1-B）：发布只校验 schema 不校验
 * config，enum 外值或越界值一旦进入 active revision，之后所有新 job
 * 的 intake 都会失败。返回错误文案（null = 通过）；类型匹配由上游
 * parseConfigValue 保证，这里只看 enum/minimum/maximum。
 */
export function configValueConstraintError(
  prop: ConfigSchemaProperty,
  value: string | number | boolean | undefined
): string | null {
  if (value === undefined) return null
  if (prop.enum !== undefined && !prop.enum.includes(value as string | number))
    return `值必须在枚举 ${JSON.stringify(prop.enum)} 内`
  if (typeof value === 'number') {
    if (prop.minimum !== undefined && value < prop.minimum)
      return `值不得小于 ${prop.minimum}`
    if (prop.maximum !== undefined && value > prop.maximum)
      return `值不得大于 ${prop.maximum}`
  }
  return null
}
