import { useQuery } from '@tanstack/react-query'
import { listAgentWorkers } from '../../api'
import type { AgentWorkerSummary } from '../../api'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { formatDateTime } from '../../lib/formatters'
import { useWorkerConsoleUrl } from '../../hooks/useWorkerConsoleUrl'
import { workerConsoleUrl } from '../../lib/workerConsoleUrl'
import {
  PRESENCE_LABEL,
  presenceChipClass,
  presenceTitle,
  workerPresence,
} from '../../lib/workerPresence'
import { WorkerConsoleLink } from '../WorkerConsoleLink'
import styles from './WorkspaceWorkersSection.module.css'

/**
 * Read-only worker list scoped to one workspace (issue #35).
 *
 * Only workers registered with this workspace's scoped tokens are visible
 * (the backend filters by allowed_workspaces); revocation and token issuance
 * stay in the admin settings' WorkerTokensSection. Rendered for every
 * workspace member. Since #1141 the admin's WorkerTokensSection renders the
 * same workspace-scoped view (workers and keys filtered to this workspace),
 * so both identities see this workspace's workers only — there is no
 * deployment-wide worker listing on the workspace settings page anymore.
 */
export function WorkspaceWorkersSection({
  workspaceId,
}: {
  workspaceId: string
}) {
  const {
    data: workers,
    isLoading,
    error,
  } = useQuery({
    queryKey: extraQueryKeys.workspaceWorkers(workspaceId),
    queryFn: ({ signal }) => listAgentWorkers(workspaceId, signal),
    // Worker 注册/下线应在几秒内自动反映到列表。
    refetchInterval: 5000,
  })
  const consoleUrl = useWorkerConsoleUrl() ?? ''

  return (
    <div className={styles.block}>
      <h3 className={styles.heading}>本 Workspace 的 Worker</h3>
      <p className={styles.hint}>
        仅显示使用本 workspace 签发 Token 注册的 Worker；Key
        签发与删除由管理员在 「Agent 与 Worker」区管理。
      </p>
      {error && (
        <p className={styles.error} role="alert">
          {(error as Error).message}
        </p>
      )}
      {isLoading ? (
        <p className={styles.empty}>加载中…</p>
      ) : (workers ?? []).length === 0 ? (
        <p className={styles.empty}>
          本 workspace 尚无可用 Worker。请联系管理员签发本 workspace 的
          Token，并在 Worker 控制台「配置 → Workspace 访问」添加。{' '}
          <WorkerConsoleLink url={consoleUrl} />
        </p>
      ) : (
        <ul className={styles.list}>
          {(workers as AgentWorkerSummary[]).map((worker) => (
            <li
              key={worker.worker_id}
              className={styles.listItem}
              data-testid={`workspace-worker-${worker.worker_id}`}
            >
              <span className={styles.itemLabel}>
                {worker.name || worker.worker_id}
              </span>
              <span
                className={`${styles.chip} ${presenceChipClass(workerPresence(worker), styles)}`}
                title={presenceTitle(
                  workerPresence(worker),
                  `最近心跳 ${formatDateTime(worker.last_seen_at)}`
                )}
              >
                {PRESENCE_LABEL[workerPresence(worker)]}
              </span>
              <span className={styles.chip}>{worker.runtimes.join(', ')}</span>
              <span className={styles.chip}>
                并发上限 {worker.max_concurrency}
              </span>
              <WorkerConsoleLink
                url={workerConsoleUrl(worker)}
                label="控制台"
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
