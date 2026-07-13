import { useEffect, useState } from 'react'
import { DagGraph } from '../../components/DagGraph'
import { WorkflowDagFullscreenButton } from './components/WorkflowDagFullscreenDialog'
import { WorkflowStudioMobileNav } from './WorkflowStudioMobileNav'
import {
  WorkflowStudioInspectorPanel,
  WorkflowStudioLeftPanel,
} from './WorkflowStudioSidePanels'
import { useWorkflowStudioMobilePanel } from './useWorkflowStudioMobilePanel'
import type { StudioLayoutProps } from './workflowStudioLayoutProps'
import canvasStyles from '../WorkflowStudioPageCanvas.module.css'
import canvasToolbarStyles from '../WorkflowStudioPageCanvasToolbar.module.css'
import pageStyles from '../WorkflowStudioPageResponsive.module.css'
import resizeStyles from './WorkflowStudioResizableLayout.module.css'

export function WorkflowStudioWorkspace(props: StudioLayoutProps) {
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [rightCollapsed, setRightCollapsed] = useState(false)
  const { mobilePanel, setMobilePanel } = useWorkflowStudioMobilePanel(
    props.selectedNodeKey
  )

  useEffect(() => {
    const hasValidation =
      props.validationErrors.length > 0 || props.validationMessage !== ''
    if (hasValidation) setMobilePanel('inspector')
  }, [props.validationErrors.length, props.validationMessage, setMobilePanel])

  const forcedMode =
    mobilePanel === 'changes'
      ? 'changes'
      : mobilePanel === 'yaml'
        ? 'yaml'
        : undefined

  const versionsActive = mobilePanel === 'versions'
  const graphActive = mobilePanel === 'graph'
  const inspectorActive =
    mobilePanel === 'inspector' ||
    mobilePanel === 'changes' ||
    mobilePanel === 'yaml'

  return (
    <>
      <WorkflowStudioMobileNav value={mobilePanel} onChange={setMobilePanel} />
      <div
        className={`${pageStyles.layout}${leftCollapsed ? ` ${resizeStyles.leftCollapsed}` : ''}${rightCollapsed ? ` ${resizeStyles.rightCollapsed}` : ''}`}
      >
        <WorkflowStudioLeftPanel
          props={props}
          collapsed={leftCollapsed}
          active={versionsActive}
          onToggle={() => setLeftCollapsed((value) => !value)}
        />
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
        <WorkflowStudioInspectorPanel
          props={props}
          collapsed={rightCollapsed}
          active={inspectorActive}
          forcedMode={forcedMode}
          onToggle={() => setRightCollapsed((value) => !value)}
        />
      </div>
    </>
  )
}
