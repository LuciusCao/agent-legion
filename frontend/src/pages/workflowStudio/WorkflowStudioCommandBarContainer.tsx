import type { useWorkflowStudio } from './useWorkflowStudio'
import { WorkflowStudioCommandBar } from './WorkflowStudioCommandBar'

type Props = {
  studio: ReturnType<typeof useWorkflowStudio>
  onOpenChanges: () => void
  onOpenYaml: () => void
  onOpenAgents: () => void
  onOpenExecutors: () => void
  onValidate: () => void
}

export function WorkflowStudioCommandBarContainer(props: Props) {
  const { studio } = props
  return (
    <WorkflowStudioCommandBar
      revision={studio.revision}
      revisions={studio.revisions}
      activeRevision={studio.activeRevision}
      viewMode={studio.viewMode}
      dirty={studio.dirty}
      readOnly={studio.readOnly}
      hasPreservedDraft={studio.hasPreservedDraft}
      compareSummary={studio.compareSummary}
      compareState={studio.compareState}
      actionState={studio.actionState}
      canSubmit={studio.canSubmit}
      canPublish={studio.canPublish}
      selectedRevisionId={studio.selectedRevisionId}
      isLoadingRevision={studio.isLoadingRevision}
      revisionLoadError={studio.revisionLoadError}
      onOpenChanges={props.onOpenChanges}
      onOpenYaml={props.onOpenYaml}
      onOpenAgents={props.onOpenAgents}
      onOpenExecutors={props.onOpenExecutors}
      onSelectRevision={studio.selectRevision}
      onValidate={props.onValidate}
      onPublish={() => void studio.requestPublish()}
      onReset={studio.resetDefinition}
      backToDraft={studio.backToDraft}
      useViewedRevisionAsDraft={studio.useViewedRevisionAsDraft}
    />
  )
}
