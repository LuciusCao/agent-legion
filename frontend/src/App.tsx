import { lazy, Suspense, useEffect } from 'react'
import { Routes, Route, useLocation, Navigate } from 'react-router-dom'
import { useUiStore } from './stores/uiStore'
import Toast from './components/Toast'
import JobDetailPage from './pages/JobDetailPage'

const ListPage = lazy(() =>
  import('./pages/ListPage').then((m) => ({ default: m.ListPage }))
)
const DetailPage = lazy(() =>
  import('./pages/DetailPage').then((m) => ({ default: m.DetailPage }))
)
const DashboardPage = lazy(() =>
  import('./pages/DashboardPage').then((m) => ({ default: m.DashboardPage }))
)
const WorkspaceLayout = lazy(() => import('./layouts/WorkspaceLayout'))
const VideoHiveLayout = lazy(() => import('./layouts/VideoHiveLayout'))
const VideoHiveSettingsPage = lazy(() =>
  import('./pages/VideoHiveSettingsPage').then((m) => ({
    default: m.VideoHiveSettingsPage,
  }))
)
const SettingsPage = lazy(() =>
  import('./pages/SettingsPage').then((m) => ({ default: m.SettingsPage }))
)
const WorkspaceMainPage = lazy(() =>
  import('./pages/WorkspaceMainPage').then((m) => ({ default: m.default }))
)

export default function App() {
  const { connectAgentsWs, closeAddDialog } = useUiStore()
  const location = useLocation()

  useEffect(() => {
    const cleanup = connectAgentsWs()
    return cleanup
  }, [connectAgentsWs])

  useEffect(() => {
    closeAddDialog()
  }, [location.pathname, closeAddDialog])

  return (
    <main className="app-shell">
      <Suspense fallback={<div style={{ padding: 24 }}>加载中…</div>}>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/video-hive" element={<VideoHiveLayout />}>
            <Route index element={<ListPage />} />
          </Route>
          <Route
            path="/video-hive/settings"
            element={<VideoHiveSettingsPage />}
          />
          <Route path="/workspaces" element={<Navigate to="/" replace />} />
          <Route path="/workspaces/:workspaceId" element={<WorkspaceLayout />}>
            <Route index element={<WorkspaceMainPage />} />
            <Route path="jobs/:jobId" element={<JobDetailPage />} />
          </Route>
          <Route
            path="/workspaces/:workspaceId/settings"
            element={<SettingsPage />}
          />
          <Route path="/videos/:id" element={<DetailPage />} />
        </Routes>
      </Suspense>
      <Toast />
    </main>
  )
}
