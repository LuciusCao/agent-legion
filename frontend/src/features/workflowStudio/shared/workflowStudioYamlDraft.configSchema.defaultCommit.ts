import type { ConfigSchemaProperty } from '../../../types'
import { configValueConstraintError } from './workflowStudioYamlDraft.configSchema.constraints'
import {
  defaultValueMatchesType,
  parseSchemaDefaultValue,
} from './workflowStudioYamlDraft.configSchema.helpers'

// 默认值编辑器的一次性提交校验（#428 codex 二轮 P2 + 二轮复审 NIT-2b），
// 从 configSchema.constraints 拆出守单文件预算：解析 → 类型匹配 →
// enum/边界完整约束，任一不过返回错误文案（null = 可提交）；通过时
// parsed 回传解析结果，调用方据此 patch。enum 外/越界的默认值会被
// loader 拒绝整份草稿——发布校验只查 schema 本身，这里是唯一闸口。
export function schemaDefaultCommit(
  raw: string,
  prop: ConfigSchemaProperty
): { error: string | null; parsed: string | number | boolean | undefined } {
  const parsed = parseSchemaDefaultValue(raw, prop.type)
  if (parsed === null)
    return { error: unparseable(prop.type), parsed: undefined }
  if (parsed !== undefined && !defaultValueMatchesType(parsed, prop.type))
    return { error: typeMismatch(prop.type), parsed: undefined }
  const constraint = configValueConstraintError(prop, parsed)
  if (constraint)
    return { error: `默认${constraint}，未写入`, parsed: undefined }
  return { error: null, parsed }
}

function unparseable(type: ConfigSchemaProperty['type']): string {
  return `无法解析为 ${type} 类型的默认值，未写入`
}

function typeMismatch(type: ConfigSchemaProperty['type']): string {
  return `默认值与类型 ${type} 不匹配，未写入`
}
