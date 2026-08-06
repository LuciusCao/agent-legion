import { lazy } from 'react'

export const LoginPage = lazy(() => import('../pages/LoginPage'))
export const SetupPage = lazy(() => import('../pages/SetupPage'))
export const UsersAdminPage = lazy(() => import('../pages/UsersAdminPage'))
export const JobDetailPage = lazy(() => import('../pages/JobDetailPage'))
export const DashboardPage = lazy(() =>
  import('../pages/DashboardPage').then((m) => ({ default: m.DashboardPage }))
)
export const WorkspaceLayout = lazy(() => import('../layouts/WorkspaceLayout'))
export const SettingsPage = lazy(() =>
  import('../pages/SettingsPage').then((m) => ({ default: m.SettingsPage }))
)
export const WorkflowStudioPage = lazy(() =>
  import('../pages/WorkflowStudioPage').then((m) => ({
    default: m.WorkflowStudioPage,
  }))
)
export const WorkspaceMainPage = lazy(
  () => import('../pages/WorkspaceMainPage')
)
export const TokenUsagePage = lazy(() =>
  import('../pages/TokenUsagePage').then((m) => ({ default: m.TokenUsagePage }))
)
export const MonitoringPage = lazy(() => import('../pages/MonitoringPage'))
export const GlobalSettingsPage = lazy(
  () => import('../pages/GlobalSettingsPage')
)
