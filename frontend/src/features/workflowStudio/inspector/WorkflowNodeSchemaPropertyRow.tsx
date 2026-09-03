import { useState } from 'react'
import type { ConfigSchemaProperty } from '../../../types'
import type { SchemaPropertyPatch } from '../shared/workflowStudioYamlDraft.configSchema.properties'
import { SCHEMA_PROPERTY_TYPES } from '../shared/workflowStudioYamlDraft.configSchema.helpers'
import { validateSchemaPropertyRename } from '../shared/workflowStudioYamlDraft.configSchema.helpers'
import { SchemaPropertyDefaultField } from './SchemaPropertyDefaultField'
import styles from './WorkflowStructuredEditor.module.css'

type Props = {
  propKey: string
  prop: ConfigSchemaProperty
  /** 同节点其余属性名——改名撞名/撞保留键的校验依据（#428 复审 P2-1）。 */
  otherKeys: string[]
  readOnly?: boolean
  onPatch: (propKey: string, changes: SchemaPropertyPatch) => void
  onRename: (propKey: string, nextName: string) => void
  onRemove: (propKey: string) => void
}

// config_schema 单属性的编辑行（#418 面板）：名称、类型、描述、默认值、
// runtime_mutable 开关、删除。全部经 patch 回调写回草稿。从
// WorkflowNodeConfigSchemaProperties 拆出以守单文件预算。
// 改名先过 validateSchemaPropertyRename：重名静默覆盖、保留键、含空白
// 名都不落草稿，行内报错（与 Adder 的错误显示模式一致）；默认值类型
// 不匹配同样行内报错不落盘（#428 复审 NIT）。类型选择器拆在
// SchemaPropertyTypeSelect、默认值编辑器拆在 SchemaPropertyDefaultField。
export function WorkflowNodeSchemaPropertyRow({
  propKey,
  prop,
  otherKeys,
  readOnly,
  onPatch,
  onRename,
  onRemove,
}: Props) {
  const [renameError, setRenameError] = useState('')
  return (
    <div className={styles.fieldGroup}>
      <div className={styles.field}>
        <span className={styles.fieldLabel}>属性名</span>
        <input
          aria-label={`属性名 ${propKey}`}
          className={styles.fieldInput}
          defaultValue={propKey}
          disabled={readOnly}
          onBlur={(event) => {
            const next = event.target.value.trim()
            if (!next || next === propKey) {
              setRenameError('')
              return
            }
            const error = validateSchemaPropertyRename(next, propKey, otherKeys)
            if (error) {
              setRenameError(error)
              event.target.value = propKey
              return
            }
            setRenameError('')
            onRename(propKey, next)
          }}
        />
        {renameError && (
          <span className={styles.fieldHint} role="alert">
            {renameError}
          </span>
        )}
      </div>
      <SchemaPropertyTypeSelect
        propKey={propKey}
        prop={prop}
        readOnly={readOnly}
        onPatch={onPatch}
      />
      <div className={styles.field}>
        <span className={styles.fieldLabel}>描述</span>
        <input
          aria-label={`描述 ${propKey}`}
          className={styles.fieldInput}
          defaultValue={prop.description ?? ''}
          disabled={readOnly}
          placeholder={prop.description ? undefined : '（无描述）'}
          onBlur={(event) =>
            onPatch(propKey, { description: event.target.value })
          }
        />
      </div>
      <SchemaPropertyDefaultField
        propKey={propKey}
        prop={prop}
        readOnly={readOnly}
        onPatch={onPatch}
      />
      <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <input
          type="checkbox"
          aria-label={`运行开关 ${propKey}`}
          checked={prop.runtime_mutable === true}
          disabled={readOnly}
          onChange={(event) =>
            onPatch(propKey, { runtimeMutable: event.target.checked })
          }
        />{' '}
        运行开关（runtime_mutable）
      </label>
      {!readOnly && (
        <button
          type="button"
          aria-label={`删除属性 ${propKey}`}
          onClick={() => onRemove(propKey)}
        >
          删除属性
        </button>
      )}
    </div>
  )
}

// 类型选择器（#428 复审拆分）：限定后端子集四选一。
export function SchemaPropertyTypeSelect({
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
  return (
    <div className={styles.field}>
      <span className={styles.fieldLabel}>类型</span>
      <select
        aria-label={`类型 ${propKey}`}
        className={styles.fieldInput}
        value={prop.type}
        disabled={readOnly}
        onChange={(event) =>
          onPatch(propKey, {
            type: event.target.value as ConfigSchemaProperty['type'],
          })
        }
      >
        {SCHEMA_PROPERTY_TYPES.map((type) => (
          <option key={type} value={type}>
            {type}
          </option>
        ))}
      </select>
    </div>
  )
}
