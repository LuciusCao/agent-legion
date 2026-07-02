import { api } from './api/core'
import type {
  ArtifactResponse,
  CreateJobBatchInput,
  JobBatchResponse,
  JobDetailResponse,
  JobsResponse,
  WorkspaceRecord,
  WorkspacesResponse,
} from './types'
import type { WorkspaceStats } from './workspaceTypes'

export { api } from './api/core'
export {
  fetchWorkflowDefinition,
  fetchWorkflows,
  publishWorkflowDraft,
  validateWorkflowDraft,
} from './api/workflows'

export async function fetchPackages(): Promise<{
  packages: Array<{
    id: number
    name: string
    path: string
    video_count: number
    size_bytes: number
    locked: number
    created_at: string
  }>
}> {
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

export async function deleteWorkspacePackage(
  workspaceId: string,
  packageId: number
): Promise<{ deleted: boolean }> {
  return api<{ deleted: boolean }>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/packages/${packageId}`,
    { method: 'DELETE' }
  )
}

export async function updateWorkspacePackage(
  workspaceId: string,
  packageId: number,
  body: { name?: string; locked?: boolean }
): Promise<{ id: number; name?: string; locked?: boolean }> {
  return api<{ id: number; name?: string; locked?: boolean }>(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/packages/${packageId}`,
    {
      method: 'PATCH',
      body: JSON.stringify(body),
    }
  )
}

export async function fetchJobs(
  workspaceId: string,
  workflowKey?: string
): Promise<JobsResponse> {
  const params = new URLSearchParams()
  if (workflowKey) params.set('workflow_key', workflowKey)
  const query = params.toString()
  try {
    return await api(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs${query ? `?${query}` : ''}`
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
  workflowKey: string,
  cmsConfig: Record<string, unknown> = {},
  resourceConfig: Record<string, unknown> = {},
  defaultEntity: string = 'question',
  intakeConfig: Record<string, unknown> = {}
): Promise<WorkspaceRecord> {
  const result = await api<{ workspace: WorkspaceRecord }>('/api/workspaces', {
    method: 'POST',
    body: JSON.stringify({
      name,
      default_workflow_key: workflowKey,
      cms_config: cmsConfig,
      resource_config: resourceConfig,
      default_entity: defaultEntity,
      intake_config: intakeConfig,
    }),
  })
  return result.workspace
}

export async function updateWorkspace(
  workspaceId: string,
  fields: {
    name?: string
    description?: string
    default_workflow_key?: string
    default_entity?: string
    cms_config?: Record<string, unknown>
    resource_config?: Record<string, unknown>
    intake_config?: Record<string, unknown>
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

export async function createJobBatch(
  input: CreateJobBatchInput
): Promise<JobBatchResponse> {
  const { workspaceId, workflowKey, entity, sourceKind, inputField, values } =
    input
  const body: Record<string, unknown> = {
    workflow_key: workflowKey,
    source_kind: sourceKind,
  }
  if (entity) {
    body.entity = entity
  }
  body[inputField] = values
  if (inputField === 'question_ids') {
    body.knowledge_codes = []
  } else if (inputField === 'knowledge_codes') {
    body.question_ids = []
  }
  return api(`/api/workspaces/${encodeURIComponent(workspaceId)}/job-batches`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function fetchJobDetail(
  jobId: string
): Promise<JobDetailResponse> {
  return api(`/api/jobs/${encodeURIComponent(jobId)}`)
}

export async function deleteJob(jobId: string): Promise<{ deleted: string }> {
  return api(`/api/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' })
}

export async function fetchJobArtifact(
  jobId: string,
  artifactName: string
): Promise<ArtifactResponse> {
  return api(
    `/api/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(artifactName)}`
  )
}
