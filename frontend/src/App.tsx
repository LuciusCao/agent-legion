import { Suspense, useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { useAgentsStore } from './stores/agentsStore'
import { useUiStore } from './stores/uiStore'
import Toast from './components/Toast'
import AppRoutes from './AppRoutes'

export default function App() {
  const { connectAgentsWs } = useAgentsStore()
  const { closeAddDialog } = useUiStore()
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
        <AppRoutes />
      </Suspense>
      <Toast />
    </main>
  )
}
