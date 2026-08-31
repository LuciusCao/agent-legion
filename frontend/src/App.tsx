import { Suspense, useEffect } from 'react'
import { useAgentsStore } from './stores/agentsStore'
import Toast from './components/Toast'
import AppRoutes from './AppRoutes'
import { ErrorBoundary } from './components/ErrorBoundary'
import { AppErrorFallback } from './components/ErrorFallback'

export default function App() {
  // Field selector only: subscribing to the whole store would re-render the
  // entire route tree on every WS agent message (upsertAgent → new array).
  const connectAgentsWs = useAgentsStore((s) => s.connectAgentsWs)

  useEffect(() => {
    const cleanup = connectAgentsWs()
    return cleanup
  }, [connectAgentsWs])

  return (
    // App 层兜底（#271）：任何未被页面级边界捕获的渲染错误都停在这里，
    // 不再白屏；chunk 加载失败（发版后 hash 失效）时先尝试整页 reload 一次。
    <ErrorBoundary fallback={<AppErrorFallback />} reloadOnChunkError>
      <main className="app-shell">
        <Suspense fallback={<div style={{ padding: 24 }}>加载中…</div>}>
          <AppRoutes />
        </Suspense>
        <Toast />
      </main>
    </ErrorBoundary>
  )
}
