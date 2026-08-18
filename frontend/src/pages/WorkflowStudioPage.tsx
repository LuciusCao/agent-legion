import { useParams } from 'react-router-dom'
import { AppShell } from '../layouts/AppShell'
import { WorkflowStudioAppBar } from './workflowStudio/WorkflowStudioAppBar'
import { WorkflowStudioPageContent } from './workflowStudio/WorkflowStudioPageContent'
import { useWorkflowStudio } from './workflowStudio/useWorkflowStudio'
import { useWorkflowStudioPageView } from './workflowStudio/useWorkflowStudioPageView'

export function WorkflowStudioPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
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
