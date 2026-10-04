import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { archiveAgent, fetchAgentDefinitions } from '../../api'
import { useWorkflowDefinitionQuery } from '../../hooks/useWorkflowDefinitionQuery'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { toErrorMessage } from '../../lib/queryError'
import { useUiStore } from '../../stores/uiStore'
import type { AgentListItem, WorkflowDefinitionRecord } from '../../types'
import { ConfirmDialog } from '../ConfirmDialog'
import settingsStyles from '../../pages/SettingsPage.module.css'
import styles from './WorkspaceAgentsSection.module.css'

type WorkflowNode = WorkflowDefinitionRecord['nodes'][number]
type AgentFilter = 'all' | 'unreferenced'

/** capability → 引用它的 agent 节点（只有 `type: agent` 节点按 capability
 * 路由到 Agent；code 节点同名 capability 不构成引用）。 */
export function agentNodeReferences(
  nodes: readonly WorkflowNode[]
): Map<string, WorkflowNode[]> {
  const byCapability = new Map<string, WorkflowNode[]>()
  for (const node of nodes) {
    if (node.node_type !== 'agent' || !node.capability) continue
    const list = byCapability.get(node.capability) ?? []
    list.push(node)
    byCapability.set(node.capability, list)
  }
  return byCapability
}

const SECTION_HINT =
  '本 workspace 的全部 Agent 定义（不含已归档）。「未被引用」指当前生效的 workflow 中没有 Agent 节点使用其 capability，通常是重构后遗留的孤儿，可在此归档。'

function referencedWarning(refs: WorkflowNode[]): string {
  return `该 Agent 仍被当前 workflow 的 ${refs.length} 个节点引用（${refs.map(nodeName).join('、')}）。后端不会阻止归档，但归档后这些节点的 capability 将没有已发布的 Agent 可解析，运行与再次发布 workflow 都可能因此失败。`
}

function nodeName(node: WorkflowNode): string {
  return node.label && node.label !== node.key
    ? `${node.label}（${node.key}）`
    : node.key
}

/**
 * Workspace 的 Agent 定义目录（#677）：列出全部未归档的 Agent 定义，标出
 * 「未被当前 workflow 任何节点引用」的孤儿并提供该过滤视图，列表项上带
 * 二次确认的归档动作——归档是 Agent 定义自身的生命周期动作，不依赖「恰好
 * 有节点绑定它」（Studio inspector 的归档入口只对已绑定节点可达）。
 *
 * 引用关系以 workspace 的 active revision 为准（未发布的 workflow 草稿不计）。
 * 后端 DELETE 对仍被引用的 Agent 不拒绝（Agent 归档不改路由），故这里只在
 * 确认框里提示引用关系与后果，不在前端另立拦截。端点经 studio_secured 挂载
 * 要求 admin，页面层按角色只对 admin 渲染本区块。
 */
export function WorkspaceAgentsSection({
  workspaceId,
}: {
  workspaceId: string
}) {
  const [filter, setFilter] = useState<AgentFilter>('all')
  const [pending, setPending] = useState<AgentListItem | null>(null)
  const [error, setError] = useState('')
  const queryClient = useQueryClient()
  const showToast = useUiStore((s) => s.showToast)

  const agentsQuery = useQuery({
    queryKey: extraQueryKeys.agentDefinitions(workspaceId),
    queryFn: () => fetchAgentDefinitions(workspaceId),
  })
  const workflowQuery = useWorkflowDefinitionQuery(workspaceId)
  const listError = toErrorMessage(agentsQuery.error)
  // 引用关系只有在 active revision 查询成功返回（含 404 → null，即尚未
  // 发布任何 revision）后才可断定；在途或失败时不把任何 Agent 标成孤儿。
  const referencesKnown = workflowQuery.data !== undefined
  const references = agentNodeReferences(workflowQuery.data?.nodes ?? [])
  const agents = (agentsQuery.data?.agents ?? []).filter(
    (agent) => agent.status !== 'archived'
  )
  const refsOf = (agent: AgentListItem) =>
    references.get(agent.capability) ?? []
  const unreferenced = referencesKnown
    ? agents.filter((agent) => refsOf(agent).length === 0)
    : []
  const visible = filter === 'unreferenced' ? unreferenced : agents
  const pendingRefs = pending ? refsOf(pending) : []

  async function handleArchive() {
    if (!pending) return
    setError('')
    try {
      await archiveAgent(workspaceId, pending.agent_id)
      showToast(`Agent「${pending.agent_id}」已归档`, 'success')
      // 归档改变 capability 的 published 解析：Studio 目录同会话失效重取。
      void queryClient.invalidateQueries({
        queryKey: extraQueryKeys.agentDefinitions(workspaceId),
      })
      void queryClient.invalidateQueries({
        queryKey: extraQueryKeys.studioAgentCatalog(workspaceId),
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setPending(null)
    }
  }

  return (
    <section id="workspace-agents" className={settingsStyles.section}>
      <h2 className={settingsStyles.sectionTitle}>Agent 定义</h2>
      <hr className={settingsStyles.sectionDivider} />
      <p className={styles.hint}>{SECTION_HINT}</p>
      {(error || listError) && (
        <p className={styles.error} role="alert">
          {error || listError}
        </p>
      )}
      {workflowQuery.isError && (
        <p className={styles.hint} role="status">
          当前 workflow 加载失败，暂无法判断引用关系。
        </p>
      )}
      <div className={styles.filters} role="group" aria-label="Agent 过滤">
        <button
          type="button"
          className={styles.filterButton}
          aria-pressed={filter === 'all'}
          onClick={() => setFilter('all')}
        >
          全部（{agents.length}）
        </button>
        <button
          type="button"
          className={styles.filterButton}
          aria-pressed={filter === 'unreferenced'}
          onClick={() => setFilter('unreferenced')}
          disabled={!referencesKnown}
        >
          未被引用（{unreferenced.length}）
        </button>
      </div>
      {agentsQuery.isPending ? (
        <p className={styles.empty}>加载中...</p>
      ) : visible.length === 0 ? (
        <p className={styles.empty}>
          {filter === 'unreferenced'
            ? '没有未被引用的 Agent 定义。'
            : '暂无 Agent 定义。'}
        </p>
      ) : (
        <ul className={styles.list} aria-label="Agent 定义列表">
          {visible.map((agent) => {
            const refs = refsOf(agent)
            return (
              <li
                key={agent.agent_id}
                className={styles.item}
                aria-label={agent.agent_id}
              >
                <div className={styles.itemMain}>
                  <span className={styles.itemLabel}>{agent.agent_id}</span>
                  <span className={styles.meta}>
                    capability：{agent.capability} · v{agent.version}
                  </span>
                </div>
                <span className={styles.chip}>
                  {agent.status === 'draft' ? '草稿' : '已发布'}
                </span>
                {referencesKnown && (
                  <span
                    className={refs.length ? styles.chip : styles.chipOrphan}
                    title={refs.map(nodeName).join('、') || undefined}
                  >
                    {refs.length ? `被 ${refs.length} 个节点引用` : '未被引用'}
                  </span>
                )}
                <button
                  type="button"
                  className={styles.dangerButton}
                  onClick={() => setPending(agent)}
                  aria-label={`归档 ${agent.agent_id}`}
                >
                  归档
                </button>
              </li>
            )
          })}
        </ul>
      )}
      <ConfirmDialog
        open={pending !== null}
        title="归档 Agent"
        confirmLabel="归档"
        busyLabel="归档中..."
        onClose={() => setPending(null)}
        onConfirm={handleArchive}
      >
        <p className={styles.dialogText}>
          {`确定要归档 Agent「${pending?.agent_id ?? ''}」（capability：${pending?.capability ?? ''}）吗？其全部版本都会标记为已归档，不再出现在 Agent 目录与选择器中。`}
        </p>
        {pendingRefs.length > 0 && (
          <p className={styles.warning} role="alert">
            {referencedWarning(pendingRefs)}
          </p>
        )}
        {!referencesKnown && pending && (
          <p className={styles.warning} role="alert">
            当前 workflow 的引用关系尚未确认，请确认没有节点仍依赖该 Agent。
          </p>
        )}
      </ConfirmDialog>
    </section>
  )
}
