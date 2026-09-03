import type { ConfigSchemaProperty } from '../../../types'
import type { SchemaPropertyPatch } from '../shared/workflowStudioYamlDraft.configSchema.properties'
import {
  SCHEMA_PROPERTY_TYPES,
  parseSchemaDefaultValue,
  defaultValueMatchesType,
} from '../shared/workflowStudioYamlDraft.configSchema.helpers'
import styles from './WorkflowStructuredEditor.module.css'

type Props = {
  propKey: string
  prop: ConfigSchemaProperty
  readOnly?: boolean
  onPatch: (propKey: string, changes: SchemaPropertyPatch) => void
  onRename: (propKey: string, nextName: string) => void
  onRemove: (propKey: string) => void
}

// config_schema 单属性的编辑行（#418 面板）：名称、类型、描述、默认值、
// runtime_mutable 开关、删除。全部经 patch 回调写回草稿。从
// WorkflowNodeConfigSchemaProperties 拆出以守单文件预算。
export function WorkflowNodeSchemaPropertyRow({
  propKey,
  prop,
  readOnly,
  onPatch,
  onRename,
  onRemove,
}: Props) {
  const defaultRaw = prop.default === undefined ? '' : String(prop.default)
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
            if (next && next !== propKey) {
              onRename(propKey, next)
            }
          }}
        />
      </div>
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
              const parsed = parseSchemaDefaultValue(
                event.target.value,
                prop.type
              )
              if (
                event.target.value.trim() !== defaultRaw &&
                (parsed === undefined ||
                  defaultValueMatchesType(parsed, prop.type))
              ) {
                onPatch(propKey, { default: parsed })
              }
            }}
          />
        )}
      </div>
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
