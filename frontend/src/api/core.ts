import { handleUnauthorized, withCsrfHeader } from './requestAuth'

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? 'GET').toUpperCase()
  const headers = withCsrfHeader(method, {
    'Content-Type': 'application/json',
    ...(init?.headers ?? {}),
  } as Record<string, string>)
  const response = await fetch(path, {
    ...(method === 'GET' ? { cache: 'no-store' } : {}),
    ...init,
    headers,
  })
  if (response.status === 401) handleUnauthorized(path)
  if (!response.ok) {
    const text = await response.text()
    let message: string
    const prefix = `HTTP ${response.status}`
    try {
      const json = JSON.parse(text)
      message = json.detail || json.message || prefix
    } catch {
      message = `${prefix}: ${text.slice(0, 200)}`
    }
    throw Object.assign(new Error(message), { status: response.status })
  }
  return (await response.json()) as T
}
