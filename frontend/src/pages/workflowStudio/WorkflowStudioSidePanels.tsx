import { WorkflowStudioRightPanel } from './WorkflowStudioRightPanel'
import type { StudioLayoutProps } from './workflowStudioLayoutProps'
import pageStyles from '../WorkflowStudioPageResponsive.module.css'
import sidePanelStyles from '../WorkflowStudioPageSidePanel.module.css'

type Props = {
  props: StudioLayoutProps
  active: boolean
  onClose: () => void
}

export function WorkflowStudioInspectorPanel({
  props,
  active,
  onClose,
}: Props) {
  return (
    <aside className={panelClass(active)} data-mobile-panel="inspector">
      <WorkflowStudioRightPanel {...props} onClose={onClose} />
    </aside>
  )
}

function panelClass(active: boolean) {
  return `${sidePanelStyles.sidePanel} ${pageStyles.sidePanel}${active ? ` ${pageStyles.activePanel}` : ''}`
}
