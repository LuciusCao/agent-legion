import { lazy, Suspense, useEffect } from 'react'
import {
  Routes,
  Route,
  useLocation,
  Navigate,
  useParams,
} from 'react-router-dom'
import { useUiStore } from './stores/uiStore'
import Toast from './components/Toast'
import WorkspaceOverview from './views/WorkspaceOverview'
import WorkspaceJobList from './views/WorkspaceJobList'
import WorkspaceJobDetail from './views/WorkspaceJobDetail'
import { VIDEO_HIVE_ID } from './layouts/WorkspaceLayout'
import { SettingsPage } from './pages/SettingsPage'

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

function WorkspaceOverviewWrapper() {
  const { workspaceId } = useParams()
  return <WorkspaceOverview isVideoHive={workspaceId === VIDEO_HIVE_ID} />
}

function WorkspaceJobListWrapper() {
  const { workspaceId } = useParams()
  return <WorkspaceJobList isVideoHive={workspaceId === VIDEO_HIVE_ID} />
}

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
          <Route path="/video-hive" element={<ListPage />} />
          <Route path="/workspaces" element={<Navigate to="/" replace />} />
          <Route path="/workspaces/:workspaceId" element={<WorkspaceLayout />}>
            <Route index element={<WorkspaceOverviewWrapper />} />
            <Route path="jobs" element={<WorkspaceJobListWrapper />} />
            <Route path="jobs/:jobId" element={<WorkspaceJobDetail />} />
            <Route
              path="packages"
              element={<div>Packages view — 待实现</div>}
            />
            <Route path="settings" element={<SettingsPage />} />
          </Route>
          <Route path="/videos/:id" element={<DetailPage />} />
        </Routes>
      </Suspense>
      <Toast />
    </main>
  )
}
