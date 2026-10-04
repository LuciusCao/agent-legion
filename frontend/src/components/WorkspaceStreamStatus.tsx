import {
  selectWorkspaceStreamHealth,
  useWorkspaceStreamStore,
} from '../stores/workspaceStreamStore'
import styles from './WorkspaceStreamStatus.module.css'

function formatClock(ms: number): string {
  return new Date(ms).toLocaleTimeString('zh-CN', { hour12: false })
}

/**
 * #720：workspace 实时流断线提示（AgentConnectionDot 同款模式：订阅连接态
 * store，健康时不渲染）。断线期间任务列表的进度停在断线时刻，显式告诉
 * 用户「进度可能已过时、正在重连」，避免把冻结进度误判为任务卡死而重跑；
 * 重连成功后 useWorkspaceEvents 重拉快照，本提示自动消失。右侧 × 可关闭
 * 本次断线周期的提示（#918，不持久化）。
 */
export function WorkspaceStreamStatus({
  workspaceId,
}: {
  workspaceId: string
}) {
  const storeWorkspaceId = useWorkspaceStreamStore((s) => s.workspaceId)
  const status = useWorkspaceStreamStore((s) => s.status)
  const everOpened = useWorkspaceStreamStore((s) => s.everOpened)
  const attempts = useWorkspaceStreamStore((s) => s.attempts)
  const staleSince = useWorkspaceStreamStore((s) => s.staleSince)
  const dismissed = useWorkspaceStreamStore((s) => s.dismissed)
  const dismiss = useWorkspaceStreamStore((s) => s.dismiss)
  const health = selectWorkspaceStreamHealth(
    { workspaceId: storeWorkspaceId, status, everOpened, attempts, staleSince },
    workspaceId
  )
  // #918：关闭只对当前断线周期生效（store 在恢复 open 时复位，刷新即清空）。
  if (health.kind === 'live' || dismissed) return null
  return (
    <div
      className={styles.notice}
      role="status"
      data-testid="workspace-stream-status"
    >
      <span
        className={`${styles.dot} ${health.kind === 'unreachable' ? styles.dotClosed : ''}`}
      />
      {health.kind === 'reconnecting' ? (
        <span>
          实时连接中断，正在重连… 任务进度停留在{' '}
          {formatClock(health.staleSince)}{' '}
          的状态，可能已过时；断线不影响服务端执行，请勿仅凭此重跑任务。
        </span>
      ) : (
        <span>实时连接未建立，正在重试… 任务进度暂不会自动刷新。</span>
      )}
      <button
        type="button"
        className={styles.close}
        aria-label="关闭断线提示"
        title="关闭（本次断线期间不再提示；刷新页面后若仍断线会再次出现）"
        onClick={() => dismiss(workspaceId)}
      >
        ×
      </button>
    </div>
  )
}
