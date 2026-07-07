import { useParams } from 'react-router-dom'
import { useState } from 'react'
import { AppShell } from '../layouts/AppShell'
import { WorkflowStudioAppBar } from './workflowStudio/WorkflowStudioAppBar'
import { WorkflowStudioLayout } from './workflowStudio/WorkflowStudioLayout'
import { useWorkflowStudio } from './workflowStudio/useWorkflowStudio'

export function WorkflowStudioPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const [dagFullscreenOpen, setDagFullscreenOpen] = useState(false)
  const studio = useWorkflowStudio(workspaceId)

  return (
    <AppShell
      appBar={({ scrolled }) => (
        <WorkflowStudioAppBar
          workspaceId={workspaceId}
          studio={studio}
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
