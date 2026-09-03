import type { ConfigSchema } from '../../../types'
import {
  parseConfigValue,
  formatConfigValue,
} from '../shared/workflowStudioYamlDraft.nodeConfig'
import { patchWorkflowNodeConfigValue } from '../shared/workflowStudioYamlDraft.nodeConfig'
import styles from './WorkflowStructuredEditor.module.css'

type Props = {
  nodeKey: string
  schema: ConfigSchema
  config: Record<string, unknown>
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

// code 节点 revision 作用域的 config 版本值表单（#418 后半）：按草稿
// config_schema 的属性生成控件，值写 draft YAML 的 node config，随发布
// 进入新版本。boolean 用下拉（Schema 默认/true/false）；未填写的键
// 沿用 Schema 默认值。从 WorkflowNodeConfigSection 拆出守单文件预算。
export function WorkflowNodeConfigValues({
  nodeKey,
  schema,
  config,
  definitionYaml,
  setDefinitionYaml,
  readOnly,
}: Props) {
  const keys = Object.keys(schema.properties ?? {}).filter((key) => {
    const prop = schema.properties?.[key]
    return prop != null && typeof prop === 'object'
  })
  const patchValue = (key: string, raw: string) => {
    const prop = schema.properties?.[key]
    if (!prop) return
    try {
      setDefinitionYaml(
        patchWorkflowNodeConfigValue(
          definitionYaml,
          nodeKey,
          key,
          parseConfigValue(raw, prop)
        )
      )
    } catch {
      // 非法输入不落草稿；受控输入回弹。
    }
  }

  return (
    <>
      <p className={styles.fieldHint}>
        版本值：写入 workflow 定义，随发布进入新版本；job 在 intake 时
        按此值冻结。未填写的键沿用 Schema 默认值。
      </p>
      <div className={styles.fieldStack}>
        {keys.map((key) => {
          const prop = schema.properties![key]
          const raw = formatConfigValue(config[key])
          return prop.type === 'boolean' ? (
            <label key={key} className={styles.field}>
              <span className={styles.fieldLabel}>
                {key}（boolean，默认{' '}
                {prop.default === undefined ? '—' : String(prop.default)}）
              </span>
              <select
                aria-label={`版本值 ${key}`}
                className={styles.fieldInput}
                value={raw}
                disabled={readOnly}
                onChange={(event) => patchValue(key, event.target.value)}
              >
                <option value="">（Schema 默认）</option>
                <option value="true">true</option>
                <option value="false">false</option>
              </select>
            </label>
          ) : (
            <label key={key} className={styles.field}>
              <span className={styles.fieldLabel}>
                {key}（{prop.type}，默认{' '}
                {prop.default === undefined ? '—' : String(prop.default)}）
              </span>
              <input
                aria-label={`版本值 ${key}`}
                className={styles.fieldInput}
                value={raw}
                disabled={readOnly}
                placeholder="（Schema 默认）"
                onChange={(event) => patchValue(key, event.target.value)}
              />
            </label>
          )
        })}
      </div>
    </>
  )
}
