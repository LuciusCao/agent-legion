import { useState } from 'react'
import type { ConfigSchemaProperty } from '../../../types'
import {
  parseConfigValue,
  formatConfigValue,
} from '../shared/workflowStudioYamlDraft.nodeConfig'
import { configValueConstraintError } from '../shared/workflowStudioYamlDraft.configSchema.constraints'
import { ConfigValueFieldLabel } from './ConfigValueFieldLabel'
import { NumberOrTextValueField } from './NumberOrTextValueField'
import styles from './WorkflowStructuredEditor.module.css'

export type ConfigValueOverride = { value: unknown } | undefined

// 单个版本值字段（#428 codex 二轮拆分，从 WorkflowNodeConfigValues 拆出
// 守单文件预算）：enum 用下拉（选项 = enum 值，P1-B）；boolean 用下拉
// （Schema 默认/true/false）；其余用失焦提交的文本输入（独立复审 P2-3）。
// 提交前过 configValueConstraintError（enum/minimum/maximum，P1-B），
// 非法值不落草稿并行内报错；被运行时覆盖遮蔽的键加徽标（P2-2）。
export function ConfigValueField({
  fieldKey,
  prop,
  raw,
  overrideValue,
  readOnly,
  onCommit,
}: {
  fieldKey: string
  prop: ConfigSchemaProperty
  raw: string
  overrideValue: ConfigValueOverride
  readOnly?: boolean
  onCommit: (next: string) => void
}) {
  const [error, setError] = useState('')
  const label = (
    <ConfigValueFieldLabel
      fieldKey={fieldKey}
      prop={prop}
      overrideValue={overrideValue}
    />
  )
  const commit = (next: string) => {
    const value = parseConfigValue(next, prop)
    const constraintError = configValueConstraintError(prop, value)
    if (constraintError) {
      setError(constraintError)
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
        {error && (
          <span className={styles.fieldHint} role="alert">
            {error}
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
        {error && (
          <span className={styles.fieldHint} role="alert">
            {error}
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
      error={error}
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

/** 字段当前展示串（config 落盘值 → 表单文本）。 */
export function configFieldRaw(
  config: Record<string, unknown>,
  key: string
): string {
  return formatConfigValue(config[key])
}
