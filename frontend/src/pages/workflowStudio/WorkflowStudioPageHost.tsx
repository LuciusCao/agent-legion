import { AppShell } from '../../layouts/AppShell'
import { WorkflowStudioAppBar } from './WorkflowStudioAppBar'
import { WorkflowStudioPageContent } from './WorkflowStudioPageContent'
import { useWorkflowStudio } from './useWorkflowStudio'
import { useWorkflowStudioPageView } from './useWorkflowStudioPageView'

export function WorkflowStudioPageHost({
  workspaceId,
}: {
  workspaceId?: string
}) {
  const studio = useWorkflowStudio(workspaceId)
  const view = useWorkflowStudioPageView(studio)

  return (
    <AppShell
      appBar={({ scrolled }) => (
        <WorkflowStudioAppBar
          workspaceId={workspaceId}
          studio={studio}
          scrolled={scrolled}
          onValidate={() => void view.validateAndShowResult()}
        />
      )}
    >
      <WorkflowStudioPageContent studio={studio} view={view} />
    </AppShell>
  )
}
