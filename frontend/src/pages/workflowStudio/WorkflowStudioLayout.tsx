import { DagGraph } from '../../components/DagGraph'
import { WorkflowNodeInspector } from './WorkflowNodeInspector'
import { WorkflowNodeOutline } from './WorkflowNodeOutline'
import { WorkflowRevisionList } from './WorkflowRevisionList'
import { WorkflowDefinitionEditor } from './WorkflowDefinitionEditor'
import { WorkflowValidationPanel } from './WorkflowValidationPanel'
import { WorkflowChangeSummaryPanel } from './components/WorkflowChangeSummaryPanel'
import { WorkflowDagFullscreenButton } from './components/WorkflowDagFullscreenDialog'
import { WorkflowStudioLayoutDialogs } from './WorkflowStudioLayoutDialogs'
import type { StudioLayoutProps } from './workflowStudioLayoutProps'
import canvasStyles from '../WorkflowStudioPageCanvas.module.css'
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
                activeRevisionId={props.activeRevision?.id}
              />
              <WorkflowNodeOutline
                workflow={props.workflow}
                selectedNodeKey={props.selectedNodeKey}
                onSelectNode={props.setSelectedNodeKey}
                changedNodeKeys={props.compareSummary?.changedNodeKeys}
              />
            </aside>
            <main className={canvasStyles.canvas}>
              <div className={canvasStyles.canvasToolbar}>
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
              <WorkflowNodeInspector
                workflow={props.workflow}
                selectedNodeKey={props.selectedNodeKey}
              />
              <WorkflowChangeSummaryPanel
                summary={props.compareSummary}
                loading={props.compareState === 'loading'}
                errors={props.compareErrors}
                onSelectNode={props.setSelectedNodeKey}
              />
              <WorkflowDefinitionEditor
                value={props.definitionYaml}
                onChange={props.setDefinitionYaml}
              />
              <WorkflowValidationPanel
                message={props.validationMessage}
                errors={props.validationErrors}
                compareErrors={props.compareErrors ?? undefined}
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
