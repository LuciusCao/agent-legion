import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createWorkspaceApiToken,
  listWorkspaceApiTokens,
  revokeWorkspaceApiToken,
} from '../../api'
import type {
  WorkspaceApiTokenCreatedResponse,
  WorkspaceApiTokenSummary,
} from '../../api'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { toErrorMessage } from '../../lib/queryError'
import { ConfirmDialog } from '../ConfirmDialog'
import styles from './WorkerTokensSection.module.css'

/**
 * Workspace API intake token panel (issue #626): issue / list / revoke for
 * the machine-to-machine submission credentials. Mirrors WorkerTokensSection
 * (same settings section, same one-time-plaintext UX); differences from the
 * worker keys: optional TTL at issuance, soft revoke instead of hard delete,
 * and a last_used_at watermark per token.
 */
export function WorkspaceApiTokensSection({
  workspaceId,
}: {
  workspaceId: string
}) {
  const [label, setLabel] = useState('')
  const [ttlHours, setTtlHours] = useState('')
  const [createdToken, setCreatedToken] =
    useState<WorkspaceApiTokenCreatedResponse | null>(null)
  const [copied, setCopied] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [pendingRevoke, setPendingRevoke] =
    useState<WorkspaceApiTokenSummary | null>(null)
  const queryClient = useQueryClient()

  // React Router 复用组件实例：A→B 切换 workspace 时，A 的明文 token
  // （以及本次会话的临时输入/确认态）不能继续显示在 B 的面板上——面板
  // 文案把凭据描述为绑定当前 workspace，跨 workspace 残留即误导。
  const prevWorkspaceIdRef = useRef(workspaceId)
  useEffect(() => {
    if (prevWorkspaceIdRef.current === workspaceId) return
    prevWorkspaceIdRef.current = workspaceId
    setCreatedToken(null)
    setCopied(false)
    setLabel('')
    setTtlHours('')
    setError('')
    setPendingRevoke(null)
  }, [workspaceId])

  const { data: tokens, error: listQueryError } = useQuery({
    queryKey: extraQueryKeys.workspaceApiTokens(workspaceId),
    queryFn: () => listWorkspaceApiTokens(workspaceId),
  })
  const listError = toErrorMessage(listQueryError)

  function refresh() {
    void queryClient.invalidateQueries({
      queryKey: extraQueryKeys.workspaceApiTokens(workspaceId),
    })
  }

  async function handleCreate() {
    const trimmedLabel = label.trim()
    if (!trimmedLabel || !workspaceId) return
    const ttl = ttlHours.trim() === '' ? undefined : Number(ttlHours)
    if (ttl !== undefined && (!Number.isInteger(ttl) || ttl < 1)) {
      setError('有效期必须是正整数小时，或留空表示永不过期')
      return
    }
    setError('')
    setLoading(true)
    try {
      const created = await createWorkspaceApiToken(workspaceId, {
        label: trimmedLabel,
        ttl_hours: ttl,
      })
      setCreatedToken(created)
      setCopied(false)
      setLabel('')
      setTtlHours('')
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
      await navigator.clipboard.writeText(createdToken.api_token)
      setCopied(true)
    } catch {
      setError('复制失败，请手动选择并复制 token')
    }
  }

  async function handleRevoke() {
    if (!pendingRevoke) return
    setError('')
    try {
      await revokeWorkspaceApiToken(workspaceId, pendingRevoke.token_id)
      refresh()
    } catch (err) {
      setError(toErrorMessage(err))
    } finally {
      setPendingRevoke(null)
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
        <h3 className={styles.heading}>签发 API Token</h3>
        <div className={styles.row}>
          <input
            className={styles.input}
            placeholder="Token 名称（必填，如 cms-cron）"
            aria-label="API Token 名称"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
          <input
            className={styles.input}
            placeholder="有效期（小时，可空）"
            aria-label="API Token 有效期（小时）"
            value={ttlHours}
            onChange={(event) => setTtlHours(event.target.value)}
            inputMode="numeric"
          />
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
          API Token 供外部系统（CMS / 表单 / 定时任务 / 其他
          agent）免登录提交条目： 凭{' '}
          <code>Authorization: Bearer &lt;token&gt;</code> 调用
          <code>POST /api/workspaces/{workspaceId}/runs</code>
          及运行状态只读查询；仅绑定当前 workspace，不能访问管理面或其他工作区。
        </p>

        {createdToken && (
          <div data-testid="created-api-token">
            <p className={styles.hint}>
              API Token「{createdToken.label}」已签发（仅当前
              workspace），对应凭据：
            </p>
            <div className={styles.tokenBox}>{createdToken.api_token}</div>
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

      <h3 className={styles.heading}>已签发 API Token</h3>
      {tokens && tokens.length === 0 ? (
        <p className={styles.empty}>本 workspace 暂无已签发的 API Token</p>
      ) : (
        <ul className={styles.list}>
          {(tokens ?? []).map((token) => (
            <li
              key={token.token_id}
              className={styles.listItem}
              data-testid={`api-token-${token.token_id}`}
            >
              <span className={styles.itemLabel}>
                {token.label || token.token_id}
              </span>
              <span
                className={styles.chip}
                title={`Token ID：${token.token_id}`}
              >
                {token.token_id.slice(0, 8)}
              </span>
              <span
                className={styles.chip}
                title={token.last_used_at ?? '尚未使用'}
              >
                {token.last_used_at ? '最近使用过' : '未使用'}
              </span>
              {token.expires_at && (
                <span
                  className={styles.chip}
                  title={`过期时间：${token.expires_at}`}
                >
                  {new Date(token.expires_at).toLocaleString()}
                </span>
              )}
              {token.revoked ? (
                <span className={`${styles.chip} ${styles.chipRevoked}`}>
                  已吊销
                </span>
              ) : (
                <button
                  type="button"
                  className={styles.dangerButton}
                  onClick={() => setPendingRevoke(token)}
                >
                  吊销
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      <ConfirmDialog
        open={pendingRevoke !== null}
        title="吊销 API Token"
        confirmLabel="吊销"
        onClose={() => setPendingRevoke(null)}
        onConfirm={handleRevoke}
      >
        <p>
          确定要吊销 API Token「
          {pendingRevoke?.label || pendingRevoke?.token_id}
          」吗？吊销后使用该 token
          的外部系统会立即失去提交权限（401），不可恢复。
        </p>
      </ConfirmDialog>
    </div>
  )
}
