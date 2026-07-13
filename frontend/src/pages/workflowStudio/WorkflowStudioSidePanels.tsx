import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import { WorkflowStudioPanelCollapseButton } from './WorkflowStudioPanelCollapseButton'
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
      <WorkflowStudioPanelCollapseButton
        label="关闭节点配置"
        onClick={onClose}
        icon={<ChevronRightIcon />}
      />
      <WorkflowStudioRightPanel {...props} />
    </aside>
  )
}

function panelClass(active: boolean) {
  return `${sidePanelStyles.sidePanel} ${pageStyles.sidePanel}${active ? ` ${pageStyles.activePanel}` : ''}`
}
