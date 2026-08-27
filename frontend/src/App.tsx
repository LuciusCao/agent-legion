import { Suspense, useEffect } from 'react'
import { useAgentsStore } from './stores/agentsStore'
import Toast from './components/Toast'
import AppRoutes from './AppRoutes'

export default function App() {
  // Field selector only: subscribing to the whole store would re-render the
  // entire route tree on every WS agent message (upsertAgent → new array).
  const connectAgentsWs = useAgentsStore((s) => s.connectAgentsWs)

  useEffect(() => {
    const cleanup = connectAgentsWs()
    return cleanup
  }, [connectAgentsWs])

  return (
    <main className="app-shell">
      <Suspense fallback={<div style={{ padding: 24 }}>加载中…</div>}>
        <AppRoutes />
      </Suspense>
      <Toast />
    </main>
  )
}
