import { Navigate, useParams } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'
import { WorkflowStudioPageHost } from './workflowStudio/WorkflowStudioPageHost'

export function WorkflowStudioPage() {
  // Studio 全入口 admin-only（P4）：非 admin 重定向回 workspace 主页。
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const home = workspaceId ? `/workspaces/${workspaceId}` : '/'
  if (!isAdmin) return <Navigate to={home} replace />
  return <WorkflowStudioPageHost workspaceId={workspaceId} />
}
