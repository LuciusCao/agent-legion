import type { ConfigSchemaProperty } from '../../../types'
import styles from './WorkflowStructuredEditor.module.css'

type Props = {
  properties: Record<string, ConfigSchemaProperty>
  propKeys: string[]
  readOnly?: boolean
  onRuntimeMutableChange: (propKey: string, checked: boolean) => void
}

function formatDefault(prop: ConfigSchemaProperty): string {
  if (prop.default === undefined) return '—'
  return String(prop.default)
}

// 节点 config_schema 的属性列表：只读展示 type/默认值，唯一可编辑项是
// 「运行开关」（runtime_mutable）勾选。从 WorkflowNodeConfigSchemaSection
// 拆出以控制单文件体积。
export function WorkflowNodeConfigSchemaProperties({
  properties,
  propKeys,
  readOnly,
  onRuntimeMutableChange,
}: Props) {
  return (
    <div className={styles.fieldStack}>
      {propKeys.map((propKey) => {
        const prop = properties[propKey]
        return (
          <div key={propKey} className={styles.fieldGroup}>
            <span className={styles.fieldLabel}>
              {propKey}（{prop.type}，默认 {formatDefault(prop)}）
            </span>
            {readOnly ? (
              prop.runtime_mutable ? (
                <span className={styles.fieldHint}>运行开关</span>
              ) : null
            ) : (
              <label>
                <input
                  type="checkbox"
                  aria-label={`运行开关 ${propKey}`}
                  checked={prop.runtime_mutable === true}
                  onChange={(event) =>
                    onRuntimeMutableChange(propKey, event.target.checked)
                  }
                />{' '}
                运行开关
              </label>
            )}
          </div>
        )
      })}
    </div>
  )
}
