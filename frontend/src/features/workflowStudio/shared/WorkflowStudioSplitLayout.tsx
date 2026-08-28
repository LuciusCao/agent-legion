import { StudioChatAside } from '../chat/StudioChatAside'
import { WorkflowStudioCanvasPanel } from '../canvas/WorkflowStudioCanvasPanel'
import { WorkflowStudioDetailSection } from '../inspector/WorkflowStudioDetailSection'
import { WorkflowStudioResizeHandle } from './WorkflowStudioResizeHandle'
import type { StudioMobilePanel } from './WorkflowStudioMobileNav'
import { useStudioState } from './studioStateContext'
import pageStyles from '../../../pages/WorkflowStudioPageResponsive.module.css'
import sidePanelStyles from '../../../pages/WorkflowStudioPageSidePanel.module.css'
import splitStyles from './WorkflowStudioSplitLayout.module.css'

type Props = {
  mobilePanel: StudioMobilePanel
  agentOpen: boolean
  onToggleAgent: () => void
}

/** 左右分栏 grid：左半画布（或节点详情），右半 Agent 对话（或详情）。
 * chat 收起后保持挂载（chatCollapsed hidden），会话与滚动状态不丢。 */
export function WorkflowStudioSplitLayout({
  mobilePanel,
  agentOpen,
  onToggleAgent,
}: Props) {
  const studio = useStudioState()
  const nodeSelected = studio.selectedNodeKey !== null
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
        agentOpen={agentOpen}
        onToggleAgent={onToggleAgent}
        mobileActive={mobilePanel === 'graph'}
        replacedByDetail={detailLeft}
      />
      {split && <WorkflowStudioResizeHandle />}
      {studio.selectedNodeKey !== null && (
        <WorkflowStudioDetailSection
          workflow={studio.workflow}
          nodeKey={studio.selectedNodeKey}
          agentCatalog={studio.agentCatalog}
          definitionYaml={studio.definitionYaml}
          setDefinitionYaml={studio.setDefinitionYaml}
          compareSummary={studio.compareSummary}
          readOnly={studio.readOnly}
          detailLeft={detailLeft}
          mobileActive={mobilePanel === 'editor'}
          agentOpen={agentOpen}
          onToggleAgent={onToggleAgent}
          onBack={() => studio.setSelectedNodeKey(null)}
        />
      )}
      <StudioChatAside agentOpen={agentOpen} asideClass={asideClass} />
    </div>
  )
}
