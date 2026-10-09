import { useState } from 'react'
import { deleteAgentWorker } from '../../api'
import type { AgentRegisterTokenSummary, AgentWorkerSummary } from '../../api'
import { formatDateTime } from '../../lib/formatters'
import { toErrorMessage } from '../../lib/queryError'
import { workerConsoleUrl } from '../../lib/workerConsoleUrl'
import {
  PRESENCE_LABEL,
  presenceChipClass,
  presenceTitle,
  workerPresence,
} from '../../lib/workerPresence'
import { ConfirmDialog } from '../ConfirmDialog'
import { WorkerConsoleLink } from '../WorkerConsoleLink'
import { AgentWorkerScopeChips } from './AgentWorkerScopeChips'
import styles from './WorkerTokensSection.module.css'

export function workerName(worker: AgentWorkerSummary): string {
  return worker.name || worker.worker_id
}

interface AgentWorkerListProps {
  // Workers whose allowed_workspaces contains the current workspace
  // (WorkerTokensSection filters; the list itself stays dumb, issue #1141).
  workers: AgentWorkerSummary[]
  // The workspace this list is rendered for: passed through to the scope
  // chips so the row reads as serving *this* workspace (+ others).
  workspaceId: string
  // Keys of the current workspace only: the「绑定 key」chip renders just the
  // worker↔key binding (schema v59) of this workspace (issue #1141).
  tokens: AgentRegisterTokenSummary[]
  // Every issued key (all workspaces): the deletable gate must keep judging
  // "all bound keys are gone" against the full set — display filtering must
  // not open the manual-delete path for a worker whose other-workspace keys
  // are still alive (the backend re-gates with 409).
  allTokens: AgentRegisterTokenSummary[]
  workspaceName: (workspaceId: string | null) => string
  onChanged: () => void
  onError: (message: string) => void
  /** 「打开 Worker 控制台」入口地址（空串 = 未配置，只留文字说明）。 */
  consoleUrl?: string
}

/**
 * Registered-worker list scoped to one workspace (issue #1141): the section
 * only renders workers whose stored scope contains that workspace. There is
 * no per-worker revoke: a worker's access is cut by deleting its register
 * keys — deleting a key cascade-deletes workers left without any live key
 * and narrows survivors to their remaining keys. Manually deleting the
 * registration record remains for legacy workers without a recorded binding
 * (the migration cleanup target); the backend enforces the same gate with
 * 409. The worker↔key chip shows only keys bound to the listed workspace;
 * the deletable gate still evaluates the full key set (see allTokens).
 */
export function AgentWorkerList({
  workers,
  workspaceId,
  tokens,
  allTokens,
  workspaceName,
  onChanged,
  onError,
  consoleUrl = '',
}: AgentWorkerListProps) {
  const allTokenById = new Map(
    allTokens.map((token) => [token.token_id, token])
  )
  const [pendingDeleteWorker, setPendingDeleteWorker] =
    useState<AgentWorkerSummary | null>(null)

  function deletable(worker: AgentWorkerSummary): boolean {
    return (worker.register_token_ids ?? []).every(
      (id) => !allTokenById.has(id)
    )
  }

  async function handleDeleteWorker() {
    if (!pendingDeleteWorker) return
    onError('')
    try {
      await deleteAgentWorker(pendingDeleteWorker.worker_id)
      onChanged()
    } catch (err) {
      onError(toErrorMessage(err))
    } finally {
      setPendingDeleteWorker(null)
    }
  }

  return (
    <>
      <h3 className={styles.heading}>已注册 Worker</h3>
      {workers.length === 0 ? (
        <p className={styles.empty}>
          暂无已注册 Worker：在 Worker 控制台「配置 → Workspace 访问」添加本
          workspace 的 Key 后，Worker 会出现在这里。{' '}
          <WorkerConsoleLink url={consoleUrl} />
        </p>
      ) : (
        <ul className={styles.list}>
          {workers.map((worker) => (
            <li
              key={worker.worker_id}
              className={styles.listItem}
              data-testid={`worker-${worker.worker_id}`}
            >
              <span className={styles.itemLabel}>{workerName(worker)}</span>
              <span
                className={`${styles.chip} ${presenceChipClass(workerPresence(worker), styles)}`}
                title={presenceTitle(
                  workerPresence(worker),
                  `最近心跳 ${formatDateTime(worker.last_seen_at)}`
                )}
              >
                {PRESENCE_LABEL[workerPresence(worker)]}
              </span>
              <AgentWorkerScopeChips
                worker={worker}
                workspaceId={workspaceId}
                tokens={tokens}
                allTokens={allTokens}
                workspaceName={workspaceName}
              />
              {worker.revoked && (
                <span className={`${styles.chip} ${styles.chipRevoked}`}>
                  已失效（旧版吊销）
                </span>
              )}
              <WorkerConsoleLink
                url={workerConsoleUrl(worker)}
                label="控制台"
              />
              {deletable(worker) && (
                <button
                  type="button"
                  className={styles.dangerButton}
                  onClick={() => setPendingDeleteWorker(worker)}
                >
                  删除
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      <ConfirmDialog
        open={pendingDeleteWorker !== null}
        title="删除 Worker 注册记录"
        onClose={() => setPendingDeleteWorker(null)}
        onConfirm={handleDeleteWorker}
      >
        <p>
          确定要删除 Worker「
          {pendingDeleteWorker ? workerName(pendingDeleteWorker) : ''}
          」的注册记录吗？此操作不可恢复（历史执行记录不受影响）。
        </p>
      </ConfirmDialog>
    </>
  )
}
