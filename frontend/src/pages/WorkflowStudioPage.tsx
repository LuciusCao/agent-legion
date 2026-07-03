import { useParams } from 'react-router-dom'
import { useState } from 'react'
import { AppShell } from '../layouts/AppShell'
import { AppBar } from '../components/AppBar'
import { WorkflowStudioLayout } from './workflowStudio/WorkflowStudioLayout'
import { useWorkflowStudio } from './workflowStudio/useWorkflowStudio'

export function WorkflowStudioPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const [dagFullscreenOpen, setDagFullscreenOpen] = useState(false)
  const studio = useWorkflowStudio(workspaceId)

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
      <WorkflowStudioLayout
        {...studio}
        dagFullscreenOpen={dagFullscreenOpen}
        setDagFullscreenOpen={setDagFullscreenOpen}
        onValidate={() => void studio.validateDraft()}
        onPublish={() => void studio.requestPublish()}
        onReset={studio.resetDefinition}
      />
    </AppShell>
  )
}
