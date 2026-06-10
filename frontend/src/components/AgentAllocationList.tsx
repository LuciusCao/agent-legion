import { useCallback, useEffect, useRef, useState } from 'react'
import type { AgentStatus, WorkspaceAgentAssignment } from '../types'
import { fetchAgents } from '../api'
import { WORKSPACE_LABELS } from '../labels'
import { useUiStore } from '../stores/uiStore'
import styles from './AgentAllocationList.module.css'

interface AgentAllocationListProps {
  workspaceId: string
  assignments: WorkspaceAgentAssignment[] | null
  onAssignmentsChange: (assignments: WorkspaceAgentAssignment[] | null) => void
}

function clampLimit(value: string): number {
  const parsed = Number.parseInt(value, 10)
  return Number.isNaN(parsed) || parsed < 1 ? 1 : parsed
}

export function AgentAllocationList({
  assignments,
  onAssignmentsChange,
}: AgentAllocationListProps) {
  const [agents, setAgents] = useState<AgentStatus[]>([])
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editLimit, setEditLimit] = useState(1)

  const showToast = useUiStore((state) => state.showToast)
  const cancelledRef = useRef(false)

  const loadData = useCallback(async () => {
    try {
      setLoading(true)
      const all = await fetchAgents()
      if (cancelledRef.current) return
      setAgents(all.agents)
    } catch (err) {
      if (cancelledRef.current) return
      const message = err instanceof Error ? err.message : '加载失败'
      showToast(message, 'error')
    } finally {
      if (!cancelledRef.current) {
        setLoading(false)
      }
    }
  }, [showToast])

  useEffect(() => {
    cancelledRef.current = false
    const run = async () => {
      await loadData()
    }
    void run()
    return () => {
      cancelledRef.current = true
    }
  }, [loadData])

  const currentAssignments = assignments ?? []

  const assignedIds = new Set(currentAssignments.map((a) => a.agent_id))
  const availableAgents = agents.filter((a) => !assignedIds.has(a.id))
  const assignedAgents = currentAssignments
    .map((a) => ({ ...a, agent: agents.find((g) => g.id === a.agent_id) }))
    .sort((a, b) =>
      (a.agent?.name ?? a.agent_id).localeCompare(b.agent?.name ?? b.agent_id)
    )

  const startAssign = (agentId: string) => {
    setEditingId(agentId)
    setEditLimit(1)
  }

  const cancelAssign = () => {
    setEditingId(null)
  }

  const confirmAssign = (agentId: string, limit: number) => {
    const newAssignment: WorkspaceAgentAssignment = {
      agent_id: agentId,
      concurrency_limit: limit,
    }
    onAssignmentsChange([...currentAssignments, newAssignment])
    setEditingId(null)
  }

  const handleUnassign = (agentId: string) => {
    onAssignmentsChange(
      currentAssignments.filter((a) => a.agent_id !== agentId)
    )
  }

  const handleLimitChange = (agentId: string, value: string) => {
    const clamped = clampLimit(value)
    onAssignmentsChange(
      currentAssignments.map((a) =>
        a.agent_id === agentId ? { ...a, concurrency_limit: clamped } : a
      )
    )
  }

  return (
    <div aria-live="polite" aria-atomic="true">
      {loading && agents.length === 0 && currentAssignments.length === 0 && (
        <div className={styles.loading}>加载中...</div>
      )}

      <section aria-labelledby="available-agents-heading">
        <h3 id="available-agents-heading" className={styles.sectionTitle}>
          {WORKSPACE_LABELS.availableAgents}
        </h3>
        {availableAgents.length === 0 ? (
          <p className={styles.empty}>{WORKSPACE_LABELS.noAgentsAvailable}</p>
        ) : (
          <ul className={styles.list} data-testid="available-agents">
            {availableAgents.map((agent) => (
              <li key={agent.id} className={styles.row}>
                <span
                  className={`${styles.dot} ${agent.busy ? styles.dotBusy : ''}`}
                  aria-hidden="true"
                />
                <span className={styles.name}>{agent.name}</span>
                <span className={styles.pill}>
                  {agent.busy
                    ? `忙碌 (${agent.task_count}/${agent.max_tasks})`
                    : '空闲'}
                </span>
                {editingId === agent.id ? (
                  <div className={styles.inlineForm}>
                    <md-outlined-text-field
                      type="number"
                      min={1}
                      value={editLimit}
                      onInput={(event: Event) =>
                        setEditLimit(
                          Math.max(
                            1,
                            Number.parseInt(
                              (event.target as HTMLInputElement).value,
                              10
                            ) || 1
                          )
                        )
                      }
                      style={{ width: 96 }}
                      aria-label={WORKSPACE_LABELS.concurrencyLimit}
                    />
                    <md-filled-button
                      onClick={() => confirmAssign(agent.id, editLimit)}
                    >
                      {WORKSPACE_LABELS.confirmAssign}
                    </md-filled-button>
                    <md-outlined-button onClick={cancelAssign}>
                      {WORKSPACE_LABELS.cancel}
                    </md-outlined-button>
                  </div>
                ) : (
                  <md-outlined-button onClick={() => startAssign(agent.id)}>
                    {WORKSPACE_LABELS.assignAgent}
                  </md-outlined-button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="assigned-agents-heading">
        <h3 id="assigned-agents-heading" className={styles.sectionTitle}>
          {WORKSPACE_LABELS.assignedAgents}
        </h3>
        {assignedAgents.length === 0 ? (
          <p className={styles.empty}>{WORKSPACE_LABELS.noAgentsAssigned}</p>
        ) : (
          <ul className={styles.list} data-testid="assigned-agents">
            {assignedAgents.map(({ agent_id, concurrency_limit, agent }) => (
              <li key={agent_id} className={styles.row}>
                {agent && (
                  <>
                    <span
                      className={`${styles.dot} ${agent.busy ? styles.dotBusy : ''}`}
                      aria-hidden="true"
                    />
                    <span className={styles.name}>{agent.name}</span>
                    <span className={styles.pill}>
                      {agent.busy
                        ? `忙碌 (${agent.task_count}/${agent.max_tasks})`
                        : '空闲'}
                    </span>
                  </>
                )}
                {!agent && <span className={styles.name}>{agent_id}</span>}
                <md-outlined-text-field
                  type="number"
                  min={1}
                  value={concurrency_limit}
                  onInput={(event: Event) =>
                    handleLimitChange(
                      agent_id,
                      (event.target as HTMLInputElement).value
                    )
                  }
                  style={{ width: 96 }}
                  aria-label={WORKSPACE_LABELS.concurrencyLimit}
                />
                <md-outlined-button
                  onClick={() => handleUnassign(agent_id)}
                >
                  {WORKSPACE_LABELS.unassignAgent}
                </md-outlined-button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
