import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, fetchAgentVersions, publishAgent } from '../../../api'
import type { components } from '../../../generated/api'
import { useSettingStore } from '../../../stores/settingStore'
import { useUiStore } from '../../../stores/uiStore'
import { invalidateStudioTurnEndQueries } from './studioChatInvalidation'
import styles from './StudioChatPanel.module.css'

/* #692 codex P1：Agent 定义 / 节点代码草稿卡的发布入口。这两类草稿是
 * 独立实体（各有自己的 publish 端点），不能复用 WorkflowDraftPublishButton
 * ——那个按钮发布的是编辑器 YAML（workflow revision），仅实体变更时它会
 * 因 workflow 无 diff 而禁用；若 YAML 同时引用新草稿，服务端发布门禁又
 * 会因实体未发布而拒绝。这里按卡片类型直接发布对应实体：
 * - Agent 定义 → POST /api/agent-definitions/{id}/publish?workspace_id={ws}
 *   （api/agentDefinitions.publishAgent，AgentEditor.handlePublish 同一函数）
 * - 节点代码 → POST /api/workspaces/{ws}/nodes/{key}/code/publish
 *   （WorkflowNodeCodeSection.publish 同一端点）
 *
 * #692 codex P1（第三轮）——发布前核对服务端草稿身份：实体是 workspace
 * 级状态，本会话保存后其他会话（或用户在编辑器）可以覆盖同一实体的
 * 草稿；卡片携带保存响应返回的 draftHash，发布前先读服务端当前草稿
 * 的 hash 比对，一致才发。比对用的是 versions 列表的首个 draft 行
 * （两端点的 versions 列表都 version 降序，首个 draft 即当前草稿）。
 * draftHash 为 null 的旧转录（保存响应不可解析）按「无法核对身份」处
 * 理：不拦截（否则历史会话的卡永远不能发），但风险提示降级为依赖
 * 404 兜底。发布成功后走 onChanged 的等价物：失效 react-query 侧的
 * Agent 目录/定义与画布草稿查询并把按钮置为「已发布」。失效覆盖面
 * 的如实边界：两个实体的编辑面板本体（AgentEditor /
 * WorkflowNodeCodeSection）是 useEffect 本地 fetch，不在这批 query key
 * 上——已打开的面板不会自动刷新，其发布按钮再点得 404（下方已给友好
 * 文案）；面板刷新缺口在 follow-up #709（实体发布 nonce）跟踪。失败在
 * 按钮下方内联展示。实体发布与 workflow revision 发布是独立动作：
 * revision 引用新版本时用户再走顶栏「发布新版本」。 */

type NodeCodeVersionResponse =
  components['schemas']['WorkflowNodeCodeVersionResponse']
type NodeCodeVersionsResponse =
  components['schemas']['WorkflowNodeCodeVersionsResponse']

/** 服务端当前草稿的 hash：versions 列表 version 降序，首个 draft 行
 * 即当前草稿；没有 draft（已发布/被归档）返回 null。 */
function currentDraftHash(
  versions: { status: string }[],
  hashKey: string
): string | null {
  const draft = versions.find((row) => row.status === 'draft')
  const value = draft ? (draft as Record<string, unknown>)[hashKey] : null
  return typeof value === 'string' && value ? value : null
}

function errorMessage(err: unknown): string {
  // 404 no draft：后端对无草稿实体（已发布过/竞态已发布）的拒绝，
  // 原文是英文 "no draft for ..."——卡片语境给用户可行动的中文。
  const status = (err as { status?: number } | null)?.status
  if (status === 404) return '没有待发布的草稿（可能刚已发布过）'
  return err instanceof Error ? err.message : String(err)
}

export function EntityDraftPublishButton({
  kind,
  entityId,
  draftHash,
}: {
  kind: 'agent' | 'code'
  entityId: string
  draftHash: string | null
}) {
  const workspaceId = useSettingStore((s) => s.workspaceId)
  const showToast = useUiStore((s) => s.showToast)
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [published, setPublished] = useState(false)
  const [error, setError] = useState('')

  /** 读服务端当前草稿的 hash（workspace 级权威状态）。 */
  async function serverDraftHash(): Promise<string | null> {
    if (kind === 'agent') {
      const versions = await fetchAgentVersions(workspaceId!, entityId)
      return currentDraftHash(versions.versions, 'definition_hash')
    }
    const base = `/api/workspaces/${encodeURIComponent(workspaceId!)}/nodes/${encodeURIComponent(entityId)}/code`
    const versions = await api<NodeCodeVersionsResponse>(`${base}/versions`)
    return currentDraftHash(versions.versions, 'code_hash')
  }

  async function publish() {
    if (!workspaceId || busy || published) return
    setError('')
    setBusy(true)
    try {
      if (draftHash !== null) {
        const current = await serverDraftHash()
        if (current !== draftHash) {
          setError(
            current === null
              ? '服务端草稿已变更（可能已被发布或覆盖），请刷新后重试'
              : '草稿已被其他会话或编辑器更新，当前卡片不再对应最新草稿'
          )
          return
        }
      }
      if (kind === 'agent') {
        await publishAgent(workspaceId, entityId)
        showToast(`Agent「${entityId}」已发布`, 'success')
      } else {
        const base = `/api/workspaces/${encodeURIComponent(workspaceId)}/nodes/${encodeURIComponent(entityId)}/code`
        await api<NodeCodeVersionResponse>(`${base}/publish`, {
          method: 'POST',
        })
        showToast(`节点代码「${entityId}」已发布，新执行立即生效`, 'success')
      }
      setPublished(true)
      invalidateStudioTurnEndQueries(queryClient, workspaceId)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const label = published
    ? '已发布'
    : busy
      ? '发布中…'
      : kind === 'agent'
        ? '发布 Agent 定义'
        : '发布节点代码'
  return (
    <>
      <button
        type="button"
        className={styles.draftButton}
        disabled={busy || published || !workspaceId}
        onClick={() => void publish()}
      >
        {label}
      </button>
      {error && (
        <div className={styles.draftError} role="alert">
          {error}
        </div>
      )}
    </>
  )
}
