import type { WorkflowNodeRecord } from '../../../types'
import { parseWorkflowNode } from '../shared/workflowStudioYamlDraft'
import { patchWorkflowNodeConfigSchema } from '../shared/workflowStudioYamlDraft.configSchema'
import { validateSchemaPropertyName } from '../shared/workflowStudioYamlDraft.configSchema.helpers'
import { addWorkflowNodeSchemaProperty } from '../shared/workflowStudioYamlDraft.configSchema.properties'
import inspectorStyles from './WorkflowNodeInspector.module.css'
import styles from './WorkflowStructuredEditor.module.css'
import { WorkflowNodeConfigSchemaList } from './WorkflowNodeConfigSchemaList'
import { WorkflowNodeSchemaPropertyAdder } from './WorkflowNodeSchemaPropertyAdder'
import { WorkflowChannelBadge } from './WorkflowChannelBadge'

type Props = {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}

// code/agent 节点 revision 作用域的 config_schema 结构化编辑：属性列表
// 完整可编辑——新增、改名、改 type/description/default、删除、secret /
// secret_ref / runtime_mutable 开关，全部经 configSchema patch 写回草稿
// YAML，随发布进版本。YAML 源码编辑器仍是 enum/minimum/maximum 等低频
// 约束的兜底。列表编排/行编辑器/新增入口拆在毗邻组件守预算。
export function WorkflowNodeConfigSchemaSection({
  node,
  definitionYaml,
  setDefinitionYaml,
  readOnly,
}: Props) {
  // 类型注册表只挂 code/agent；此处第二层防线防直接渲染暴露其他类型的
  // YAML config_schema。node_type 缺失是遗留 code 节点，仍允许渲染。
  if (node.node_type && !['code', 'agent'].includes(node.node_type)) return null
  const properties =
    parseWorkflowNode(definitionYaml, node.key)?.config_schema?.properties ?? {}
  const hasKeys = Object.keys(properties).length > 0
  // 写路径失败（非法输入）不落草稿；受控输入回弹。
  const apply = (next: string) => {
    try {
      setDefinitionYaml(next)
    } catch {
      /* 回弹 */
    }
  }
  const handleAdd = (name: string): string | null => {
    const error = validateSchemaPropertyName(name, Object.keys(properties))
    if (error) return error
    apply(addWorkflowNodeSchemaProperty(definitionYaml, node.key, name.trim()))
    return null
  }

  return (
    <section
      className={inspectorStyles.section}
      aria-label={`配置 Schema ${node.key}`}
    >
      <div className={inspectorStyles.sectionTitle}>
        配置 Schema
        <WorkflowChannelBadge kind="structure" />
      </div>
      <p className={styles.fieldHint}>
        声明当前节点的可调参数，随发布进入 workflow 版本。运行开关
        （runtime_mutable）在 job intake 时不冻结，每次 dispatch 按
        运行时覆盖实时重取，适合 dry_run 这类开关；enum/范围等低频约束 仍可在
        YAML 源码编辑器里编辑。
      </p>
      <WorkflowNodeConfigSchemaList
        node={node}
        definitionYaml={definitionYaml}
        setDefinitionYaml={setDefinitionYaml}
        readOnly={readOnly}
      />
      {readOnly ? (
        <p className={styles.fieldHint}>
          历史版本查看模式下配置 Schema 不可编辑，请切回草稿视图修改。
        </p>
      ) : (
        <WorkflowNodeSchemaPropertyAdder
          propKeys={hasKeys ? Object.keys(properties) : []}
          onAdd={handleAdd}
          onRemoveSchema={() =>
            apply(
              patchWorkflowNodeConfigSchema(definitionYaml, node.key, undefined)
            )
          }
        />
      )}
    </section>
  )
}
