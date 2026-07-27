// Cookie-based session endpoints require a custom header on every mutating
// request as a CSRF guard (SameSite=Strict cookie + custom header).
const CSRF_HEADER = 'x-agent-legion-request'

// 401 handling is delegated to a registered handler (authStore) so the API
// layer does not import the store; the fallback redirects to /login directly.
type UnauthorizedHandler = () => void
let unauthorizedHandler: UnauthorizedHandler | null = null

export function setUnauthorizedHandler(
  handler: UnauthorizedHandler | null
): void {
  unauthorizedHandler = handler
}

export function withCsrfHeader(
  method: string,
  headers: Record<string, string>
): Record<string, string> {
  if (method === 'GET' || method === 'HEAD') return headers
  return { ...headers, [CSRF_HEADER]: '1' }
}

export function handleUnauthorized(path: string): void {
  // Login/bootstrap 401s are form errors, not expired sessions.
  if (path.startsWith('/api/auth/')) return
  if (typeof window === 'undefined') return
  if (window.location.pathname === '/login') return
  if (window.location.pathname === '/setup') return
  if (unauthorizedHandler) {
    unauthorizedHandler()
    return
  }
  try {
    window.location.assign('/login')
  } catch {
    // ignore — environments without navigation support (e.g. jsdom)
  }
}
