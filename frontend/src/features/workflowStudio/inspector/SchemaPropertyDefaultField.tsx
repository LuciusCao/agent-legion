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
// 输入（如 integer 输 1.5）不落草稿，行内报错（#428 复审 NIT——原实现
// 静默丢弃）。
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
          aria-label={`默认值 ${propKey}`}
          className={styles.fieldInput}
          defaultValue={defaultRaw}
          disabled={readOnly}
          placeholder={defaultRaw ? undefined : '（无默认）'}
          onBlur={(event) => {
            if (event.target.value.trim() === defaultRaw) {
              setDefaultError('')
              return
            }
            const parsed = parseSchemaDefaultValue(
              event.target.value,
              prop.type
            )
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
