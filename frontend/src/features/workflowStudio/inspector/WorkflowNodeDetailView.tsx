import type { WorkflowDefinitionRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'
import { WorkflowNodeDetailBody } from './WorkflowNodeDetailBody'
import { useNodeDetailPreview } from './useNodeDetailPreview'
import { selectedNodeDetails } from '../shared/workflowStudioModel'
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

/** 节点详情视图：面包屑（工作流 / 节点 [/ 预览]）+ 分级返回 + inspector 内容
 * 平铺。预览状态（useNodeDetailPreview，nodeKey 变化即清除）使面包屑随预览态
 * 加深、返回按钮分级（预览中→回节点详情，否则→回 DAG），预览面板自身不再有
 * 第二层导航。Agent 面板展开时占左半（替换 DAG），收起时占右半。 */
export function WorkflowNodeDetailView(props: Props) {
  const preview = useNodeDetailPreview(props.nodeKey)
  const node = selectedNodeDetails(props.workflow, props.nodeKey)?.node
  const workflowLabel = props.workflow?.label || props.workflow?.key || ''
  return (
    <div className={styles.detail}>
      <div className={styles.breadcrumbBar}>
        <button
          type="button"
          className={styles.back}
          onClick={preview.activeKind ? preview.closePreview : props.onBack}
          aria-label={preview.activeKind ? '返回节点详情' : '返回 DAG'}
        >
          ← 返回
        </button>
        <span className={styles.breadcrumb}>
          {workflowLabel} / {node?.label ?? props.nodeKey}
          {preview.activeLabel ? ` / ${preview.activeLabel}` : ''}
        </span>
        <StudioAgentPanelToggle
          open={props.agentOpen}
          onToggle={props.onToggleAgent}
        />
      </div>
      <div className={styles.body}>
        <WorkflowNodeDetailBody
          workflow={props.workflow}
          nodeKey={props.nodeKey}
          agentCatalog={props.agentCatalog}
          definitionYaml={props.definitionYaml}
          setDefinitionYaml={props.setDefinitionYaml}
          compareSummary={props.compareSummary}
          readOnly={props.readOnly}
          activeKind={preview.activeKind}
          onShowPreview={preview.showPreview}
          onClose={props.onBack}
        />
      </div>
    </div>
  )
}
