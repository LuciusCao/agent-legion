import type { WorkflowDefinitionRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'
import { WorkflowNodeDetailView } from './WorkflowNodeDetailView'
import type { AgentCatalogSettle } from './agentBindingStatus'
import pageStyles from '../../../pages/WorkflowStudioPageResponsive.module.css'
import sidePanelStyles from '../../../pages/WorkflowStudioPageSidePanel.module.css'
import splitStyles from '../shared/WorkflowStudioSplitLayout.module.css'

type Props = {
  workflow: WorkflowDefinitionRecord | null
  nodeKey: string
  agentCatalog: AgentDefinition[]
  agentCatalogSettle: AgentCatalogSettle
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  compareSummary?: ChangeSummaryViewModel | null
  readOnly: boolean
  mobileActive: boolean
  onBack: () => void
}

/** 节点详情的分栏容器：固定放右半（grid-column: 3，DAG 保留在左——
 * #795 PR② Agent 对话迁入 Dock 浮层后不再有「详情替换画布」模式）；
 * 移动端是「编辑节点」面板。 */
export function WorkflowStudioDetailSection(props: Props) {
  const className = [
    sidePanelStyles.sidePanel,
    pageStyles.sidePanel,
    splitStyles.colRight,
    props.mobileActive ? pageStyles.activePanel : '',
  ]
    .filter(Boolean)
    .join(' ')
  return (
    <section
      data-mobile-panel="editor"
      data-placement="right"
      aria-label="节点详情"
      className={className}
    >
      <WorkflowNodeDetailView
        workflow={props.workflow}
        nodeKey={props.nodeKey}
        agentCatalog={props.agentCatalog}
        agentCatalogSettle={props.agentCatalogSettle}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        compareSummary={props.compareSummary}
        readOnly={props.readOnly}
        onBack={props.onBack}
      />
    </section>
  )
}
