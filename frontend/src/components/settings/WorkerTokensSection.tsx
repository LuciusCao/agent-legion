import { useCallback, useEffect, useState } from 'react'
import {
  createRegisterToken,
  isManagementAuthError,
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
import styles from './WorkerTokensSection.module.css'

const SESSION_KEY = 'agentWorkerMgmtToken'

function formatTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

/**
 * Worker register token management (issue / list / revoke) plus revocation of
 * already-registered workers. Management endpoints are guarded by the global
 * management token; the UI asks for it once per tab and keeps it in
 * sessionStorage only. A 401 from any call clears it and asks again.
 */
export function WorkerTokensSection() {
  const [managementToken, setManagementToken] = useState<string>(
    () => sessionStorage.getItem(SESSION_KEY) ?? ''
  )
  const [tokenInput, setTokenInput] = useState('')
  const [tokens, setTokens] = useState<AgentRegisterTokenSummary[]>([])
  const [workers, setWorkers] = useState<AgentWorkerSummary[]>([])
  const [label, setLabel] = useState('')
  const [workspaceId, setWorkspaceId] = useState('')
  const [createdToken, setCreatedToken] =
    useState<AgentRegisterTokenCreatedResponse | null>(null)
  const [copied, setCopied] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const clearManagementToken = useCallback((message: string) => {
    sessionStorage.removeItem(SESSION_KEY)
    setManagementToken('')
    setTokenInput('')
    setError(message)
  }, [])

  useEffect(() => {
    if (!managementToken) return
    let cancelled = false
    Promise.all([listRegisterTokens(managementToken), listAgentWorkers()])
      .then(([tokenList, workerList]) => {
        if (cancelled) return
        setTokens(tokenList)
        setWorkers(workerList)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        if (isManagementAuthError(err)) {
          clearManagementToken('管理口令不正确或已失效，请重新输入')
        } else {
          setError(err instanceof Error ? err.message : String(err))
        }
      })
    return () => {
      cancelled = true
    }
  }, [managementToken, clearManagementToken])

  async function handleUnlock() {
    const candidate = tokenInput.trim()
    if (!candidate) return
    setError('')
    setLoading(true)
    try {
      await listRegisterTokens(candidate)
      sessionStorage.setItem(SESSION_KEY, candidate)
      setManagementToken(candidate)
    } catch (err) {
      if (isManagementAuthError(err)) {
        setError('管理口令不正确，请重新输入')
      } else {
        setError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setLoading(false)
    }
  }

  async function handleCreate() {
    const trimmedLabel = label.trim()
    if (!trimmedLabel) return
    setError('')
    setLoading(true)
    try {
      const created = await createRegisterToken(managementToken, {
        label: trimmedLabel,
        workspace_id: workspaceId.trim() || null,
      })
      setCreatedToken(created)
      setCopied(false)
      setLabel('')
      setWorkspaceId('')
      setTokens(await listRegisterTokens(managementToken))
    } catch (err) {
      if (isManagementAuthError(err)) {
        clearManagementToken('管理口令已失效，请重新输入')
      } else {
        setError(err instanceof Error ? err.message : String(err))
      }
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
      await revokeRegisterToken(managementToken, token.token_id)
      setTokens(await listRegisterTokens(managementToken))
    } catch (err) {
      if (isManagementAuthError(err)) {
        clearManagementToken('管理口令已失效，请重新输入')
      } else {
        setError(err instanceof Error ? err.message : String(err))
      }
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
      await revokeAgentWorker(managementToken, worker.worker_id)
      setWorkers(await listAgentWorkers())
    } catch (err) {
      if (isManagementAuthError(err)) {
        clearManagementToken('管理口令已失效，请重新输入')
      } else {
        setError(err instanceof Error ? err.message : String(err))
      }
    }
  }

  if (!managementToken) {
    return (
      <div className={styles.card}>
        <h3 className={styles.heading}>管理口令</h3>
        <div className={styles.row}>
          <input
            type="password"
            className={styles.input}
            placeholder="输入全局管理 token（agent_worker_register_token）"
            aria-label="管理口令"
            value={tokenInput}
            onChange={(event) => setTokenInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void handleUnlock()
            }}
          />
          <button
            type="button"
            className={styles.button}
            onClick={() => void handleUnlock()}
            disabled={loading || tokenInput.trim() === ''}
          >
            解锁
          </button>
        </div>
        <p className={styles.hint}>
          口令来自 Host 上的
          deploy/secrets/agent_worker_register_token，仅保存在当前标签页会话中，关闭页面即失效。
        </p>
        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}
      </div>
    )
  }

  return (
    <div>
      {error && (
        <p className={styles.error} role="alert">
          {error}
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
          <input
            className={styles.input}
            placeholder="workspace_id（可空，空 = 全部 workspace）"
            aria-label="workspace 范围"
            value={workspaceId}
            onChange={(event) => setWorkspaceId(event.target.value)}
          />
          <button
            type="button"
            className={styles.button}
            onClick={() => void handleCreate()}
            disabled={loading || label.trim() === ''}
          >
            签发
          </button>
        </div>

        {createdToken && (
          <div data-testid="created-token">
            <p className={styles.hint}>
              已签发「{createdToken.label}」
              {createdToken.workspace_id
                ? `（仅 ${createdToken.workspace_id}）`
                : '（全部 workspace）'}
              ：
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
              <span className={styles.chip}>
                {token.workspace_id ?? '全部 workspace'}
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
