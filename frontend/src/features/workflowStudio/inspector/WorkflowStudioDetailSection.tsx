import type { WorkflowDefinitionRecord } from '../../../types'
import type { AgentDefinition } from '../../../types/agentCatalogTypes'
import type { ChangeSummaryViewModel } from '../validation/workflowStudioChanges'
import { WorkflowNodeDetailView } from './WorkflowNodeDetailView'
import pageStyles from '../../../pages/WorkflowStudioPageResponsive.module.css'
import sidePanelStyles from '../../../pages/WorkflowStudioPageSidePanel.module.css'
import splitStyles from '../shared/WorkflowStudioSplitLayout.module.css'

type Props = {
  workflow: WorkflowDefinitionRecord | null
  nodeKey: string
  agentCatalog: AgentDefinition[]
  definitionYaml: string
  setDefinitionYaml: (value: string) => void
  compareSummary?: ChangeSummaryViewModel | null
  readOnly: boolean
  detailLeft: boolean
  mobileActive: boolean
  agentOpen: boolean
  onToggleAgent: () => void
  onBack: () => void
}

/** 节点详情的分栏容器：Agent 展开时放左半（grid-column: 1 替换 DAG），
 * 收起时放右半；移动端是「编辑节点」面板。 */
export function WorkflowStudioDetailSection(props: Props) {
  const className = [
    sidePanelStyles.sidePanel,
    pageStyles.sidePanel,
    props.detailLeft ? splitStyles.colLeft : splitStyles.colRight,
    props.mobileActive ? pageStyles.activePanel : '',
  ]
    .filter(Boolean)
    .join(' ')
  return (
    <section
      data-mobile-panel="editor"
      data-placement={props.detailLeft ? 'left' : 'right'}
      aria-label="节点详情"
      className={className}
    >
      <WorkflowNodeDetailView
        workflow={props.workflow}
        nodeKey={props.nodeKey}
        agentCatalog={props.agentCatalog}
        definitionYaml={props.definitionYaml}
        setDefinitionYaml={props.setDefinitionYaml}
        compareSummary={props.compareSummary}
        readOnly={props.readOnly}
        agentOpen={props.agentOpen}
        onToggleAgent={props.onToggleAgent}
        onBack={props.onBack}
      />
    </section>
  )
}
