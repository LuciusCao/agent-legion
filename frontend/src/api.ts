import type { JobsResponse } from './types'

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

export async function fetchJobs(): Promise<JobsResponse> {
  try {
    return await api('/api/jobs?pipeline_key=question_content')
  } catch (error) {
    const status =
      error && typeof error === 'object' && 'status' in error
        ? Number((error as { status?: unknown }).status)
        : undefined
    throw Object.assign(
      error instanceof Error ? error : new Error('Failed to fetch jobs'),
      { status }
    )
  }
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
