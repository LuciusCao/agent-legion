import type { WorkflowNodeRecord } from '../../../types'
import { parseWorkflowNode } from '../shared/workflowStudioYamlDraft'
import { readNodeConfig } from '../shared/workflowStudioYamlDraft.nodeConfig'
import inspectorStyles from './WorkflowNodeInspector.module.css'
import { WorkflowNodeConfigValues } from './WorkflowNodeConfigValues'
import { WorkflowNodeRuntimeOverrideCard } from './WorkflowNodeRuntimeOverrideCard'

// code 节点配置值的双通道区块（#418 后半）：
// - 版本值（draft YAML 的 node config，表单在 WorkflowNodeConfigValues）：
//   随发布进入新 revision，job intake 时按「schema 默认 → 节点 config →
//   运行时覆盖」解析后冻结进 job 快照；
// - 运行时覆盖（workspace node_config，卡片在
//   WorkflowNodeRuntimeOverrideCard）：立即生效、不产生新版本——非
//   runtime_mutable 键影响之后 intake 的新 job，runtime_mutable 键对已在
//   跑的 job 下一次 dispatch 即生效（CONFIG-RUNTIME-MUTABLE-001）。
// registry 只挂 code/agent；agent 节点的 config_schema / config 值同样经
// 节点 YAML 声明（EXEC-AGENT-PROFILE-001），本区块暂不为其提供版本值
// 表单（走 YAML 源码编辑），但 live 覆盖通道对 agent 同样有效（schema
// 取自服务端解析的节点执行档案）。
export function WorkflowNodeConfigSection(props: {
  node: WorkflowNodeRecord
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
}) {
  const { node, definitionYaml, setDefinitionYaml, readOnly } = props
  // code 节点：版本值表单按草稿 YAML 的 config_schema 生成；agent 节点
  // 的 schema 虽也在节点 YAML，本区块暂不渲染其版本值表单。
  const isCodeNode = !node.node_type || node.node_type === 'code'
  const draftSchema = isCodeNode
    ? parseWorkflowNode(definitionYaml, node.key)?.config_schema
    : undefined
  const hasSchemaKeys = Object.keys(draftSchema?.properties ?? {}).length > 0

  return (
    <section
      className={inspectorStyles.section}
      aria-label={`节点配置 ${node.key}`}
    >
      <div className={inspectorStyles.sectionTitle}>节点配置</div>
      {isCodeNode && hasSchemaKeys ? (
        <WorkflowNodeConfigValues
          nodeKey={node.key}
          schema={draftSchema!}
          config={readNodeConfig(definitionYaml, node.key)}
          definitionYaml={definitionYaml}
          setDefinitionYaml={setDefinitionYaml}
          readOnly={readOnly}
        />
      ) : isCodeNode ? (
        <div className={inspectorStyles.empty}>
          未声明 config_schema 的节点没有可配置参数；如需参数请先在 「配置
          Schema」区块声明。
        </div>
      ) : null}
      <WorkflowNodeRuntimeOverrideCard node={node} readOnly={readOnly} />
    </section>
  )
}
