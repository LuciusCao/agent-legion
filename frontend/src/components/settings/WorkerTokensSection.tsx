import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createRegisterToken,
  deleteRegisterToken,
  fetchWorkspaces,
  listAgentWorkers,
  listRegisterTokens,
} from '../../api'
import type {
  AgentRegisterTokenCreatedResponse,
  AgentRegisterTokenSummary,
  AgentWorkerSummary,
} from '../../api'
import { queryKeys } from '../../lib/queryKeys'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { toErrorMessage } from '../../lib/queryError'
import { ConfirmDialog } from '../ConfirmDialog'
import { AgentWorkerList, workerName } from './AgentWorkerList'
import styles from './WorkerTokensSection.module.css'

/**
 * Workspace-scoped worker key management (issue / list / delete) plus the
 * registered-worker list (AgentWorkerList). Endpoints require login.
 *
 * issue #35: registration is scoped-token-only — every key is bound to one
 * workspace. This section lives on the workspace settings page, so issuance
 * is pinned to the current workspace (no workspace picker) and the key list
 * shows only this workspace's keys; the issued secret is the key's token.
 * Deleting a key cuts access immediately — workers left without any live key
 * are cascade-deleted in the same transaction, survivors are narrowed to
 * their remaining keys' scope; there is no per-worker revoke.
 */
export function WorkerTokensSection({ workspaceId }: { workspaceId: string }) {
  const [label, setLabel] = useState('')
  const [createdToken, setCreatedToken] =
    useState<AgentRegisterTokenCreatedResponse | null>(null)
  const [copied, setCopied] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [pendingDeleteToken, setPendingDeleteToken] =
    useState<AgentRegisterTokenSummary | null>(null)
  const queryClient = useQueryClient()

  const { data: lists, error: listQueryError } = useQuery({
    queryKey: extraQueryKeys.workerTokens(),
    queryFn: () => Promise.all([listRegisterTokens(), listAgentWorkers()]),
    // Worker 侧添加 token 并重注册后，这里应在几秒内自动反映出来。
    refetchInterval: 5000,
  })
  const { data: workspaces } = useQuery({
    queryKey: queryKeys.workspaces(),
    queryFn: async () => (await fetchWorkspaces()).workspaces,
  })
  const listError = toErrorMessage(listQueryError)
  const allTokens = lists?.[0] ?? []
  const tokens = allTokens.filter((token) => token.workspace_id === workspaceId)
  const workers = lists?.[1] ?? []

  // The worker↔key binding (schema v59): which workers' latest registration
  // was admitted by this key.
  function tokenConsumers(tokenId: string): AgentWorkerSummary[] {
    return workers.filter((worker) =>
      (worker.register_token_ids ?? []).includes(tokenId)
    )
  }
  const pendingConsumers = tokenConsumers(pendingDeleteToken?.token_id ?? '')
  const pendingKeyWarning =
    pendingConsumers.length > 0
      ? `该 key 当前被 ${pendingConsumers.length} 个 Worker（${pendingConsumers.map(workerName).join('、')}）的最近一次注册使用：仅绑定该 key 的 Worker 会被一并删除并立即失效；同时持有其它 key 的 Worker 保留，但范围收窄到剩余 key。`
      : ''

  function workspaceName(id: string | null): string {
    if (!id) return ''
    return workspaces?.find((w) => w.id === id)?.name ?? id
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

  async function handleDeleteToken() {
    if (!pendingDeleteToken) return
    setError('')
    try {
      await deleteRegisterToken(pendingDeleteToken.token_id)
      refresh()
    } catch (err) {
      setError(toErrorMessage(err))
    } finally {
      setPendingDeleteToken(null)
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
        <h3 className={styles.heading}>签发新 Key</h3>
        <div className={styles.row}>
          <input
            className={styles.input}
            placeholder="Key 名称（必填，如 home-mac-mini）"
            aria-label="Key 名称"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
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
          签发的 Key 固定绑定当前 workspace（全局 token 已退役）；Worker 只承接
          本 workspace 的任务。
        </p>

        {createdToken && (
          <div data-testid="created-token">
            <p className={styles.hint}>
              Key「{createdToken.label}」已签发（仅{' '}
              {workspaceName(createdToken.workspace_id)}），对应 token：
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

      <h3 className={styles.heading}>已签发 Key</h3>
      {tokens.length === 0 ? (
        <p className={styles.empty}>本 workspace 暂无已签发的 key</p>
      ) : (
        <ul className={styles.list}>
          {tokens.map((token) => {
            const consumers = tokenConsumers(token.token_id)
            return (
              <li
                key={token.token_id}
                className={styles.listItem}
                data-testid={`register-token-${token.token_id}`}
              >
                <span className={styles.itemLabel}>{token.label}</span>
                <span
                  className={styles.chip}
                  title={`Key ID：${token.token_id}`}
                >
                  {token.token_id.slice(0, 8)}
                </span>
                <span
                  className={styles.chip}
                  title={
                    consumers.length > 0
                      ? `最近注册使用该 key 的 Worker：${consumers
                          .map(workerName)
                          .join('、')}`
                      : '暂无 Worker 的最近注册使用该 key'
                  }
                >
                  {consumers.length > 0
                    ? `${consumers.length} 个 Worker 使用`
                    : '未被使用'}
                </span>
                {token.revoked && (
                  <span className={`${styles.chip} ${styles.chipRevoked}`}>
                    已失效（旧版吊销）
                  </span>
                )}
                <button
                  type="button"
                  className={styles.dangerButton}
                  onClick={() => setPendingDeleteToken(token)}
                >
                  删除
                </button>
              </li>
            )
          })}
        </ul>
      )}

      <AgentWorkerList
        workers={workers}
        tokens={allTokens}
        workspaceName={workspaceName}
        onChanged={refresh}
        onError={setError}
      />

      <ConfirmDialog
        open={pendingDeleteToken !== null}
        title="删除 Key"
        onClose={() => setPendingDeleteToken(null)}
        onConfirm={handleDeleteToken}
      >
        <p>
          确定要删除 key「{pendingDeleteToken?.label}」吗？删除后该 key
          立即失效且不可恢复。{pendingKeyWarning}
        </p>
      </ConfirmDialog>
    </div>
  )
}
