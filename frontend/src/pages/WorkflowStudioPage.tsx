import { useParams } from 'react-router-dom'
import { AppShell } from '../layouts/AppShell'
import { AppBar } from '../components/AppBar'
import { DagGraph } from '../components/DagGraph'
import { WorkflowNodeInspector } from './workflowStudio/WorkflowNodeInspector'
import { WorkflowNodeOutline } from './workflowStudio/WorkflowNodeOutline'
import { WorkflowRevisionList } from './workflowStudio/WorkflowRevisionList'
import { WorkflowStudioSummaryBar } from './workflowStudio/WorkflowStudioSummaryBar'
import { WorkflowDefinitionEditor } from './workflowStudio/WorkflowDefinitionEditor'
import { WorkflowValidationPanel } from './workflowStudio/WorkflowValidationPanel'
import { useWorkflowStudio } from './workflowStudio/useWorkflowStudio'
import styles from './WorkflowStudioPage.module.css'

export function WorkflowStudioPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const {
    loadState,
    actionState,
    workflow,
    revision,
    revisions,
    definitionYaml,
    setDefinitionYaml,
    selectedNodeKey,
    setSelectedNodeKey,
    validationErrors,
    validationMessage,
    dirty,
    canSubmit,
    validateDraft,
    publishDraft,
    resetDefinition,
    nodes,
    edges,
  } = useWorkflowStudio(workspaceId)

  return (
    <AppShell
      appBar={({ scrolled }) => (
        <AppBar
          title="Workflow Studio"
          backTo={workspaceId ? `/workspaces/${workspaceId}` : '/'}
          scrolled={scrolled}
        />
      )}
    >
      <div className={styles.page}>
        <WorkflowStudioSummaryBar
          workflow={workflow}
          revision={revision}
          dirty={dirty}
          actionState={actionState}
          canSubmit={canSubmit}
          onValidate={() => void validateDraft()}
          onPublish={() => void publishDraft()}
          onReset={resetDefinition}
        />
        {loadState === 'loading' && <p>正在加载 workflow</p>}
        {loadState === 'error' && <p>无法加载 active workflow revision</p>}
        {loadState === 'ready' && (
          <div className={styles.layout}>
            <aside className={styles.sidePanel}>
              <WorkflowRevisionList
                revisions={revisions}
                activeRevisionId={revision?.id}
              />
              <WorkflowNodeOutline
                workflow={workflow}
                selectedNodeKey={selectedNodeKey}
                onSelectNode={setSelectedNodeKey}
              />
            </aside>
            <main className={styles.canvas}>
              {workflow && (
                <DagGraph
                  nodes={nodes}
                  edges={edges}
                  selectedNode={selectedNodeKey}
                  onSelectedNodeChange={setSelectedNodeKey}
                  hideNodeDetails
                />
              )}
            </main>
            <aside className={styles.sidePanel}>
              <WorkflowNodeInspector
                workflow={workflow}
                selectedNodeKey={selectedNodeKey}
              />
              <WorkflowDefinitionEditor
                value={definitionYaml}
                onChange={setDefinitionYaml}
              />
              <WorkflowValidationPanel
                message={validationMessage}
                errors={validationErrors}
              />
            </aside>
          </div>
        )}
      </div>
    </AppShell>
  )
}
