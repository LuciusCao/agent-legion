import type { WorkflowDefinitionRecord } from '../../types'
import type { AgentDefinition } from '../../types/executorTypes'
import type { ChangeSummaryViewModel } from './workflowStudioChanges'
import { WorkflowNodeInspector } from './WorkflowNodeInspector'
import { selectedNodeDetails } from './workflowStudioModel'
import { StudioAgentPanelToggle } from './StudioAgentPanelToggle'
import styles from './WorkflowNodeDetailView.module.css'

type Props = {
  workflow: WorkflowDefinitionRecord | null
  nodeKey: string
  agentCatalog: AgentDefinition[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  compareSummary?: ChangeSummaryViewModel | null
  readOnly: boolean
  agentOpen: boolean
  onToggleAgent: () => void
  onBack: () => void
}

/** 节点详情视图：面包屑（工作流 / 节点）+ 返回 DAG + inspector 内容平铺。
 * Agent 面板展开时占左半（替换 DAG），收起时占右半。 */
export function WorkflowNodeDetailView(props: Props) {
  const node = selectedNodeDetails(props.workflow, props.nodeKey)?.node
  const workflowLabel = props.workflow?.label || props.workflow?.key || ''
  return (
    <div className={styles.detail}>
      <div className={styles.breadcrumbBar}>
        <button
          type="button"
          className={styles.back}
          onClick={props.onBack}
          aria-label="返回 DAG"
        >
          ← 返回
        </button>
        <span className={styles.breadcrumb}>
          {workflowLabel} / {node?.label ?? props.nodeKey}
        </span>
        <StudioAgentPanelToggle
          open={props.agentOpen}
          onToggle={props.onToggleAgent}
        />
      </div>
      <div className={styles.body}>
        <WorkflowNodeInspector
          workflow={props.workflow}
          agentCatalog={props.agentCatalog}
          selectedNodeKey={props.nodeKey}
          definitionYaml={props.definitionYaml}
          setDefinitionYaml={props.setDefinitionYaml}
          compareSummary={props.compareSummary}
          readOnly={props.readOnly}
          onClose={props.onBack}
        />
      </div>
    </div>
  )
}
