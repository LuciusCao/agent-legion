import { lazy, Suspense, useEffect } from 'react'
import { Routes, Route, useLocation } from 'react-router-dom'
import { useUiStore } from './stores/uiStore'
import Toast from './components/Toast'

const ListPage = lazy(() =>
  import('./pages/ListPage').then((m) => ({ default: m.ListPage }))
)
const DetailPage = lazy(() =>
  import('./pages/DetailPage').then((m) => ({ default: m.DetailPage }))
)
const JobsPage = lazy(() =>
  import('./pages/JobsPage').then((m) => ({ default: m.JobsPage }))
)

export default function App() {
  const { connectAgentsWs, closeAddDialog } = useUiStore()
  const location = useLocation()

  useEffect(() => {
    const cleanup = connectAgentsWs()
    return cleanup
  }, [connectAgentsWs])

  // Close any open dialogs on route change so that navigating away
  // from the list page (e.g. into a video detail) always resets UI
  // state and prevents dialogs from re-appearing on return.
  useEffect(() => {
    closeAddDialog()
  }, [location.pathname, closeAddDialog])

  return (
    <main className="app-shell">
      <Suspense fallback={<div style={{ padding: 24 }}>加载中…</div>}>
        <Routes>
          <Route path="/" element={<ListPage />} />
          <Route path="/workspaces" element={<JobsPage />} />
          <Route path="/workspaces/:workspaceId" element={<JobsPage />} />
          <Route path="/videos/:id" element={<DetailPage />} />
        </Routes>
      </Suspense>
      <Toast />
    </main>
  )
}
