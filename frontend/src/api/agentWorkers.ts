import { api } from './core'
import type { components } from '../generated/api'

export type AgentWorkerSummary = components['schemas']['AgentWorkerSummary']
export type AgentWorkersResponse = components['schemas']['AgentWorkersResponse']

type DeleteWorkerResponse = components['schemas']['AgentWorkerDeleteResponse']

// Full list response: workers plus the deployment-level Worker console
// address (console_url, AGENT_LEGION_WORKER_CONSOLE_URL; "" = unset) that
// the "打开 Worker 控制台" entries render. Any logged-in user may call it.
export async function fetchAgentWorkers(
  workspaceId?: string
): Promise<AgentWorkersResponse> {
  // workspace_id narrows to workers registered with that workspace's scoped
  // tokens (issue #35); omitting it keeps the admin full view.
  const query = workspaceId
    ? `?workspace_id=${encodeURIComponent(workspaceId)}`
    : ''
  return api<AgentWorkersResponse>(`/api/agent-workers${query}`)
}

// Management endpoints require an admin session: delete is gated by
// require_admin on the backend (server/app/routes/agent_workers.py).
export async function listAgentWorkers(
  workspaceId?: string
): Promise<AgentWorkerSummary[]> {
  const data = await fetchAgentWorkers(workspaceId)
  return data.workers ?? []
}

// Hard-delete is only accepted once none of the worker's bound keys exist
// anymore (backend enforces 409 otherwise); deleting the key is what cuts a
// worker's access, deleting the record is the follow-up cleanup.
export async function deleteAgentWorker(
  workerId: string
): Promise<DeleteWorkerResponse> {
  return api<DeleteWorkerResponse>(
    `/api/agent-workers/${encodeURIComponent(workerId)}`,
    { method: 'DELETE' }
  )
}
