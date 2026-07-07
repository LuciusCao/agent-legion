import { useEffect } from 'react'
import { DagGraph } from '../../components/DagGraph'
import { WorkflowNodeOutline } from './WorkflowNodeOutline'
import { WorkflowRevisionList } from './WorkflowRevisionList'
import { WorkflowDagFullscreenButton } from './components/WorkflowDagFullscreenDialog'
import { WorkflowStudioRightPanel } from './WorkflowStudioRightPanel'
import { WorkflowStudioMobileNav } from './WorkflowStudioMobileNav'
import { useWorkflowStudioMobilePanel } from './useWorkflowStudioMobilePanel'
import type { StudioLayoutProps } from './workflowStudioLayoutProps'
import canvasStyles from '../WorkflowStudioPageCanvas.module.css'
import canvasToolbarStyles from '../WorkflowStudioPageCanvasToolbar.module.css'
import pageStyles from '../WorkflowStudioPageResponsive.module.css'
import sidePanelStyles from '../WorkflowStudioPageSidePanel.module.css'

export function WorkflowStudioWorkspace(props: StudioLayoutProps) {
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
      <div className={pageStyles.layout}>
        <aside
          className={`${sidePanelStyles.sidePanel} ${pageStyles.sidePanel}${versionsActive ? ` ${pageStyles.activePanel}` : ''}`}
          data-mobile-panel="versions"
        >
          <WorkflowRevisionList
            revisions={props.revisions}
            activeRevisionId={props.activeRevision?.id ?? props.revision?.id}
            selectedRevisionId={props.selectedRevisionId}
            isLoadingRevision={props.isLoadingRevision}
            revisionLoadError={props.revisionLoadError}
            onSelectRevision={props.selectRevision}
          />
          <WorkflowNodeOutline
            workflow={props.workflow}
            selectedNodeKey={props.selectedNodeKey}
            onSelectNode={props.setSelectedNodeKey}
            changedNodeKeys={props.compareSummary?.changedNodeKeys}
          />
        </aside>
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
        <aside
          className={`${sidePanelStyles.sidePanel} ${pageStyles.sidePanel}${inspectorActive ? ` ${pageStyles.activePanel}` : ''}`}
          data-mobile-panel="inspector"
        >
          <WorkflowStudioRightPanel
            {...props}
            onSelectNode={props.setSelectedNodeKey}
            forcedMode={forcedMode}
          />
        </aside>
      </div>
    </>
  )
}
