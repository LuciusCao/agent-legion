export async function fetchPackages(): Promise<
  {
    packages: Array<{
      id: number
      name: string
      path: string
      video_count: number
      size_bytes: number
      created_at: string
    }>
  }
> {
  return api('/api/packages')
}

export async function deletePackage(id: number): Promise<{ deleted: boolean }> {
  return api(`/api/packages/${id}`, { method: 'DELETE' })
}

export async function updatePackage(
  id: number,
  fields: { name?: string; locked?: boolean }
): Promise<{ id: number; name?: string; locked?: boolean }> {
  return api(`/api/packages/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(fields),
  })
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const isGet = !init || !init.method || init.method === 'GET'
  const response = await fetch(path, {
    ...(isGet ? { cache: 'no-store' } : {}),
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
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
