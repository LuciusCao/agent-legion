import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import { useUiStore } from '../../../stores/uiStore'
import type { SelectedWorkflowNodeDetails } from '../shared/workflowStudioModel'
import {
  patchWorkflowNodeType,
  WorkflowNodeTypeSwitchError,
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
  // 头部类型选择器（#392）：先做目标类型前置校验（capability/入边），
  // 通过后改写草稿 YAML 并按目标类型清洗字段。校验失败 toast 提示并
  // 保留原类型；start 只读展示，不下发回调。
  const changeNodeType = (nodeType: SwitchableNodeType) => {
    try {
      props.setDefinitionYaml(
        patchWorkflowNodeType(props.definitionYaml, node.key, nodeType)
      )
      return true
    } catch (error) {
      if (error instanceof WorkflowNodeTypeSwitchError) {
        showToast(error.message, 'error')
      } else {
        showToast(
          `类型切换失败；请手动在 YAML 将节点 type 改为 ${nodeType}`,
          'error'
        )
      }
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
