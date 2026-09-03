import type { WorkflowNodeRecord } from '../../../types'
import { parseWorkflowNode } from '../shared/workflowStudioYamlDraft'
import { patchWorkflowNodeConfigSchema } from '../shared/workflowStudioYamlDraft.configSchema'
import inspectorStyles from './WorkflowNodeInspector.module.css'
import styles from './WorkflowStructuredEditor.module.css'
import { WorkflowNodeConfigSchemaProperties } from './WorkflowNodeConfigSchemaProperties'

type Props = {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

// Code 节点 revision 作用域的 config_schema 结构化编辑：属性列表只读展示，
// 唯一可编辑项是「运行开关」（runtime_mutable）。完整 schema 编辑
// （新增属性、改描述/默认值）仍走 YAML 源码编辑器。Agent
// 节点的 schema 归 Agent Definition 管理，不属于本区块（#406）。
export function WorkflowNodeConfigSchemaSection({
  node,
  definitionYaml,
  setDefinitionYaml,
  readOnly,
}: Props) {
  // 类型注册表只会把本区块挂到 code 节点；这里保留第二层
  // 防线，避免组件被直接渲染时暴露其他节点类型不生效的 YAML
  // config_schema。node_type 缺失是遗留 code 节点，仍允许渲染。
  if (node.node_type && node.node_type !== 'code') return null
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
    </section>
  )
}
