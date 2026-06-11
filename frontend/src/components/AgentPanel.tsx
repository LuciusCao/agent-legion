import { useEffect, useMemo } from 'react'
import { useUiStore } from '../stores/uiStore'
import type { AgentStatus } from '../types'
import styles from './AgentPanel.module.css'

export interface AgentPanelProps {
  agents?: AgentStatus[]
  workerPaused?: boolean
  onTogglePause?: () => void
  autoFetch?: boolean
  bare?: boolean
  compact?: boolean
  allowedAgentIds?: string[]
  workspaceId?: string
}

export function AgentPanel({
  agents: propAgents,
  workerPaused: propPaused,
  onTogglePause,
  autoFetch = true,
  bare = false,
  compact = false,
  allowedAgentIds,
  workspaceId,
}: AgentPanelProps) {
  const storeAgents = useUiStore((state) => state.agents)
  const storePaused = useUiStore((state) => state.workerPaused)
  const fetchWorkerStatus = useUiStore((state) => state.fetchWorkerStatus)
  const setWorkerPaused = useUiStore((state) => state.setWorkerPaused)
  const showToast = useUiStore((state) => state.showToast)

  const rawAgents = propAgents ?? storeAgents
  const agents =
    allowedAgentIds !== undefined
      ? rawAgents.filter((a) => allowedAgentIds.includes(a.id))
      : rawAgents
  const workerPaused = propPaused ?? storePaused

  useEffect(() => {
    if (!autoFetch) return
    fetchWorkerStatus(workspaceId).catch((err) => {
      const message = err instanceof Error ? err.message : String(err)
      showToast(`加载自动调度状态失败: ${message}`, 'error')
    })
  }, [autoFetch, fetchWorkerStatus, showToast, workspaceId])

  const handlePausedChange = async () => {
    if (onTogglePause) {
      onTogglePause()
      return
    }
    const paused = !workerPaused
    try {
      await setWorkerPaused(paused, workspaceId)
      showToast(paused ? '已关闭自动调度' : '已开启自动调度', 'success')
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      showToast(`更新自动调度状态失败: ${message}`, 'error')
    }
  }

  const byWorkspace = useMemo(() => {
    const map = new Map<string, AgentStatus[]>()
    for (const a of agents) {
      const ws = a.workspace_id || 'global'
      if (!map.has(ws)) map.set(ws, [])
      map.get(ws)!.push(a)
    }
    return map
  }, [agents])

  const overallStatus =
    agents.length === 0 ? 'none' : agents.some((a) => a.busy) ? 'busy' : 'idle'

  const content = compact ? (
    <div className={styles.agentTrigger}>
      <md-icon-button aria-label="Agent 状态">
        <md-icon>smart_toy</md-icon>
        <span
          className={`${styles.indicator} ${styles[`indicator${overallStatus.charAt(0).toUpperCase() + overallStatus.slice(1)}`]}`}
        />
      </md-icon-button>
      <div className={styles.agentPopover}>
        <label className={styles.popoverAction}>
          <span>{workerPaused ? '自动调度关闭' : '自动调度开启'}</span>
          <md-switch
            selected={!workerPaused || undefined}
            onClick={handlePausedChange}
          />
        </label>
        <div className={styles.popoverDivider} />
        {agents.length === 0 ? (
          <div className={styles.popoverEmpty}>暂无运行中的 Agent</div>
        ) : (
          <div className={styles.popoverList}>
            {[...byWorkspace.entries()].map(([wsId, wsAgents]) => (
              <div key={wsId}>
                <div
                  style={{
                    fontSize: '12px',
                    color: 'var(--md-sys-color-on-surface-variant)',
                    padding: '4px 0',
                  }}
                >
                  {wsId === 'global' ? '全局' : wsId}
                </div>
                {wsAgents.map((agent) => (
                  <div
                    key={`${agent.id}-${agent.workspace_id}`}
                    className={styles.popoverItem}
                  >
                    <span
                      className={`${styles.popoverDot} ${agent.busy ? styles.busy : styles.idle}`}
                    />
                    <span className={styles.popoverName}>
                      {agent.name || agent.id}
                    </span>
                    <span className={styles.popoverStatus}>
                      {agent.busy
                        ? agent.task_count > 0
                          ? `忙碌 (${agent.task_count}/${agent.max_tasks})`
                          : '忙碌'
                        : '空闲'}
                    </span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  ) : (
    <div className={styles.agentContent}>
      {agents.length === 0 ? (
        <div className="empty-state">暂无运行中的 Agent</div>
      ) : (
        <div className={styles.agentList}>
          {[...byWorkspace.entries()].map(([wsId, wsAgents]) => (
            <div
              key={wsId}
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: '4px',
                width: '100%',
              }}
            >
              <h4
                style={{
                  margin: 0,
                  fontSize: '12px',
                  color: 'var(--md-sys-color-on-surface-variant)',
                }}
              >
                {wsId === 'global' ? '全局' : wsId}
              </h4>
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                {wsAgents.map((agent) => (
                  <div
                    key={`${agent.id}-${agent.workspace_id}`}
                    className={`${styles.agentCard} ${agent.busy ? styles.busy : styles.idle}`}
                  >
                    <span className={styles.agentDot} />
                    <span>{agent.name || agent.id}</span>
                    <span className={styles.agentPill}>
                      {agent.busy
                        ? agent.task_count > 0
                          ? `忙碌 (${agent.task_count}/${agent.max_tasks})`
                          : '忙碌'
                        : '空闲'}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
      <label className={styles.assignmentSwitch}>
        <span>{workerPaused ? '自动调度关闭' : '自动调度开启'}</span>
        <md-switch
          selected={!workerPaused || undefined}
          onClick={handlePausedChange}
        />
      </label>
    </div>
  )

  if (bare) {
    return content
  }

  return <div className={`${styles.agentPanel} card-outlined`}>{content}</div>
}
