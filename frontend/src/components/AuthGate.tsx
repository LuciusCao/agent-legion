import { useEffect, type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'

// Session guard around the route table: initializes auth state on mount,
// redirects to /setup on first run and to /login when anonymous.
export function AuthGate({ children }: { children: ReactNode }) {
  const location = useLocation()
  const { user, status, bootstrapAvailable, initialize } = useAuthStore()

  useEffect(() => {
    if (status === 'unknown') void initialize()
  }, [status, initialize])

  if (status === 'unknown') {
    return <div style={{ padding: 24 }}>加载中…</div>
  }

  const path = location.pathname
  if (bootstrapAvailable === true && path !== '/setup') {
    return <Navigate to="/setup" replace />
  }
  if (!user && path !== '/login' && path !== '/setup') {
    return <Navigate to="/login" replace />
  }
  if (user && path === '/login') {
    return <Navigate to="/" replace />
  }
  return <>{children}</>
}
