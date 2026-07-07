import { AppBar } from '../../components/AppBar'
import { WorkflowStudioCommandBar } from './WorkflowStudioCommandBar'
import type { useWorkflowStudio } from './useWorkflowStudio'

type Studio = ReturnType<typeof useWorkflowStudio>

type Props = {
  workspaceId: string | undefined
  studio: Studio
  scrolled?: boolean
}

export function WorkflowStudioAppBar({ workspaceId, studio, scrolled }: Props) {
  return (
    <AppBar
      title="Workflow Studio"
      backTo={workspaceId ? `/workspaces/${workspaceId}` : '/'}
      scrolled={scrolled}
      rightActions={
        <WorkflowStudioCommandBar
          workflow={studio.workflow}
          revision={studio.revision}
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
