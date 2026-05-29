export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const isGet = !init || !init.method || init.method === 'GET'
  const response = await fetch(path, {
    ...(isGet ? { cache: 'no-store' } : {}),
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
  })
  if (!response.ok) {
    const text = await response.text()
    let message: string
    try {
      const json = JSON.parse(text)
      message = json.detail || json.message || `HTTP ${response.status}`
    } catch {
      message = `HTTP ${response.status}: ${text.slice(0, 200)}`
    }
    throw new Error(message)
  }
  return (await response.json()) as T
}
