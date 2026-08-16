import { DagGraph } from '../../components/dag/DagGraph'
import { WorkflowDagFullscreenButton } from './components/WorkflowDagFullscreenButton'
import { WorkflowStudioMobileNav } from './WorkflowStudioMobileNav'
import { WorkflowStudioInspectorPanel } from './WorkflowStudioSidePanels'
import { useWorkflowStudioMobilePanel } from './useWorkflowStudioMobilePanel'
import { useStudioRightPanelTab } from './chat/useStudioRightPanelTab'
import type { StudioLayoutProps } from './workflowStudioLayoutProps'
import canvasStyles from '../WorkflowStudioPageCanvas.module.css'
import canvasToolbarStyles from '../WorkflowStudioPageCanvasToolbar.module.css'
import pageStyles from '../WorkflowStudioPageResponsive.module.css'

export function WorkflowStudioWorkspace(props: StudioLayoutProps) {
  const { mobilePanel, setMobilePanel } = useWorkflowStudioMobilePanel(
    props.selectedNodeKey
  )
  // 右栏 tab 状态提升到这一层：「Agent 助手」tab 选中时即使没有选中节点，
  // 侧栏也保持打开；选中节点自动切回节点配置 tab（保持既有交互）。
  const rightPanel = useStudioRightPanelTab(props.setSelectedNodeKey)

  const graphActive = mobilePanel === 'graph'
  const inspectorActive = mobilePanel === 'editor'
  const inspectorOpen = props.selectedNodeKey !== null || rightPanel.chatOpen

  return (
    <>
      <WorkflowStudioMobileNav
        value={mobilePanel}
        editorAvailable={inspectorOpen}
        onChange={setMobilePanel}
      />
      <div
        className={`${pageStyles.layout}${inspectorOpen ? ` ${pageStyles.withInspector}` : ''}`}
      >
        <main
          className={`${canvasStyles.canvas}${graphActive ? ` ${pageStyles.activePanel}` : ''}`}
          data-mobile-panel="graph"
        >
          <div
            data-canvas-toolbar
            className={canvasToolbarStyles.canvasToolbar}
          >
            <WorkflowDagFullscreenButton
              onClick={() => props.setDagFullscreenOpen(true)}
            />
          </div>
          {props.workflow && (
            <DagGraph
              nodes={props.nodes}
              edges={props.edges}
              selectedNode={props.selectedNodeKey}
              onSelectedNodeChange={rightPanel.selectNode}
              hideNodeDetails
            />
          )}
        </main>
        {inspectorOpen && (
          <WorkflowStudioInspectorPanel
            props={props}
            active={inspectorActive}
            rightPanelTab={rightPanel.tab}
            onRightPanelTabChange={rightPanel.setTab}
            onSelectNode={rightPanel.selectNode}
            onClose={rightPanel.closePanel}
          />
        )}
      </div>
    </>
  )
}
