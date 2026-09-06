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
      const d = json.detail as string | { message?: string } | undefined
      // #467：结构化 detail（部分创建失败）取 message；字符串直传。
      const inline = typeof d === 'string' ? d : d?.message
      message = (typeof inline === 'string' && inline) || json.message || prefix
    } catch {
      message = `${prefix}: ${text.slice(0, 200)}`
    }
    throw Object.assign(new Error(message), { status: response.status })
  }
  return (await response.json()) as T
}
