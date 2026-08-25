import { StudioChatAside } from './StudioChatAside'
import { WorkflowStudioCanvasPanel } from './WorkflowStudioCanvasPanel'
import { WorkflowStudioDetailSection } from './WorkflowStudioDetailSection'
import { WorkflowStudioResizeHandle } from './WorkflowStudioResizeHandle'
import type { StudioMobilePanel } from './WorkflowStudioMobileNav'
import type { StudioLayoutProps } from './workflowStudioLayoutProps'
import pageStyles from '../WorkflowStudioPageResponsive.module.css'
import sidePanelStyles from '../WorkflowStudioPageSidePanel.module.css'
import splitStyles from './WorkflowStudioSplitLayout.module.css'

type Props = {
  props: StudioLayoutProps
  mobilePanel: StudioMobilePanel
  agentOpen: boolean
  onToggleAgent: () => void
}

/** 左右分栏 grid：左半画布（或节点详情），右半 Agent 对话（或详情）。
 * chat 收起后保持挂载（chatCollapsed hidden），会话与滚动状态不丢。 */
export function WorkflowStudioSplitLayout({
  props,
  mobilePanel,
  agentOpen,
  onToggleAgent,
}: Props) {
  const nodeSelected = props.selectedNodeKey !== null
  const detailLeft = nodeSelected && agentOpen
  const split = agentOpen || nodeSelected

  const asideClass = [
    sidePanelStyles.sidePanel,
    pageStyles.sidePanel,
    splitStyles.colRight,
    mobilePanel === 'agent' ? pageStyles.activePanel : '',
    agentOpen ? '' : splitStyles.chatCollapsed,
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div
      className={`${pageStyles.layout}${split ? ` ${pageStyles.withInspector}` : ''}`}
    >
      <WorkflowStudioCanvasPanel
        props={props}
        agentOpen={agentOpen}
        onToggleAgent={onToggleAgent}
        mobileActive={mobilePanel === 'graph'}
        replacedByDetail={detailLeft}
      />
      {split && <WorkflowStudioResizeHandle />}
      {props.selectedNodeKey !== null && (
        <WorkflowStudioDetailSection
          workflow={props.workflow}
          nodeKey={props.selectedNodeKey}
          agentCatalog={props.agentCatalog}
          definitionYaml={props.definitionYaml}
          setDefinitionYaml={props.setDefinitionYaml}
          compareSummary={props.compareSummary}
          readOnly={props.readOnly}
          detailLeft={detailLeft}
          mobileActive={mobilePanel === 'editor'}
          agentOpen={agentOpen}
          onToggleAgent={onToggleAgent}
          onBack={() => props.setSelectedNodeKey(null)}
        />
      )}
      <StudioChatAside
        props={props}
        agentOpen={agentOpen}
        asideClass={asideClass}
      />
    </div>
  )
}
