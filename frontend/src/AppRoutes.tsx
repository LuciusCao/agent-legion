import { Routes, Route, Navigate } from 'react-router-dom'
import { AuthGate } from './components/AuthGate'
import AdminRoutes from './routes/AdminRoutes'
import {
  DashboardPage,
  JobDetailPage,
  LoginPage,
  MonitoringPage,
  SettingsPage,
  SetupPage,
  TokenUsagePage,
  WorkflowStudioPage,
  WorkspaceLayout,
  WorkspaceMainPage,
} from './routes/pages'

export default function AppRoutes() {
  return (
    <AuthGate>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/setup" element={<SetupPage />} />
        <Route path="/admin/*" element={<AdminRoutes />} />
        <Route path="/" element={<DashboardPage />} />
        <Route path="/monitoring" element={<MonitoringPage />} />
        <Route path="/workspaces" element={<Navigate to="/" replace />} />
        <Route path="/workspaces/:workspaceId" element={<WorkspaceLayout />}>
          <Route index element={<WorkspaceMainPage />} />
          <Route path="jobs/:jobId" element={<JobDetailPage />} />
        </Route>
        <Route path="/workspaces/:workspaceId">
          <Route path="settings" element={<SettingsPage />} />
          <Route path="token-usage" element={<TokenUsagePage />} />
          <Route path="monitoring" element={<MonitoringPage />} />
          <Route path="workflow-studio" element={<WorkflowStudioPage />} />
        </Route>
      </Routes>
    </AuthGate>
  )
}
