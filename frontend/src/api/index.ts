import { api } from './core'
import type { JobDetail } from '../types/jobTypes'
import type {
  ArtifactResponse,
  CreateJobBatchInput,
  JobBatchResponse,
  JobsResponse,
  WorkspaceRecord,
  WorkspaceResponse,
  WorkspacesResponse,
} from '../types'
import type { WorkspaceStats } from '../types/workspaceTypes'

export { api } from './core'
export { fetchJobsSnapshot } from './jobSnapshot'
export { fetchFailedNodeRuns, rerunJobsByFailure } from './failureApi'
export {
  deleteWorkspacePackage,
  fetchWorkspacePackages,
  updateWorkspacePackage,
} from './workspacePackages'
// prettier-ignore
export { compareWorkflowDraft, fetchActiveWorkflowRevision, fetchWorkflowRevisionDetail, fetchWorkflowRevisions } from './workflow_revisions'
export {
  fetchWorkflowDefinition,
  fetchWorkflows,
  publishWorkflowDraft,
  validateWorkflowDraft,
} from './workflows'
export { fetchOpsMetrics } from './metrics'
// prettier-ignore
export type { MetricBucket, OpsGranularity, OpsMetricsParams, OpsMetricsResponse } from './metrics'
// prettier-ignore
export { createRegisterToken, listAgentWorkers, listRegisterTokens, revokeAgentWorker, revokeRegisterToken } from './workerTokens'
// prettier-ignore
export type { AgentRegisterTokenCreatedResponse, AgentRegisterTokenSummary, AgentWorkerSummary } from './workerTokens'

export async function fetchJobs(
  workspaceId: string,
  workflowKey?: string
): Promise<JobsResponse> {
  const params = new URLSearchParams()
  if (workflowKey) params.set('workflow_key', workflowKey)
  const query = params.toString()
  return api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/jobs${query ? `?${query}` : ''}`
  )
}

export async function fetchWorkspaces(): Promise<WorkspacesResponse> {
  return api('/api/workspaces')
}

export async function createWorkspace(
  name: string,
  workflowKey: string,
  resourceConfig: Record<string, unknown> = {},
  defaultEntity: string = 'question',
  intakeConfig: Record<string, unknown> = {}
): Promise<WorkspaceRecord> {
  const result = await api<WorkspaceResponse>('/api/workspaces', {
    method: 'POST',
    body: JSON.stringify({
      name,
      default_workflow_key: workflowKey,
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
    resource_config?: Record<string, unknown>
    intake_config?: Record<string, unknown>
  }
): Promise<WorkspaceRecord> {
  const result = await api<WorkspaceResponse>(
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
  workspaceId: string,
  input: CreateJobBatchInput
): Promise<JobBatchResponse> {
  return api(`/api/workspaces/${encodeURIComponent(workspaceId)}/job-batches`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export async function fetchJobDetail(jobId: string): Promise<JobDetail> {
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
