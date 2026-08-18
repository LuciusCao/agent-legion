import { Navigate, useParams } from 'react-router-dom'
import { AppShell } from '../layouts/AppShell'
import { useAuthStore } from '../stores/authStore'
import { WorkflowStudioAppBar } from './workflowStudio/WorkflowStudioAppBar'
import { WorkflowStudioPageContent } from './workflowStudio/WorkflowStudioPageContent'
import { useWorkflowStudio } from './workflowStudio/useWorkflowStudio'
import { useWorkflowStudioPageView } from './workflowStudio/useWorkflowStudioPageView'

export function WorkflowStudioPage() {
  // Studio 全入口 admin-only（P4）：非 admin 重定向回 workspace 主页。
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const home = workspaceId ? `/workspaces/${workspaceId}` : '/'
  if (!isAdmin) return <Navigate to={home} replace />
  return <WorkflowStudioPageHost workspaceId={workspaceId} />
}

function WorkflowStudioPageHost({ workspaceId }: { workspaceId?: string }) {
  const studio = useWorkflowStudio(workspaceId)
  const view = useWorkflowStudioPageView(studio)

  return (
    <AppShell
      appBar={({ scrolled }) => (
        <WorkflowStudioAppBar
          workspaceId={workspaceId}
          studio={studio}
          scrolled={scrolled}
          onOpenChanges={() => view.setGlobalMode('changes')}
          onOpenYaml={() => view.setGlobalMode('yaml')}
          onOpenAgents={() => view.openPanel('agents')}
          onOpenExecutors={() => view.openPanel('executors')}
          onValidate={() => void view.validateAndShowResult()}
        />
      )}
    >
      <WorkflowStudioPageContent studio={studio} view={view} />
    </AppShell>
  )
}
