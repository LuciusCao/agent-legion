import ChevronLeftIcon from '@mui/icons-material/ChevronLeft'
import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import MenuOpenIcon from '@mui/icons-material/MenuOpen'
import { WorkflowNodeOutline } from './WorkflowNodeOutline'
import { WorkflowStudioPanelCollapseButton } from './WorkflowStudioPanelCollapseButton'
import { WorkflowStudioRightPanel } from './WorkflowStudioRightPanel'
import type { StudioLayoutProps } from './workflowStudioLayoutProps'
import pageStyles from '../WorkflowStudioPageResponsive.module.css'
import sidePanelStyles from '../WorkflowStudioPageSidePanel.module.css'
import collapseStyles from './WorkflowStudioSidePanels.module.css'

type LeftProps = {
  props: StudioLayoutProps
  collapsed: boolean
  active: boolean
  onToggle: () => void
}

type RightProps = LeftProps & { forcedMode?: 'changes' | 'yaml' }

export function WorkflowStudioLeftPanel({
  props,
  collapsed,
  active,
  onToggle,
}: LeftProps) {
  return (
    <aside
      className={panelClass(collapsed, active)}
      data-mobile-panel="versions"
    >
      <WorkflowStudioPanelCollapseButton
        label={collapsed ? '展开节点大纲' : '收起节点大纲'}
        onClick={onToggle}
        icon={collapsed ? <MenuOpenIcon /> : <ChevronLeftIcon />}
      />
      {!collapsed && (
        <WorkflowNodeOutline
          workflow={props.workflow}
          selectedNodeKey={props.selectedNodeKey}
          onSelectNode={props.setSelectedNodeKey}
          changedNodeKeys={props.compareSummary?.changedNodeKeys}
        />
      )}
    </aside>
  )
}

export function WorkflowStudioInspectorPanel({
  props,
  collapsed,
  active,
  forcedMode,
  onToggle,
}: RightProps) {
  return (
    <aside
      className={panelClass(collapsed, active)}
      data-mobile-panel="inspector"
    >
      <WorkflowStudioPanelCollapseButton
        label={collapsed ? '展开检查器' : '收起检查器'}
        onClick={onToggle}
        icon={collapsed ? <ChevronLeftIcon /> : <ChevronRightIcon />}
      />
      {!collapsed && (
        <WorkflowStudioRightPanel
          {...props}
          onSelectNode={props.setSelectedNodeKey}
          forcedMode={forcedMode}
        />
      )}
    </aside>
  )
}

function panelClass(collapsed: boolean, active: boolean) {
  return `${sidePanelStyles.sidePanel} ${pageStyles.sidePanel}${collapsed ? ` ${collapseStyles.collapsed}` : ''}${active ? ` ${pageStyles.activePanel}` : ''}`
}
