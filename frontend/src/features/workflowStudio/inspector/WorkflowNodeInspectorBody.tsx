import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import { useUiStore } from '../../../stores/uiStore'
import type { SelectedWorkflowNodeDetails } from '../shared/workflowStudioModel'
import {
  patchWorkflowNodeType,
  type SwitchableNodeType,
} from '../shared/workflowStudioYamlDraft.nodeType'
import { WorkflowNodeInspectorHeader } from './WorkflowNodeInspectorHeader'
import { WorkflowNodeInspectorSections } from './WorkflowNodeInspectorSections'
import styles from './WorkflowNodeInspector.module.css'

type Props = {
  details: SelectedWorkflowNodeDetails
  agentCatalog: AgentDefinition[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  readOnly?: boolean
  onClose: () => void
}

export function WorkflowNodeInspectorBody(props: Props) {
  const { node } = props.details
  const showToast = useUiStore((s) => s.showToast)
  // 头部类型选择器（#392）：改写草稿 YAML 的节点 type 并按目标类型清洗
  // 字段；改写失败降级提示手动改 YAML。start 只读展示，不下发回调。
  const changeNodeType = (nodeType: SwitchableNodeType) => {
    try {
      props.setDefinitionYaml(
        patchWorkflowNodeType(props.definitionYaml, node.key, nodeType)
      )
      return true
    } catch {
      showToast(
        `类型切换失败；请手动在 YAML 将节点 type 改为 ${nodeType}`,
        'error'
      )
      return false
    }
  }
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
