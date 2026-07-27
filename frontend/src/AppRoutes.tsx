import { lazy } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'

const JobDetailPage = lazy(() => import('./pages/JobDetailPage'))
const DashboardPage = lazy(() =>
  import('./pages/DashboardPage').then((m) => ({ default: m.DashboardPage }))
)
const WorkspaceLayout = lazy(() => import('./layouts/WorkspaceLayout'))
const SettingsPage = lazy(() =>
  import('./pages/SettingsPage').then((m) => ({ default: m.SettingsPage }))
)
const WorkflowStudioPage = lazy(() =>
  import('./pages/WorkflowStudioPage').then((m) => ({
    default: m.WorkflowStudioPage,
  }))
)
const WorkspaceMainPage = lazy(() => import('./pages/WorkspaceMainPage'))
const TokenUsagePage = lazy(() =>
  import('./pages/TokenUsagePage').then((m) => ({ default: m.TokenUsagePage }))
)
const MonitoringPage = lazy(() => import('./pages/MonitoringPage'))

export default function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<DashboardPage />} />
      <Route path="/monitoring" element={<MonitoringPage />} />
      <Route path="/workspaces" element={<Navigate to="/" replace />} />
      <Route path="/workspaces/:workspaceId" element={<WorkspaceLayout />}>
        <Route index element={<WorkspaceMainPage />} />
        <Route path="jobs/:jobId" element={<JobDetailPage />} />
      </Route>
      <Route
        path="/workspaces/:workspaceId/settings"
        element={<SettingsPage />}
      />
      <Route
        path="/workspaces/:workspaceId/token-usage"
        element={<TokenUsagePage />}
      />
      <Route
        path="/workspaces/:workspaceId/workflow-studio"
        element={<WorkflowStudioPage />}
      />
    </Routes>
  )
}
