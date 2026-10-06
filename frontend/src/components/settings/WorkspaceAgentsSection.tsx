import { useQuery } from '@tanstack/react-query'
import { fetchAgentDefinitions, fetchAgentProvenance } from '../../api'
import { useWorkflowDefinitionQuery } from '../../hooks/useWorkflowDefinitionQuery'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { toErrorMessage } from '../../lib/queryError'
import type { AgentListItem } from '../../types'
import {
  inlinedNodeName,
  inlinedNodesByAgent,
  isDraftOnly,
  legacyAgentNodeReferences,
  nodeName,
  routedCapability,
} from './workspaceAgentReferences'
import settingsStyles from '../../pages/SettingsPage.module.css'
import styles from './WorkspaceAgentsSection.module.css'

const SECTION_HINT =
  '本 workspace 的历史 Agent 定义（不含已归档）。「已内联到 N 个节点」指当前生效的 workflow 中有 N 个节点的执行档案由该定义内联而来（内联后又改过执行档案的节点不再计入）；「仍被 N 个未内联节点使用」指尚未补 execution.runtime 的节点仍按 capability 回读它。'

const RETIREMENT_PLAN_URL =
  'https://github.com/LuciusCao/agent-legion/issues/440'

function statusLabel(agent: AgentListItem): string {
  if (isDraftOnly(agent)) return '仅草稿（从未发布）'
  if (agent.status === 'draft')
    return `已发布 v${agent.published_version ?? '?'} · 有草稿`
  return '已发布'
}

/**
 * Workspace 的历史 Agent 定义（#677 目录 → #1079 / #440 D1 只读）：执行档案
 * 已内联进 workflow 节点（schema v93），本区块只读列出未归档的 Agent 定义，
 * 并标出每个定义被内联到当前 active revision 的哪些节点（读 revision 的
 * agent_profile_provenance，`GET .../agent-provenance`）以及仍回读它的未
 * 内联节点。归档按钮已移除：旧 job 快照的重跑 / 升级仍按 Agent 定义回读，
 * 归档会让它们失败。端点经 studio_secured 挂载要求 admin，页面层按角色只
 * 对 admin 渲染本区块。
 */
export function WorkspaceAgentsSection({
  workspaceId,
}: {
  workspaceId: string
}) {
  const agentsQuery = useQuery({
    queryKey: extraQueryKeys.agentDefinitions(workspaceId),
    queryFn: () => fetchAgentDefinitions(workspaceId),
  })
  const provenanceQuery = useQuery({
    queryKey: extraQueryKeys.agentProvenance(workspaceId),
    queryFn: () => fetchAgentProvenance(workspaceId),
  })
  const workflowQuery = useWorkflowDefinitionQuery(workspaceId)
  const listError = toErrorMessage(agentsQuery.error)
  // 内联 / 引用关系只有在对应查询成功返回后才可断定；在途或失败时不出
  // 计数（不把任何定义误标为「未内联」）。
  const inlined = provenanceQuery.data
    ? inlinedNodesByAgent(provenanceQuery.data.nodes)
    : null
  const legacyRefs =
    workflowQuery.data !== undefined
      ? legacyAgentNodeReferences(workflowQuery.data?.nodes ?? [])
      : null
  const agents = (agentsQuery.data?.agents ?? []).filter(
    (agent) => agent.status !== 'archived'
  )

  return (
    <section id="workspace-agents" className={settingsStyles.section}>
      <h2 className={settingsStyles.sectionTitle}>历史 Agent 定义</h2>
      <hr className={settingsStyles.sectionDivider} />
      <p className={styles.retirementNotice} role="note">
        <strong>Agent 定义已退役为只读历史：</strong>
        执行配置（runtime、工具、Worker 标签、可调参数与 skill）已内联到
        workflow 节点，随 workflow 版本发布，请在 Studio
        的节点详情中编辑。旧任务的重跑与升级仍会回读这些定义，故不再提供归档。
        <a href={RETIREMENT_PLAN_URL} target="_blank" rel="noreferrer">
          查看退役计划
        </a>
      </p>
      <p className={styles.hint}>{SECTION_HINT}</p>
      {listError && (
        <p className={styles.error} role="alert">
          {listError}
        </p>
      )}
      {(provenanceQuery.isError || workflowQuery.isError) && (
        <p className={styles.hint} role="status">
          当前 workflow 加载失败，暂无法判断内联与引用关系。
        </p>
      )}
      {agentsQuery.isPending ? (
        <p className={styles.empty}>加载中...</p>
      ) : agents.length === 0 ? (
        <p className={styles.empty}>暂无 Agent 定义。</p>
      ) : (
        <ul className={styles.list} aria-label="历史 Agent 定义列表">
          {agents.map((agent) => {
            const nodes = inlined?.get(agent.agent_id) ?? []
            const refs = legacyRefs?.get(routedCapability(agent)) ?? []
            const version = agent.published_version ?? agent.version
            return (
              <li
                key={agent.agent_id}
                className={styles.item}
                aria-label={agent.agent_id}
              >
                <div className={styles.itemMain}>
                  <span className={styles.itemLabel}>{agent.agent_id}</span>
                  <span className={styles.meta}>
                    {`capability：${routedCapability(agent)} · v${version}`}
                  </span>
                </div>
                <span className={styles.chip}>{statusLabel(agent)}</span>
                {inlined && (
                  <span
                    className={nodes.length ? styles.chip : styles.chipMuted}
                    title={nodes.map(inlinedNodeName).join('、') || undefined}
                  >
                    {nodes.length
                      ? `已内联到 ${nodes.length} 个节点`
                      : '未内联到当前 workflow'}
                  </span>
                )}
                {refs.length > 0 && (
                  <span
                    className={styles.chipOrphan}
                    title={refs.map(nodeName).join('、')}
                  >
                    {`仍被 ${refs.length} 个未内联节点使用`}
                  </span>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
