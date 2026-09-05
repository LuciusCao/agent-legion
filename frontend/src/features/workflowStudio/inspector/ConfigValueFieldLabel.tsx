import type { ConfigSchemaProperty } from '../../../types'
import { formatConfigValue } from '../shared/workflowStudioYamlDraft.nodeConfig'
import styles from './WorkflowStructuredEditor.module.css'

// 版本值表单的字段标签（#428 复审拆分，从 WorkflowNodeConfigValues 拆出
// 守单文件预算）：键名 + 类型 + Schema 默认值；被 workspace 运行时覆盖
// 遮蔽的键（P2-2）加徽标并 tooltip 说明实际生效值——覆盖优先级更高，
// intake 冻结的是覆盖值，不是表单里的版本值。
export function ConfigValueFieldLabel({
  fieldKey,
  prop,
  overrideValue,
}: {
  fieldKey: string
  prop: ConfigSchemaProperty
  overrideValue: { value: unknown } | undefined
}) {
  const shadowed = overrideValue !== undefined
  return (
    <span className={styles.fieldLabel}>
      {fieldKey}（{prop.type}，默认{' '}
      {prop.default === undefined ? '—' : String(prop.default)}）
      {shadowed && (
        <span
          title={`该键已被 workspace 运行时覆盖遮蔽（当前覆盖值：${formatConfigValue(
            overrideValue!.value
          )}）；intake 冻结的是覆盖值，修改版本值需先清除覆盖`}
          style={{
            marginLeft: 6,
            fontSize: 11,
            color: '#6a4c00',
            background: '#fff3cd',
            borderRadius: 4,
            padding: '1px 5px',
          }}
        >
          已被运行时覆盖
        </span>
      )}
    </span>
  )
}
