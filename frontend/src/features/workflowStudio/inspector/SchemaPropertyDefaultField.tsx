import { useState } from 'react'
import type { ConfigSchemaProperty } from '../../../types'
import type { SchemaPropertyPatch } from '../shared/workflowStudioYamlDraft.configSchema.properties'
import {
  parseSchemaDefaultValue,
  defaultValueMatchesType,
} from '../shared/workflowStudioYamlDraft.configSchema.helpers'
import styles from './WorkflowStructuredEditor.module.css'

// config_schema 单属性的默认值编辑器（#428 复审拆分，从
// WorkflowNodeSchemaPropertyRow 拆出守单文件预算）：boolean 用下拉
// （无默认/true/false），其余类型用失焦提交的文本输入。类型不匹配的
// 输入（如 integer 输 1.5）不落草稿，行内报错（#428 复审 NIT）。
// 非受控输入框以落盘 default 为 key：外部 default 变化（含类型切换被
// strip 清空）时整体 remount，显示随之重置，不再残留旧串（#428 二轮
// 复审 NIT-2a）。
export function SchemaPropertyDefaultField({
  propKey,
  prop,
  readOnly,
  onPatch,
}: {
  propKey: string
  prop: ConfigSchemaProperty
  readOnly?: boolean
  onPatch: (propKey: string, changes: SchemaPropertyPatch) => void
}) {
  const [defaultError, setDefaultError] = useState('')
  const defaultRaw = prop.default === undefined ? '' : String(prop.default)
  return (
    <div className={styles.field}>
      <span className={styles.fieldLabel}>默认值</span>
      {prop.type === 'boolean' ? (
        <select
          aria-label={`默认值 ${propKey}`}
          className={styles.fieldInput}
          value={defaultRaw}
          disabled={readOnly}
          onChange={(event) =>
            onPatch(propKey, {
              default:
                event.target.value === ''
                  ? undefined
                  : event.target.value === 'true',
            })
          }
        >
          <option value="">（无默认）</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      ) : (
        <input
          key={defaultRaw}
          aria-label={`默认值 ${propKey}`}
          className={styles.fieldInput}
          defaultValue={defaultRaw}
          disabled={readOnly}
          placeholder={defaultRaw ? undefined : '（无默认）'}
          onBlur={(event) => {
            const raw = event.target.value
            if (raw.trim() === defaultRaw) return setDefaultError('')
            const parsed = parseSchemaDefaultValue(raw, prop.type)
            if (
              parsed !== undefined &&
              !defaultValueMatchesType(parsed, prop.type)
            ) {
              setDefaultError(`默认值与类型 ${prop.type} 不匹配，未写入`)
              return
            }
            setDefaultError('')
            onPatch(propKey, { default: parsed })
          }}
        />
      )}
      {defaultError && (
        <span className={styles.fieldHint} role="alert">
          {defaultError}
        </span>
      )}
    </div>
  )
}
