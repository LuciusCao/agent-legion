import type { ConfigSchemaProperty } from '../../../types'
import { parseConfigValue } from './workflowStudioYamlDraft.nodeConfig'
import { configValueConstraintError } from './workflowStudioYamlDraft.configSchema.constraints'
import { isConfigValueOfType } from './workflowStudioYamlDraft.configSchema.configLink'

// 版本值（node config）的提交与存量校验（#428 三轮复审 P3-3/P3-4），从
// ConfigValueField 拆出守单文件预算，与默认值编辑器的
// configSchema.defaultCommit 对称。发布只校验 schema 不校验 config：
// 非法值一旦随发布进入 active revision，之后所有新 job 的 intake 都会
// 失败且 UI 无从清理。

/** 版本值提交校验（P3-3）：不可解析的数字输入返回错误文案而非按
 * 「未填」删键——parseConfigValue 对垃圾输入返回 null（对齐默认值编辑
 * 器 NIT-2b 的 parseSchemaDefaultValue），显式清空（undefined）才是
 * 合法的删键路径。其余走 enum/边界/整数性约束。 */
export function configValueCommitError(
  raw: string,
  prop: ConfigSchemaProperty
): string | null {
  const value = parseConfigValue(raw, prop)
  if (value === null) return `无法解析为 ${prop.type} 类型的值，未写入`
  return configValueConstraintError(prop, value)
}

/** 存量落盘值的渲染校验（P3-4，只提示不阻塞显示）：必须对落盘的类型值
 * 直接判定——经表单串往返（format → parse）会抹掉类型信息，string 属性
 * 塞 42 / number 属性塞 '20' 显示正常无提示，发布后 intake 的
 * _type_matches 才 raise。类型失配先行，匹配后照常跑 enum/边界约束。 */
export function storedConfigValueError(
  prop: ConfigSchemaProperty,
  value: unknown
): string | null {
  if (value === undefined) return null
  if (!isConfigValueOfType(value, prop.type))
    return `存量值与类型 ${prop.type} 不匹配，发布后新 job 的 intake 将失败`
  return configValueConstraintError(prop, value as string | number | boolean)
}
