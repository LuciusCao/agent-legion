import { AppBar } from '../../components/AppBar'
import { WorkflowStudioCommandBar } from './WorkflowStudioCommandBar'
import type { useWorkflowStudio } from './useWorkflowStudio'
import { useWorkflowStudioAppTitle } from './useWorkflowStudioAppTitle'
type Studio = ReturnType<typeof useWorkflowStudio>

type Props = {
  workspaceId: string | undefined
  studio: Studio
  scrolled?: boolean
}

export function WorkflowStudioAppBar({ workspaceId, studio, scrolled }: Props) {
  const title = useWorkflowStudioAppTitle(workspaceId)
  return (
    <AppBar
      title={title}
      backTo={workspaceId ? `/workspaces/${workspaceId}` : '/'}
      scrolled={scrolled}
      rightActions={
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
          onSelectRevision={studio.selectRevision}
          onValidate={() => void studio.validateDraft()}
          onPublish={() => void studio.requestPublish()}
          onReset={studio.resetDefinition}
          backToDraft={studio.backToDraft}
          useViewedRevisionAsDraft={studio.useViewedRevisionAsDraft}
        />
      }
    />
  )
}
