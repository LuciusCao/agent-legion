import type {
  JobBatchResponse,
  JobsResponse,
  PipelineResponse,
  WorkspaceRecord,
  WorkspaceStats,
  WorkspacesResponse,
} from './types'

export async function fetchPackages(): Promise<
  {
    packages: Array<{
      id: number
      name: string
      path: string
      video_count: number
      size_bytes: number
      locked: number
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

export async function fetchJobs(workspaceId = 'default'): Promise<JobsResponse> {
  try {
    return await api(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs?pipeline_key=question_content`
    )
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

export async function fetchWorkspaces(): Promise<WorkspacesResponse> {
  try {
    return await api('/api/workspaces')
  } catch (error) {
    const status =
      error && typeof error === 'object' && 'status' in error
        ? Number((error as { status?: unknown }).status)
        : undefined
    throw Object.assign(
      error instanceof Error ? error : new Error('Failed to fetch workspaces'),
      { status }
    )
  }
}

export async function createWorkspace(
  name: string,
  cmsConfig: Record<string, unknown> = {}
): Promise<WorkspaceRecord> {
  const result = await api<{ workspace: WorkspaceRecord }>('/api/workspaces', {
    method: 'POST',
    body: JSON.stringify({ name, cms_config: cmsConfig }),
  })
  return result.workspace
}

export async function updateWorkspace(
  workspaceId: string,
  fields: {
    name?: string
    default_pipeline_key?: string
    cms_config?: Record<string, unknown>
    resource_config?: Record<string, unknown>
  }
): Promise<WorkspaceRecord> {
  const result = await api<{ workspace: WorkspaceRecord }>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}`,
    {
      method: 'PATCH',
      body: JSON.stringify(fields),
    }
  )
  return result.workspace
}

export async function fetchWorkspaceStats(
  workspaceId: string
): Promise<WorkspaceStats> {
  return api(`/api/workspaces/${encodeURIComponent(workspaceId)}/stats`)
}

export async function deleteWorkspace(workspaceId: string): Promise<void> {
  await api(`/api/workspaces/${encodeURIComponent(workspaceId)}`, {
    method: 'DELETE',
  })
}

export async function fetchPipelineDefinition(
  pipelineKey = 'question_content'
): Promise<PipelineResponse> {
  return api(`/api/pipelines/${encodeURIComponent(pipelineKey)}`)
}

export async function createJobBatch(
  workspaceId: string,
  questionIds: string[],
  pipelineKey = 'question_content'
): Promise<JobBatchResponse> {
  return api(`/api/workspaces/${encodeURIComponent(workspaceId)}/job-batches`, {
    method: 'POST',
    body: JSON.stringify({
      pipeline_key: pipelineKey,
      source_kind: 'question_ids',
      question_ids: questionIds,
      knowledge_codes: [],
    }),
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
