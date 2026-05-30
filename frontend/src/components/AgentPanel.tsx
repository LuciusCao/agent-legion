import { useEffect } from 'react'
import { useUiStore } from '../stores/uiStore'
import styles from './AgentPanel.module.css'

export function AgentPanel() {
  const agents = useUiStore((state) => state.agents)
  const workerPaused = useUiStore((state) => state.workerPaused)
  const fetchWorkerStatus = useUiStore((state) => state.fetchWorkerStatus)
  const setWorkerPaused = useUiStore((state) => state.setWorkerPaused)
  const showToast = useUiStore((state) => state.showToast)

  useEffect(() => {
    fetchWorkerStatus().catch((err) => {
      const message = err instanceof Error ? err.message : String(err)
      showToast(`加载自动调度状态失败: ${message}`, 'error')
    })
  }, [fetchWorkerStatus, showToast])

  const handlePausedChange = async () => {
    const paused = !workerPaused
    try {
      await setWorkerPaused(paused)
      showToast(paused ? '已关闭自动调度' : '已开启自动调度', 'success')
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      showToast(`更新自动调度状态失败: ${message}`, 'error')
    }
  }

  return (
    <div className={`${styles.agentPanel} card-outlined`}>
      <div className={styles.agentContent}>
        {agents.length === 0 ? (
          <div className="empty-state">暂无运行中的 Agent</div>
        ) : (
          <div className={styles.agentList}>
            {agents.map((agent, i) => (
              <div
                key={i}
                className={`${styles.agentCard} ${agent.busy ? styles.busy : styles.idle}`}
              >
                <span className={styles.agentDot} />
                <span>{agent.name}</span>
                <span className={styles.agentPill}>
                  {agent.busy
                    ? `忙碌 (${agent.task_count}/${agent.max_tasks})`
                    : '空闲'}
                </span>
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
    </div>
  )
}
