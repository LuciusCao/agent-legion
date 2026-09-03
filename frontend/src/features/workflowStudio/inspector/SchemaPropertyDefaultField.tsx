import { useState } from 'react'
import type { ConfigSchemaProperty } from '../../../types'
import type { SchemaPropertyPatch } from '../shared/workflowStudioYamlDraft.configSchema.properties'
import { schemaDefaultCommit } from '../shared/workflowStudioYamlDraft.configSchema.defaultCommit'
import styles from './WorkflowStructuredEditor.module.css'

// config_schema 单属性的默认值编辑器（#428 复审拆分，从
// WorkflowNodeSchemaPropertyRow 拆出守单文件预算）：boolean 用下拉
// （无默认/true/false），其余类型用失焦提交的文本输入（聚焦持本地草稿
// 串、失焦回显外部值——类型切换 strip 掉 default 后显示随之清空，
// #428 二轮复审 NIT-2a）。提交经 schemaDefaultCommit 完整校验（解析 →
// 类型 → enum/边界，codex 二轮 P2）：非法不落草稿并行内报错——enum 外/
// 越界的默认值会被 loader 拒绝整份草稿。
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
  const [draft, setDraft] = useState('')
  const [focused, setFocused] = useState(false)
  const defaultRaw = prop.default === undefined ? '' : String(prop.default)
  const value = focused ? draft : defaultRaw
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
          value={value}
          disabled={readOnly}
          placeholder={defaultRaw ? undefined : '（无默认）'}
          onFocus={(event) => {
            setFocused(true)
            setDraft(event.target.value)
          }}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={() => {
            setFocused(false)
            if (draft.trim() === defaultRaw) return setDefaultError('')
            const { error, parsed } = schemaDefaultCommit(draft, prop)
            setDefaultError(error ?? '')
            if (!error) onPatch(propKey, { default: parsed })
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
