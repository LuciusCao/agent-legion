import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { SelectedWorkflowNodeDetails } from '../shared/workflowStudioModel'
import { parseWorkflowNode } from '../shared/workflowStudioYamlDraft'
import { useNodeTypeSwitch } from './useNodeTypeSwitch'
import { WorkflowNodeInspectorHeader } from './WorkflowNodeInspectorHeader'
import { WorkflowNodeInspectorSections } from './WorkflowNodeInspectorSections'
import type { AgentCatalogSettle } from './agentBindingStatus'
import styles from './WorkflowNodeInspector.module.css'

type Props = {
  details: SelectedWorkflowNodeDetails
  agentCatalog: AgentDefinition[]
  agentCatalogSettle: AgentCatalogSettle
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
  onClose: () => void
}

export function WorkflowNodeInspectorBody(props: Props) {
  const { node } = props.details
  // 头部类型选择器（#392）接线；start 只读展示，不下发回调。切换前置
  // 校验按草稿 YAML 的当前类型判定（details 可能来自已发布基线，草稿里
  // 类型可能已被切走；#405 approval 切出据此弹窗补 capability）。
  const changeNodeType = useNodeTypeSwitch(
    props.definitionYaml,
    node.key,
    parseWorkflowNode(props.definitionYaml, node.key)?.type,
    props.setDefinitionYaml
  )
  return (
    <section aria-label="Workflow inspector" className={styles.panel}>
      <WorkflowNodeInspectorHeader
        label={node.label}
        nodeKey={node.key}
        nodeType={node.node_type}
        onNodeTypeChange={props.readOnly ? undefined : changeNodeType}
        onClose={props.onClose}
      />
      <div className={styles.content}>
        <WorkflowNodeInspectorSections {...props} />
      </div>
    </section>
  )
}
