import { DagGraph } from '../../components/DagGraph'
import { WorkflowNodeOutline } from './WorkflowNodeOutline'
import { WorkflowRevisionList } from './WorkflowRevisionList'
import { WorkflowDagFullscreenButton } from './components/WorkflowDagFullscreenDialog'
import { WorkflowStudioLayoutDialogs } from './WorkflowStudioLayoutDialogs'
import { WorkflowStudioRightPanel } from './WorkflowStudioRightPanel'
import type { StudioLayoutProps } from './workflowStudioLayoutProps'
import canvasStyles from '../WorkflowStudioPageCanvas.module.css'
import canvasToolbarStyles from '../WorkflowStudioPageCanvasToolbar.module.css'
import pageStyles from '../WorkflowStudioPage.module.css'
import '../WorkflowStudioPageResponsive.module.css'
import sidePanelStyles from '../WorkflowStudioPageSidePanel.module.css'

export function WorkflowStudioLayout(props: StudioLayoutProps) {
  return (
    <>
      <div className={pageStyles.page}>
        {props.loadState === 'loading' && <p>正在加载 workflow</p>}
        {props.loadState === 'error' && (
          <p>无法加载 active workflow revision</p>
        )}
        {props.loadState === 'ready' && (
          <div className={pageStyles.layout}>
            <aside className={sidePanelStyles.sidePanel}>
              <WorkflowRevisionList
                revisions={props.revisions}
                activeRevisionId={
                  props.activeRevision?.id ?? props.revision?.id
                }
                selectedRevisionId={props.selectedRevisionId}
                onSelectRevision={props.selectRevision}
              />
              <WorkflowNodeOutline
                workflow={props.workflow}
                selectedNodeKey={props.selectedNodeKey}
                onSelectNode={props.setSelectedNodeKey}
                changedNodeKeys={props.compareSummary?.changedNodeKeys}
              />
            </aside>
            <main className={canvasStyles.canvas}>
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
            <aside className={sidePanelStyles.sidePanel}>
              <WorkflowStudioRightPanel
                workflow={props.workflow}
                selectedNodeKey={props.selectedNodeKey}
                readOnly={props.readOnly}
                definitionYaml={props.definitionYaml}
                setDefinitionYaml={props.setDefinitionYaml}
                compareSummary={props.compareSummary}
                compareState={props.compareState}
                compareErrors={props.compareErrors}
                validationMessage={props.validationMessage}
                validationErrors={props.validationErrors}
                onSelectNode={props.setSelectedNodeKey}
              />
            </aside>
          </div>
        )}
      </div>
      <WorkflowStudioLayoutDialogs {...props} />
    </>
  )
}
