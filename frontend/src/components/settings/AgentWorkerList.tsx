import { useState } from 'react'
import { deleteAgentWorker } from '../../api'
import type { AgentRegisterTokenSummary, AgentWorkerSummary } from '../../api'
import { toErrorMessage } from '../../lib/queryError'
import { ConfirmDialog } from '../ConfirmDialog'
import styles from './WorkerTokensSection.module.css'

export function workerName(worker: AgentWorkerSummary): string {
  return worker.name || worker.worker_id
}

interface AgentWorkerListProps {
  workers: AgentWorkerSummary[]
  // Every issued key (all workspaces), for rendering the worker↔key binding
  // (schema v59) by label and for the delete gate below.
  tokens: AgentRegisterTokenSummary[]
  workspaceName: (workspaceId: string | null) => string
  onChanged: () => void
  onError: (message: string) => void
}

/**
 * Registered-worker list. There is no per-worker revoke: a worker's access is
 * cut by deleting its register keys — deleting a key cascade-deletes workers
 * left without any live key and narrows survivors to their remaining keys.
 * Manually deleting the registration record remains for legacy workers
 * without a recorded binding (the migration cleanup target); the backend
 * enforces the same gate with 409.
 */
export function AgentWorkerList({
  workers,
  tokens,
  workspaceName,
  onChanged,
  onError,
}: AgentWorkerListProps) {
  const tokenById = new Map(tokens.map((token) => [token.token_id, token]))
  const [pendingDeleteWorker, setPendingDeleteWorker] =
    useState<AgentWorkerSummary | null>(null)

  function deletable(worker: AgentWorkerSummary): boolean {
    return (worker.register_token_ids ?? []).every((id) => !tokenById.has(id))
  }

  function boundLabel(id: string): string {
    const bound = tokenById.get(id)
    if (!bound) return `${id.slice(0, 8)}（已删除）`
    return bound.revoked ? `${bound.label}（已失效）` : bound.label
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
        <p className={styles.empty}>暂无已注册 Worker</p>
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
                className={`${styles.chip} ${
                  worker.online ? styles.chipActive : ''
                }`}
                title={`最近心跳 ${worker.last_seen_at}`}
              >
                {worker.online ? '在线' : '离线'}
              </span>
              {worker.allowed_workspaces.length === 0 ? (
                <span
                  className={`${styles.chip} ${styles.chipRevoked}`}
                  title="旧全局 token 注册的存量 Worker（scope=全部）。仅管理员可见；删除其注册记录后请为其签发 workspace key 并重新注册"
                >
                  待迁移（旧全局注册）
                </span>
              ) : (
                <span className={styles.chipScope}>
                  {worker.allowed_workspaces
                    .map((id) => workspaceName(id))
                    .join(', ')}
                </span>
              )}
              {(worker.register_token_ids ?? []).length > 0 && (
                <span
                  className={styles.chip}
                  title={`该 Worker 最近一次注册使用的 key：${(worker.register_token_ids ?? []).map(boundLabel).join('、')}`}
                >
                  绑定 key：
                  {(worker.register_token_ids ?? []).map(boundLabel).join('、')}
                </span>
              )}
              {worker.revoked && (
                <span className={`${styles.chip} ${styles.chipRevoked}`}>
                  已失效（旧版吊销）
                </span>
              )}
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
