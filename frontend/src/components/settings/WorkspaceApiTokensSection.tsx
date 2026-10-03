import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { createWorkspaceApiToken, revokeWorkspaceApiToken } from '../../api'
import type {
  WorkspaceApiTokenCreatedResponse,
  WorkspaceApiTokenSummary,
} from '../../api'
import { useWorkspaceApiTokensQuery } from '../../hooks/useWorkspaceApiTokensQuery'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { toErrorMessage } from '../../lib/queryError'
import { ConfirmDialog } from '../ConfirmDialog'
import { ApiAccessCopyButton } from './ApiAccessCopyButton'
import styles from './ApiAccess.module.css'

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

/**
 * Workspace API intake token panel (issue #626), hosted by the 外部对接
 * section since #870: issue / list / revoke for the machine-to-machine
 * submission credentials. Differences from the worker register keys: optional
 * TTL at issuance, soft revoke instead of hard delete, and a last_used_at
 * watermark per token.
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
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [pendingRevoke, setPendingRevoke] =
    useState<WorkspaceApiTokenSummary | null>(null)
  const queryClient = useQueryClient()

  // React Router 复用组件实例：A→B 切换 workspace 时，A 的明文 token
  // （以及本次会话的临时输入/确认态）不能继续显示在 B 的面板上——面板
  // 文案把凭据描述为绑定当前 workspace，跨 workspace 残留即误导。
  // prevWorkspaceIdRef 由切换 effect 维护、始终等于最新 workspace，因此
  // 兼任异步响应的身份锚点：handleCreate 闭包捕获的是发起时的
  // workspace，响应返回时若已切走（A→B）就整体丢弃，A 的一次性明文
  // 不能漏进 B 面板。
  const prevWorkspaceIdRef = useRef(workspaceId)
  useEffect(() => {
    if (prevWorkspaceIdRef.current === workspaceId) return
    prevWorkspaceIdRef.current = workspaceId
    setCreatedToken(null)
    setLabel('')
    setTtlHours('')
    setError('')
    setPendingRevoke(null)
  }, [workspaceId])

  const { data, error: listQueryError } =
    useWorkspaceApiTokensQuery(workspaceId)
  const tokens = data?.tokens
  const listError = toErrorMessage(listQueryError)

  function refresh() {
    void queryClient.invalidateQueries({
      queryKey: extraQueryKeys.workspaceApiTokens(workspaceId),
    })
  }

  async function handleCreate() {
    if (!label.trim() || !workspaceId) return
    const ttl = ttlHours.trim() === '' ? undefined : Number(ttlHours)
    if (ttl !== undefined && (!Number.isInteger(ttl) || ttl < 1)) {
      setError('有效期必须是正整数小时，或留空表示永不过期')
      return
    }
    setError('')
    setLoading(true)
    try {
      const created = await createWorkspaceApiToken(workspaceId, {
        label: label.trim(),
        ttl_hours: ttl,
      })
      if (prevWorkspaceIdRef.current !== workspaceId) return
      setCreatedToken(created)
      setLabel('')
      setTtlHours('')
      refresh()
    } catch (err) {
      setError(toErrorMessage(err))
    } finally {
      setLoading(false)
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
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <h3 className={styles.heading}>API Token</h3>
      </div>
      {(error || listError) && (
        <p className={styles.error} role="alert">
          {error || listError}
        </p>
      )}

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
          placeholder="有效期（小时，留空永不过期）"
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
        一个外部系统一个 Token，便于单独吊销与审计。Token 仅绑定当前
        workspace，只能调用下方列出的端点，不能访问管理面或其他 workspace。
      </p>

      {createdToken && (
        <div className={styles.created} data-testid="created-api-token">
          <p className={styles.hint}>
            API Token「{createdToken.label}」已签发（仅当前 workspace）：
          </p>
          <div className={styles.tokenBox}>{createdToken.api_token}</div>
          <div className={styles.row}>
            <ApiAccessCopyButton
              text={createdToken.api_token}
              label="复制 Token"
            />
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

      <h4 className={styles.subheading}>已签发</h4>
      {tokens && tokens.length === 0 ? (
        <div className={styles.empty}>
          <p className={styles.emptyTitle}>还没有 API Token</p>本 workspace
          暂无已签发的 API Token。在上方填写名称签发后，把明文 token
          交给外部系统，配合下方接入信息即可开始调用。
        </div>
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
                className={
                  token.last_used_at
                    ? `${styles.chip} ${styles.chipActive}`
                    : styles.chip
                }
                title={token.last_used_at ?? '尚未使用'}
              >
                {token.last_used_at
                  ? `最近使用 ${formatTime(token.last_used_at)}`
                  : '未使用'}
              </span>
              <span
                className={styles.chip}
                title={token.expires_at ?? '永不过期'}
              >
                {token.expires_at
                  ? `过期 ${formatTime(token.expires_at)}`
                  : '永不过期'}
              </span>
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
          的外部系统会立即失去调用权限（401），不可恢复。
        </p>
      </ConfirmDialog>
    </div>
  )
}
