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
    // #355：结构化 detail 可能是整个响应体（409 携带最新文档供前端刷
    // 新）。解析出的 json 挂在 error.body 上，调用侧按需取用；message
    // 语义不变（#467：优先 detail.message / 字符串直传）。
    let body: unknown
    try {
      const json = JSON.parse(text)
      body = json
      const d = json.detail as string | { message?: string } | undefined
      const inline = typeof d === 'string' ? d : d?.message
      message = (typeof inline === 'string' && inline) || json.message || prefix
    } catch {
      message = `${prefix}: ${text.slice(0, 200)}`
    }
    throw Object.assign(new Error(message), { status: response.status, body })
  }
  return (await response.json()) as T
}
