import { useNavigate } from 'react-router-dom'
import { useWorkerReadiness } from '../hooks/useWorkerReadiness'
import { workerConsoleUrl } from '../lib/workerConsoleUrl'
import { hasClaimingWorker, hasOnlineWorker } from '../lib/workerPresence'
import { MaterialIcon } from './MaterialIcon'
import { WorkerConsoleLink } from './WorkerConsoleLink'
import styles from './WorkerReadinessBanner.module.css'

export interface WorkerReadinessBannerProps {
  workspaceId: string
  /** 「等待中」任务数（queued + pending）。 */
  waitingCount: number
  /** workflow 是否含 Agent 节点（纯 code workflow 由 Host 本地执行，不查 Worker）。 */
  needsWorker: boolean
}

/**
 * 「提交了但一直没动」的排查横幅（PRD 常见问题第一条）：有任务在等待，而
 * 调度暂停 / 没有 Worker 在线 / Worker 在线但未开领取——三种都是确定性的
 * 状态而非「慢」，所以不设时间阈值，命中即显示并给出对应动作。Worker
 * 列表首次加载完之前不渲染，避免闪一帧「没有 Worker」。
 */
export function WorkerReadinessBanner({
  workspaceId,
  waitingCount,
  needsWorker,
}: WorkerReadinessBannerProps) {
  const navigate = useNavigate()
  const { workers, paused, consoleUrl, resumeScheduling } = useWorkerReadiness(
    workspaceId,
    waitingCount > 0
  )
  if (waitingCount <= 0 || workers === undefined) return null
  const online = hasOnlineWorker(workers)
  const noWorker = needsWorker && !online
  const idleWorker = needsWorker && online && !hasClaimingWorker(workers)
  if (!paused && !noWorker && !idleWorker) return null
  const entryUrl = workers.map(workerConsoleUrl).find(Boolean) ?? consoleUrl

  return (
    <section
      className={styles.banner}
      role="alert"
      data-testid="worker-readiness-banner"
    >
      <MaterialIcon name="warning" className={styles.icon} />
      <div className={styles.body}>
        <p className={styles.title}>
          有 {waitingCount} 个任务在等待中，但现在不会开始执行：
        </p>
        <ul className={styles.list}>
          {paused && (
            <li>
              本 workspace 的调度已暂停。
              <button
                type="button"
                className={styles.action}
                onClick={resumeScheduling}
              >
                恢复调度
              </button>
            </li>
          )}
          {noWorker && (
            <li>
              没有在线的 Worker。请按「设置 → Agent 与 Worker」的说明接入。
              <button
                type="button"
                className={styles.action}
                onClick={() => navigate(`/workspaces/${workspaceId}/settings`)}
              >
                去接入 Worker
              </button>
              <WorkerConsoleLink url={entryUrl} />
            </li>
          )}
          {idleWorker && (
            <li>
              Worker 在线但未开始领取任务。到 Worker 控制台点「开始领取」。
              <WorkerConsoleLink url={entryUrl} />
            </li>
          )}
        </ul>
      </div>
    </section>
  )
}
