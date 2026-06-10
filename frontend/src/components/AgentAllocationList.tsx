import { useCallback, useEffect, useRef, useState } from 'react'
import type { AgentStatus, WorkspaceAgentAssignment } from '../types'
import {
  assignAgent,
  fetchAgents,
  fetchWorkspaceAgents,
  unassignAgent,
} from '../api'
import { WORKSPACE_LABELS } from '../labels'
import { useUiStore } from '../stores/uiStore'
import { useWorkspaceStore } from '../stores/workspaceStore'
import styles from './AgentAllocationList.module.css'

interface AgentAllocationListProps {
  workspaceId: string
}

function clampLimit(value: string): number {
  const parsed = Number.parseInt(value, 10)
  return Number.isNaN(parsed) || parsed < 1 ? 1 : parsed
}

export function AgentAllocationList({ workspaceId }: AgentAllocationListProps) {
  const [agents, setAgents] = useState<AgentStatus[]>([])
  const [assignments, setAssignments] = useState<WorkspaceAgentAssignment[]>([])
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editLimit, setEditLimit] = useState(1)
  const [savingId, setSavingId] = useState<string | null>(null)
  const [limitDrafts, setLimitDrafts] = useState<Record<string, string>>({})

  const showToast = useUiStore((state) => state.showToast)
  const fetchWorkspaceStats = useWorkspaceStore(
    (state) => state.fetchWorkspaceStats
  )
  const cancelledRef = useRef(false)

  const loadData = useCallback(async () => {
    try {
      setLoading(true)
      const [all, assigned] = await Promise.all([
        fetchAgents(),
        fetchWorkspaceAgents(workspaceId),
      ])
      if (cancelledRef.current) return
      setAgents(all.agents)
      setAssignments(assigned.agents)
    } catch (err) {
      if (cancelledRef.current) return
      const message = err instanceof Error ? err.message : '加载失败'
      showToast(message, 'error')
    } finally {
      if (!cancelledRef.current) {
        setLoading(false)
      }
    }
  }, [workspaceId, showToast])

  useEffect(() => {
    cancelledRef.current = false
    const run = async () => {
      await loadData()
    }
    void run()
    return () => {
      cancelledRef.current = true
    }
  }, [workspaceId, loadData])

  const assignedIds = new Set(assignments.map((a) => a.agent_id))
  const availableAgents = agents.filter((a) => !assignedIds.has(a.id))
  const assignedAgents = assignments
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

  const removeDraft = (agentId: string) => {
    setLimitDrafts((prev) => {
      if (!(agentId in prev)) return prev
      const next = { ...prev }
      delete next[agentId]
      return next
    })
  }

  const confirmAssign = async (agentId: string, limit: number) => {
    setSavingId(agentId)
    try {
      await assignAgent(workspaceId, agentId, limit)
      showToast('分配成功', 'success')
      setEditingId(null)
      removeDraft(agentId)
      await fetchWorkspaceStats(workspaceId)
      if (!cancelledRef.current) {
        await loadData()
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : '分配失败'
      showToast(message, 'error')
    } finally {
      setSavingId(null)
    }
  }

  const handleUnassign = async (agentId: string) => {
    setSavingId(agentId)
    try {
      await unassignAgent(workspaceId, agentId)
      showToast('已取消分配', 'success')
      removeDraft(agentId)
      await fetchWorkspaceStats(workspaceId)
      if (!cancelledRef.current) {
        await loadData()
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : '取消分配失败'
      showToast(message, 'error')
    } finally {
      setSavingId(null)
    }
  }

  const handleLimitChange = (agentId: string, value: string) => {
    setLimitDrafts((prev) => ({ ...prev, [agentId]: value }))
  }

  const commitLimitDraft = (agentId: string) => {
    setLimitDrafts((prev) => {
      if (!(agentId in prev)) return prev
      const next = { ...prev }
      next[agentId] = String(clampLimit(prev[agentId]))
      return next
    })
  }

  const handleSaveLimit = async (agentId: string) => {
    const draft = limitDrafts[agentId]
    const current =
      draft !== undefined
        ? clampLimit(draft)
        : (assignments.find((a) => a.agent_id === agentId)?.concurrency_limit ??
          1)
    setSavingId(agentId)
    try {
      await assignAgent(workspaceId, agentId, current)
      showToast('并发限制已更新', 'success')
      removeDraft(agentId)
      await fetchWorkspaceStats(workspaceId)
      if (!cancelledRef.current) {
        await loadData()
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : '更新失败'
      showToast(message, 'error')
    } finally {
      setSavingId(null)
    }
  }

  const isBusy = (id: string) => savingId === id

  return (
    <div aria-live="polite" aria-atomic="true">
      {loading && agents.length === 0 && assignments.length === 0 && (
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
                      disabled={isBusy(agent.id) || undefined}
                    >
                      {WORKSPACE_LABELS.confirmAssign}
                    </md-filled-button>
                    <md-outlined-button
                      onClick={cancelAssign}
                      disabled={isBusy(agent.id) || undefined}
                    >
                      {WORKSPACE_LABELS.cancel}
                    </md-outlined-button>
                  </div>
                ) : (
                  <md-outlined-button
                    onClick={() => startAssign(agent.id)}
                    disabled={isBusy(agent.id) || undefined}
                  >
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
                  value={limitDrafts[agent_id] ?? concurrency_limit}
                  onInput={(event: Event) =>
                    handleLimitChange(
                      agent_id,
                      (event.target as HTMLInputElement).value
                    )
                  }
                  onBlur={() => commitLimitDraft(agent_id)}
                  style={{ width: 96 }}
                  aria-label={WORKSPACE_LABELS.concurrencyLimit}
                />
                <md-filled-button
                  onClick={() => handleSaveLimit(agent_id)}
                  disabled={isBusy(agent_id) || undefined}
                >
                  {WORKSPACE_LABELS.saveAgents}
                </md-filled-button>
                <md-outlined-button
                  onClick={() => handleUnassign(agent_id)}
                  disabled={isBusy(agent_id) || undefined}
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
