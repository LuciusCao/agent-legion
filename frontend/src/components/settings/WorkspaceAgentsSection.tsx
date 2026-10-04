import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { archiveAgent, fetchAgentDefinitions } from '../../api'
import { useWorkflowDefinitionQuery } from '../../hooks/useWorkflowDefinitionQuery'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { toErrorMessage } from '../../lib/queryError'
import { useUiStore } from '../../stores/uiStore'
import type { AgentListItem } from '../../types'
import { ConfirmDialog } from '../ConfirmDialog'
import {
  agentNodeReferences,
  isDraftOnly,
  nodeName,
  pendingDraftCapability,
  referencedWarning,
  routedCapability,
} from './workspaceAgentReferences'
import settingsStyles from '../../pages/SettingsPage.module.css'
import styles from './WorkspaceAgentsSection.module.css'

type AgentFilter = 'all' | 'unreferenced'

const SECTION_HINT =
  '本 workspace 的全部 Agent 定义（不含已归档）。「未被引用」指当前生效的 workflow 中没有 Agent 节点使用其已发布版本的 capability（从未发布的按草稿 capability 判定），通常是重构后遗留的孤儿，可在此归档。'

const RETIREMENT_PLAN_URL =
  'https://github.com/LuciusCao/agent-legion/issues/440'

function statusLabel(agent: AgentListItem): string {
  if (isDraftOnly(agent)) return '仅草稿（从未发布）'
  if (agent.status === 'draft')
    return `已发布 v${agent.published_version ?? '?'} · 有草稿`
  return '已发布'
}

/**
 * Workspace 的 Agent 定义目录（#677）：列出全部未归档的 Agent 定义，标出
 * 「未被当前 workflow 任何节点引用」的孤儿并提供该过滤视图，列表项上带
 * 二次确认的归档动作——归档是 Agent 定义自身的生命周期动作，不依赖「恰好
 * 有节点绑定它」（Studio inspector 的归档入口只对已绑定节点可达）。
 *
 * 引用关系以 workspace 的 active revision 为准（未发布的 workflow 草稿不计），
 * Agent 侧按已发布版本的 capability 判定（#906，口径见 routedCapability）。
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
    references.get(routedCapability(agent)) ?? []
  const unreferenced = referencesKnown
    ? agents.filter((agent) => refsOf(agent).length === 0)
    : []
  const visible = filter === 'unreferenced' ? unreferenced : agents
  const pendingRefs = pending ? refsOf(pending) : []
  const pendingDraftCap = pending ? pendingDraftCapability(pending) : null

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
      {/* #932（#440 P1）：退役公告。双读阶段目录与归档原样保留（D1）。 */}
      <p className={styles.retirementNotice} role="note">
        <strong>Agent 定义即将退役：</strong>
        执行配置（runtime、工具、Worker 标签、可调参数与 skill）将改由 workflow
        节点自身声明，随 workflow
        版本发布。过渡期内本目录、引用判定与归档照常可用，后续版本将改为只读历史。
        <a href={RETIREMENT_PLAN_URL} target="_blank" rel="noreferrer">
          查看退役计划
        </a>
      </p>
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
            const draftCap = pendingDraftCapability(agent)
            return (
              <li
                key={agent.agent_id}
                className={styles.item}
                aria-label={agent.agent_id}
              >
                <div className={styles.itemMain}>
                  <span className={styles.itemLabel}>{agent.agent_id}</span>
                  <span className={styles.meta}>
                    {`capability：${routedCapability(agent)}${draftCap ? `（草稿改为 ${draftCap}，未发布）` : ''} · v${agent.version}`}
                  </span>
                </div>
                <span className={styles.chip}>{statusLabel(agent)}</span>
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
          {`确定要归档 Agent「${pending?.agent_id ?? ''}」（capability：${pending ? routedCapability(pending) : ''}）吗？其全部版本${pending && !isDraftOnly(pending) ? '（含已发布版本）' : ''}都会标记为已归档，不再出现在 Agent 目录与选择器中。`}
        </p>
        {pending && pendingDraftCap && (
          <p className={styles.dialogText}>
            {`草稿已把 capability 改为 ${pendingDraftCap}（未发布）；节点仍按已发布的 ${routedCapability(pending)} 路由到它，引用以此判定。`}
          </p>
        )}
        {pending && pendingRefs.length > 0 && (
          <p className={styles.warning} role="alert">
            {referencedWarning(pending, pendingRefs)}
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
