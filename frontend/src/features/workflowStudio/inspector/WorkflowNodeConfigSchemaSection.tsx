import type { ConfigSchemaProperty, WorkflowNodeRecord } from '../../../types'
import { parseWorkflowNode } from '../shared/workflowStudioYamlDraft'
import { patchWorkflowNodeConfigSchema } from '../shared/workflowStudioYamlDraft.configSchema'
import inspectorStyles from './WorkflowNodeInspector.module.css'
import styles from './WorkflowStructuredEditor.module.css'

type Props = {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

function formatDefault(prop: ConfigSchemaProperty): string {
  if (prop.default === undefined) return '—'
  return String(prop.default)
}

// Revision 作用域的节点 config_schema 结构化编辑：属性列表只读展示，
// 唯一可编辑项是「运行开关」（runtime_mutable）。完整 schema 编辑
// （新增属性、改描述/默认值）仍走 YAML 源码编辑器。
export function WorkflowNodeConfigSchemaSection({
  node,
  definitionYaml,
  setDefinitionYaml,
  readOnly,
}: Props) {
  const schema = parseWorkflowNode(definitionYaml, node.key)?.config_schema
  const properties = schema?.properties ?? {}
  const keys = Object.keys(properties)

  const handleRuntimeMutableChange = (propKey: string, checked: boolean) => {
    const current = parseWorkflowNode(definitionYaml, node.key)?.config_schema
    const prop = current?.properties?.[propKey]
    if (!current || !prop) return
    const next = structuredClone(current)
    const nextProp = next.properties![propKey]
    if (checked) {
      nextProp.runtime_mutable = true
    } else {
      // false 不落地：取消勾选即从属性里删掉 runtime_mutable 键。
      delete nextProp.runtime_mutable
    }
    setDefinitionYaml(
      patchWorkflowNodeConfigSchema(definitionYaml, node.key, next)
    )
  }

  return (
    <section
      className={inspectorStyles.section}
      aria-label={`配置 Schema ${node.key}`}
    >
      <div className={inspectorStyles.sectionTitle}>配置 Schema</div>
      <p className={styles.fieldHint}>
        运行开关（runtime_mutable）在 job intake 时不冻结，每次 dispatch 按
        workspace 节点配置实时重取，适合 dry_run 这类开关。完整 schema 编辑
        （新增属性、改描述/默认值）请用 YAML 源码编辑器。
      </p>
      {keys.length === 0 ? (
        <div className={inspectorStyles.empty}>
          该节点未声明 config_schema；可在 YAML 源码编辑器中为节点添加。
        </div>
      ) : (
        <div className={styles.fieldStack}>
          {keys.map((propKey) => {
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
                        handleRuntimeMutableChange(
                          propKey,
                          event.target.checked
                        )
                      }
                    />{' '}
                    运行开关
                  </label>
                )}
              </div>
            )
          })}
        </div>
      )}
      {readOnly && (
        <p className={styles.fieldHint}>
          历史版本查看模式下配置 Schema 不可编辑，请切回草稿视图修改。
        </p>
      )}
    </section>
  )
}
