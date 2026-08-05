import type { AgentWorkerSummary as WorkerSummary } from '../api/workerTokens'
import type { AgentStatus } from '../types'

export interface WorkerRow {
  key: string
  name: string
  workload: string
  /** null for in-process agents without a registered Worker row. */
  online: boolean | null
  heartbeatTitle: string
}

export function buildWorkerRows(
  workers: WorkerSummary[],
  allAgents: AgentStatus[],
  workspaceId: string
): WorkerRow[] {
  const visibleWorkers = workers.filter(
    (worker) =>
      !worker.revoked &&
      (worker.allowed_workspaces.length === 0 ||
        worker.allowed_workspaces.includes(workspaceId))
  )
  const agents = allAgents.filter((agent) => agent.workspace_id === workspaceId)
  const agentByWorkerId = new Map(agents.map((agent) => [agent.id, agent]))

  const rows = visibleWorkers.map((worker) => {
    const agent = agentByWorkerId.get(worker.worker_id)
    const taskCount = agent?.task_count ?? 0
    const maxTasks = agent?.max_tasks ?? worker.max_concurrency
    const status = agent?.busy ? '忙碌' : worker.online ? '空闲' : ''
    return {
      key: worker.worker_id,
      name: worker.name || worker.worker_id,
      workload: `${status ? `${status} ` : ''}${taskCount}/${maxTasks}`,
      online: worker.online,
      heartbeatTitle: `最近心跳 ${worker.last_seen_at}`,
    }
  })

  // In-process agents (e.g. the local Pi executor) have no registered
  // Worker row; show them after the registered workers.
  const workerIds = new Set(visibleWorkers.map((worker) => worker.worker_id))
  const localRows = agents
    .filter((agent) => !workerIds.has(agent.id))
    .map((agent) => ({
      key: agent.id,
      name: agent.name || agent.id,
      workload: `${agent.busy ? '忙碌' : '空闲'} ${agent.task_count}/${agent.max_tasks}`,
      online: null,
      heartbeatTitle: '',
    }))
  return [...rows, ...localRows]
}
