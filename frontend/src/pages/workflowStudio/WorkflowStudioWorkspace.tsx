import { DagGraph } from '../../components/dag/DagGraph'
import { WorkflowDagFullscreenButton } from './components/WorkflowDagFullscreenButton'
import { WorkflowStudioMobileNav } from './WorkflowStudioMobileNav'
import { WorkflowStudioInspectorPanel } from './WorkflowStudioSidePanels'
import { useWorkflowStudioMobilePanel } from './useWorkflowStudioMobilePanel'
import type { StudioLayoutProps } from './workflowStudioLayoutProps'
import canvasStyles from '../WorkflowStudioPageCanvas.module.css'
import canvasToolbarStyles from '../WorkflowStudioPageCanvasToolbar.module.css'
import pageStyles from '../WorkflowStudioPageResponsive.module.css'

export function WorkflowStudioWorkspace(props: StudioLayoutProps) {
  const { mobilePanel, setMobilePanel } = useWorkflowStudioMobilePanel(
    props.selectedNodeKey
  )

  const graphActive = mobilePanel === 'graph'
  const inspectorActive = mobilePanel === 'editor'
  const inspectorOpen = props.selectedNodeKey !== null

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
              onSelectedNodeChange={props.setSelectedNodeKey}
              hideNodeDetails
            />
          )}
        </main>
        {inspectorOpen && (
          <WorkflowStudioInspectorPanel
            props={props}
            active={inspectorActive}
            onClose={() => props.setSelectedNodeKey(null)}
          />
        )}
      </div>
    </>
  )
}
