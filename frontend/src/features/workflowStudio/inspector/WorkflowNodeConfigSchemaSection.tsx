import type { WorkflowNodeRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import { parseWorkflowNode } from '../shared/workflowStudioYamlDraft'
import { patchWorkflowNodeConfigSchema } from '../shared/workflowStudioYamlDraft.configSchema'
import inspectorStyles from './WorkflowNodeInspector.module.css'
import styles from './WorkflowStructuredEditor.module.css'
import { WorkflowNodeConfigSchemaProperties } from './WorkflowNodeConfigSchemaProperties'

type Props = {
  node: WorkflowNodeRecord
  agentCatalog: AgentDefinition[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

// Revision 作用域的节点 config_schema 结构化编辑：属性列表只读展示，
// 唯一可编辑项是「运行开关」（runtime_mutable）。完整 schema 编辑
// （新增属性、改描述/默认值）仍走 YAML 源码编辑器。
export function WorkflowNodeConfigSchemaSection({
  node,
  agentCatalog,
  definitionYaml,
  setDefinitionYaml,
  readOnly,
}: Props) {
  // capability 已有 published Agent 时生效 schema 以 Agent 定义为准，
  // 节点 YAML 的 config_schema 不参与解析（node_config.py），只给指引。
  const agentBacked = agentCatalog.some(
    (definition) => definition.capability === node.capability
  )
  const schema = parseWorkflowNode(definitionYaml, node.key)?.config_schema
  const properties = schema?.properties ?? {}
  // YAML 编辑中间态（刚输入 `foo:` 尚未补内容）会把属性解析为 null，
  // 渲染前过滤无效项，避免访问 prop.type 抛 TypeError 拖垮整个 Studio。
  const keys = Object.keys(properties).filter(
    (propKey) =>
      properties[propKey] != null && typeof properties[propKey] === 'object'
  )

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
      {agentBacked ? (
        <p className={styles.fieldHint}>
          该节点由 Agent 执行，生效的配置 Schema 以 Agent 定义为准，节点 YAML
          中的 config_schema 不参与解析；请在 Agent 定义中维护 config_schema。
        </p>
      ) : (
        <>
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
            <WorkflowNodeConfigSchemaProperties
              properties={properties}
              propKeys={keys}
              readOnly={readOnly}
              onRuntimeMutableChange={handleRuntimeMutableChange}
            />
          )}
          {readOnly && (
            <p className={styles.fieldHint}>
              历史版本查看模式下配置 Schema 不可编辑，请切回草稿视图修改。
            </p>
          )}
        </>
      )}
    </section>
  )
}
