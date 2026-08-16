import { WorkflowStudioRightPanel } from './WorkflowStudioRightPanel'
import type { StudioRightPanelTab } from './chat/StudioRightPanelTabs'
import type { StudioLayoutProps } from './workflowStudioLayoutProps'
import pageStyles from '../WorkflowStudioPageResponsive.module.css'
import sidePanelStyles from '../WorkflowStudioPageSidePanel.module.css'

type Props = {
  props: StudioLayoutProps
  active: boolean
  rightPanelTab: StudioRightPanelTab
  onRightPanelTabChange: (tab: StudioRightPanelTab) => void
  onSelectNode: (nodeKey: string) => void
  onClose: () => void
}

export function WorkflowStudioInspectorPanel(p: Props) {
  return (
    <aside className={panelClass(p.active)} data-mobile-panel="inspector">
      <WorkflowStudioRightPanel
        {...p.props}
        activeTab={p.rightPanelTab}
        onTabChange={p.onRightPanelTabChange}
        onBackToDraft={p.props.backToDraft}
        onSelectNode={p.onSelectNode}
        onClose={p.onClose}
      />
    </aside>
  )
}

function panelClass(active: boolean) {
  return `${sidePanelStyles.sidePanel} ${pageStyles.sidePanel}${active ? ` ${pageStyles.activePanel}` : ''}`
}
