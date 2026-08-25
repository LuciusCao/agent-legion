import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createRegisterToken,
  fetchWorkspaces,
  listAgentWorkers,
  listRegisterTokens,
  revokeAgentWorker,
  revokeRegisterToken,
} from '../../api'
import type {
  AgentRegisterTokenCreatedResponse,
  AgentRegisterTokenSummary,
  AgentWorkerSummary,
} from '../../api'
import { queryKeys } from '../../lib/queryKeys'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { toErrorMessage } from '../../lib/queryError'
import styles from './WorkerTokensSection.module.css'

function formatTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

/**
 * Worker register token management (issue / list / revoke) plus revocation of
 * already-registered workers (endpoints require login).
 *
 * issue #35: registration is scoped-token-only — every token is bound to one
 * workspace (the workspace picker is mandatory) and each registered worker
 * shows the workspace scope it registered with.
 */
export function WorkerTokensSection() {
  const [label, setLabel] = useState('')
  const [workspaceId, setWorkspaceId] = useState('')
  const [createdToken, setCreatedToken] =
    useState<AgentRegisterTokenCreatedResponse | null>(null)
  const [copied, setCopied] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const queryClient = useQueryClient()

  const { data: lists, error: listQueryError } = useQuery({
    queryKey: extraQueryKeys.workerTokens(),
    queryFn: () => Promise.all([listRegisterTokens(), listAgentWorkers()]),
  })
  const { data: workspaces } = useQuery({
    queryKey: queryKeys.workspaces(),
    queryFn: async () => (await fetchWorkspaces()).workspaces,
  })
  const listError = toErrorMessage(listQueryError)
  const tokens = lists?.[0] ?? []
  const workers = lists?.[1] ?? []

  function workspaceName(workspaceId: string | null): string {
    if (!workspaceId) return ''
    return workspaces?.find((w) => w.id === workspaceId)?.name ?? workspaceId
  }

  function refresh() {
    void queryClient.invalidateQueries({
      queryKey: extraQueryKeys.workerTokens(),
    })
  }

  async function handleCreate() {
    const trimmedLabel = label.trim()
    if (!trimmedLabel || !workspaceId) return
    setError('')
    setLoading(true)
    try {
      const created = await createRegisterToken({
        label: trimmedLabel,
        workspace_id: workspaceId,
      })
      setCreatedToken(created)
      setCopied(false)
      setLabel('')
      setWorkspaceId('')
      refresh()
    } catch (err) {
      setError(toErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  async function handleCopy() {
    if (!createdToken) return
    try {
      await navigator.clipboard.writeText(createdToken.register_token)
      setCopied(true)
    } catch {
      setError('复制失败，请手动选择并复制 token')
    }
  }

  async function handleRevokeToken(token: AgentRegisterTokenSummary) {
    if (!window.confirm(`确定要吊销 token「${token.label}」吗？`)) return
    setError('')
    try {
      await revokeRegisterToken(token.token_id)
      refresh()
    } catch (err) {
      setError(toErrorMessage(err))
    }
  }

  async function handleRevokeWorker(worker: AgentWorkerSummary) {
    const name = worker.name || worker.worker_id
    if (
      !window.confirm(
        `确定要吊销 Worker「${name}」吗？吊销后它将无法继续 claim 任务。`
      )
    )
      return
    setError('')
    try {
      await revokeAgentWorker(worker.worker_id)
      refresh()
    } catch (err) {
      setError(toErrorMessage(err))
    }
  }

  return (
    <div>
      {(error || listError) && (
        <p className={styles.error} role="alert">
          {error || listError}
        </p>
      )}

      <div className={styles.card}>
        <h3 className={styles.heading}>签发新 Token</h3>
        <div className={styles.row}>
          <input
            className={styles.input}
            placeholder="标签（必填，如 home-mac-mini）"
            aria-label="Token 标签"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
          <select
            className={styles.input}
            aria-label="workspace 范围"
            value={workspaceId}
            onChange={(event) => setWorkspaceId(event.target.value)}
          >
            <option value="">选择 workspace（必选）</option>
            {(workspaces ?? []).map((workspace) => (
              <option key={workspace.id} value={workspace.id}>
                {workspace.name}（{workspace.id}）
              </option>
            ))}
          </select>
          <button
            type="button"
            className={styles.button}
            onClick={() => void handleCreate()}
            disabled={loading || label.trim() === '' || !workspaceId}
          >
            签发
          </button>
        </div>
        <p className={styles.hint}>
          每个 Token 绑定一个 workspace（全局 token 已退役）；Worker 只承接该
          workspace 的任务。
        </p>

        {createdToken && (
          <div data-testid="created-token">
            <p className={styles.hint}>
              已签发「{createdToken.label}」（仅{' '}
              {workspaceName(createdToken.workspace_id)}）：
            </p>
            <div className={styles.tokenBox}>{createdToken.register_token}</div>
            <div className={styles.row}>
              <button
                type="button"
                className={styles.button}
                onClick={() => void handleCopy()}
              >
                {copied ? '已复制' : '复制 Token'}
              </button>
              <button
                type="button"
                className={styles.dangerButton}
                onClick={() => setCreatedToken(null)}
              >
                关闭
              </button>
            </div>
            <p className={styles.warning}>
              明文 token 仅显示这一次，关闭后无法再查看，请立即复制保存。
            </p>
          </div>
        )}
      </div>

      <h3 className={styles.heading}>已签发 Token</h3>
      {tokens.length === 0 ? (
        <p className={styles.empty}>暂无已签发的 scoped token</p>
      ) : (
        <ul className={styles.list}>
          {tokens.map((token) => (
            <li
              key={token.token_id}
              className={styles.listItem}
              data-testid={`register-token-${token.token_id}`}
            >
              <span className={styles.itemLabel}>{token.label}</span>
              <span className={styles.chipScope}>
                {token.workspace_id
                  ? workspaceName(token.workspace_id)
                  : '全部 workspace（已退役）'}
              </span>
              <span className={styles.chip}>
                {formatTime(token.created_at)}
              </span>
              <span
                className={`${styles.chip} ${
                  token.revoked ? styles.chipRevoked : styles.chipActive
                }`}
              >
                {token.revoked ? '已吊销' : '有效'}
              </span>
              {!token.revoked && (
                <button
                  type="button"
                  className={styles.dangerButton}
                  onClick={() => void handleRevokeToken(token)}
                >
                  吊销
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

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
              <span className={styles.itemLabel}>
                {worker.name || worker.worker_id}
              </span>
              <span
                className={`${styles.chip} ${
                  worker.online ? styles.chipActive : ''
                }`}
                title={`最近心跳 ${worker.last_seen_at}`}
              >
                {worker.online ? '在线' : '离线'}
              </span>
              <span className={styles.chip}>{worker.runtimes.join(', ')}</span>
              <span className={styles.chip}>
                并发上限 {worker.max_concurrency}
              </span>
              {worker.allowed_workspaces.length === 0 ? (
                <span
                  className={`${styles.chip} ${styles.chipRevoked}`}
                  title="旧全局 token 注册的存量 Worker（scope=全部）。仅管理员可见；请为其签发 workspace token 并重新注册"
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
              {worker.revoked && (
                <span className={`${styles.chip} ${styles.chipRevoked}`}>
                  已吊销
                </span>
              )}
              {!worker.revoked && (
                <button
                  type="button"
                  className={styles.dangerButton}
                  onClick={() => void handleRevokeWorker(worker)}
                >
                  吊销
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
