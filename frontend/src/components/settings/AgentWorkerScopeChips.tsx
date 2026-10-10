import type { AgentRegisterTokenSummary, AgentWorkerSummary } from '../../api'
import styles from './WorkerTokensSection.module.css'

interface AgentWorkerScopeChipsProps {
  worker: AgentWorkerSummary
  // The workspace this row is rendered for (WorkerTokensSection's current
  // workspace, issue #1141): its name leads the scope chip.
  workspaceId: string
  // Keys of the current workspace only: the「绑定 key」chip renders just the
  // worker↔key binding (schema v59) of this workspace.
  tokens: AgentRegisterTokenSummary[]
  // Every issued key (all workspaces): labels resolve against the full set
  // so the chip title keeps naming the complete binding.
  allTokens: AgentRegisterTokenSummary[]
  workspaceName: (workspaceId: string | null) => string
}

/**
 * The scope chip and the「绑定 key」chip of a registered-worker row
 * (issue #1141). A rendered row always serves the current workspace (the
 * list filtered on it), but may serve others too: the scope chip leads with
 * the current workspace and folds the rest into a count instead of listing
 * other workspaces' names. The [] scope is allow-all (EXEC-WORKERACL-001,
 * same predicate as claim admission): such legacy rows render the「待迁移」
 * chip instead — no scope chip, no +N count (the row serves every workspace,
 * including this one). The binding chip shows only keys issued by the
 * current workspace — the hover title still names every bound key across
 * workspaces, and only rows with at least one current-workspace key render
 * the chip at all.
 */
export function AgentWorkerScopeChips({
  worker,
  workspaceId,
  tokens,
  allTokens,
  workspaceName,
}: AgentWorkerScopeChipsProps) {
  const tokenById = new Map(tokens.map((token) => [token.token_id, token]))
  const allTokenById = new Map(
    allTokens.map((token) => [token.token_id, token])
  )
  const boundIds = worker.register_token_ids ?? []
  const visibleBoundIds = boundIds.filter((id) => tokenById.has(id))

  function boundLabel(id: string): string {
    const bound = allTokenById.get(id)
    if (!bound) return `${id.slice(0, 8)}（已删除）`
    return bound.revoked ? `${bound.label}（已失效）` : bound.label
  }

  return (
    <>
      {worker.allowed_workspaces.length === 0 ? (
        <span
          className={`${styles.chip} ${styles.chipRevoked}`}
          title="旧全局 token 注册的存量 Worker（scope=全部）。仅管理员可见；删除其注册记录后请为其签发 workspace key 并重新注册"
        >
          待迁移（旧全局注册）
        </span>
      ) : (
        <span
          className={styles.chipScope}
          title={`该 Worker 承接任务的 workspace：${[
            workspaceId,
            ...worker.allowed_workspaces.filter((id) => id !== workspaceId),
          ]
            .map((id) => workspaceName(id))
            .join('、')}`}
        >
          {workspaceName(workspaceId)}
          {worker.allowed_workspaces.length > 1 &&
            `（+${worker.allowed_workspaces.length - 1} 个其它 workspace）`}
        </span>
      )}
      {visibleBoundIds.length > 0 && (
        <span
          className={styles.chip}
          title={`该 Worker 最近一次注册使用的 key：${boundIds.map(boundLabel).join('、')}`}
        >
          绑定 key：{visibleBoundIds.map(boundLabel).join('、')}
        </span>
      )}
    </>
  )
}
