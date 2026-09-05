import { useState } from 'react'
import type { ConfigSchemaProperty } from '../../../types'
import { formatConfigValue } from '../shared/workflowStudioYamlDraft.nodeConfig'
import {
  configValueCommitError,
  storedConfigValueError,
} from '../shared/workflowStudioYamlDraft.configValueValidation'
import { ConfigValueFieldLabel } from './ConfigValueFieldLabel'
import { NumberOrTextValueField } from './NumberOrTextValueField'
import styles from './WorkflowStructuredEditor.module.css'

export type ConfigValueOverride = { value: unknown } | undefined

// 单个版本值字段（#428 codex 二轮拆分，从 WorkflowNodeConfigValues 拆出
// 守单文件预算）：enum 用下拉（选项 = enum 值，P1-B）；boolean 用下拉
// （Schema 默认/true/false）；其余用失焦提交的文本输入（独立复审 P2-3）。
// 提交与存量校验拆在 configValueValidation（三轮复审 P3-3/P3-4 拆出守
// 预算）：不可解析的数字输入行内报错不删键（对齐默认值编辑器 NIT-2b，
// 显式清空才是删键路径）；存量值按落盘类型值判定——经表单串往返会抹掉
// 类型信息（string 属性塞 42 / number 属性塞 '20' 显示正常无提示，发布
// 后 intake 的 _type_matches 才 raise）。enum/边界/整数性与存量非法值
// 行内提示（codex 二轮 P1 + 二轮复审 P3-1）；被运行时覆盖遮蔽的键加
// 徽标（P2-2）。
export function ConfigValueField({
  fieldKey,
  prop,
  storedValue,
  overrideValue,
  readOnly,
  onCommit,
}: {
  fieldKey: string
  prop: ConfigSchemaProperty
  storedValue: unknown
  overrideValue: ConfigValueOverride
  readOnly?: boolean
  onCommit: (next: string) => void
}) {
  const [error, setError] = useState('')
  const raw = formatConfigValue(storedValue)
  // 存量值（config 落盘值）跑同一约束校验：只提示不阻塞，提交路径仍以
  // error 状态优先。
  const storedError = storedConfigValueError(prop, storedValue)
  const displayedError = error || storedError || ''
  const label = (
    <ConfigValueFieldLabel
      fieldKey={fieldKey}
      prop={prop}
      overrideValue={overrideValue}
    />
  )
  const commit = (next: string) => {
    const commitError = configValueCommitError(next, prop)
    if (commitError) {
      setError(commitError)
      return
    }
    setError('')
    onCommit(next)
  }
  if (prop.enum === undefined && prop.type === 'boolean') {
    return (
      <label className={styles.field}>
        {label}
        <select
          aria-label={`版本值 ${fieldKey}`}
          className={styles.fieldInput}
          value={raw}
          disabled={readOnly}
          onChange={(event) => commit(event.target.value)}
        >
          <option value="">（Schema 默认）</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
        {displayedError && (
          <span className={styles.fieldHint} role="alert">
            {displayedError}
          </span>
        )}
      </label>
    )
  }
  if (prop.enum !== undefined) {
    return (
      <label className={styles.field}>
        {label}
        <select
          aria-label={`版本值 ${fieldKey}`}
          className={styles.fieldInput}
          value={raw}
          disabled={readOnly}
          onChange={(event) => commit(event.target.value)}
        >
          <option value="">（Schema 默认）</option>
          {prop.enum.map((option) => (
            <option key={String(option)} value={String(option)}>
              {String(option)}
            </option>
          ))}
        </select>
        {displayedError && (
          <span className={styles.fieldHint} role="alert">
            {displayedError}
          </span>
        )}
      </label>
    )
  }
  return (
    <NumberOrTextValueField
      fieldKey={fieldKey}
      label={label}
      raw={raw}
      readOnly={readOnly}
      onCommit={commit}
      error={displayedError}
    />
  )
}

/** 供消费方组装 overrideValue 的便捷封装（与 ConfigValueFieldLabel 对齐）。 */
export function configOverrideValueOf(
  liveOverrides: Record<string, unknown> | undefined,
  key: string
): ConfigValueOverride {
  return liveOverrides != null && key in liveOverrides
    ? { value: liveOverrides[key] }
    : undefined
}
