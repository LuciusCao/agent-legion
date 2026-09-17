import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, publishAgent } from '../../../api'
import type { components } from '../../../generated/api'
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
 * #692 codex P1（第三/四轮合并收口）——草稿身份的服务端原子核对：实体
 * 是 workspace 级状态，本会话保存后其他会话（或用户在编辑器）可以覆盖
 * 同一草稿。卡片携带保存响应返回的 draftHash，发布请求把它作为
 * expected_hash 传入——服务端在选择 draft 的同一事务内核对，不匹配 409
 * 且零发布副作用。这是「读-比对-发布」TOCTOU 窗口的根治（客户端预检式
 * 核对与发布后 hash 告警两版先前的缓解均已删除，被原子核对取代）。
 * draftHash 为 null 的卡（旧转录/无法解析保存响应）不渲染发布入口
 * （codex P1 第四轮）：无法验证身份的发布在草稿被覆盖时会静默发布别人
 * 的内容，且 404 兜底只覆盖「服务端已无草稿」的形态。
 *
 * workspaceId 是必填 prop（R4 P1）：job 排查 / 定制预览载体在非当前
 * workspace 下渲染本卡，读全局 settingStore 会发布到错误的 workspace。
 *
 * 发布成功后走 onChanged 的等价物：失效 react-query 侧的 Agent 目录/
 * 定义与画布草稿查询并把按钮置为「已发布」。失效覆盖面的如实边界：
 * 两个实体的编辑面板本体（AgentEditor / WorkflowNodeCodeSection）是
 * useEffect 本地 fetch，不在这批 query key 上——已打开的面板不会自动
 * 刷新，其发布按钮再点得 404（下方已给友好文案）；面板刷新缺口在
 * follow-up #709（实体发布 nonce）跟踪。失败在按钮下方内联展示。实体
 * 发布与 workflow revision 发布是独立动作：revision 引用新版本时用户
 * 再走顶栏「发布新版本」。 */

type NodeCodeVersionResponse =
  components['schemas']['WorkflowNodeCodeVersionResponse']

function errorMessage(err: unknown): string {
  const status = (err as { status?: number } | null)?.status
  // 服务端原子核对的拒绝：草稿在保存后被其他会话/编辑器覆盖。
  if (status === 409)
    return '草稿已被其他会话或编辑器更新，请刷新后从最新草稿重新发布'
  // 404 no draft：后端对无草稿实体（已发布过/竞态已发布）的拒绝，
  // 原文是英文 "no draft for ..."——卡片语境给用户可行动的中文。
  if (status === 404) return '没有待发布的草稿（可能刚已发布过）'
  return err instanceof Error ? err.message : String(err)
}

export function EntityDraftPublishButton({
  kind,
  entityId,
  draftHash,
  workspaceId,
}: {
  kind: 'agent' | 'code'
  entityId: string
  draftHash: string
  workspaceId: string
}) {
  const showToast = useUiStore((s) => s.showToast)
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [published, setPublished] = useState(false)
  const [error, setError] = useState('')

  async function publish() {
    if (busy || published) return
    setError('')
    setBusy(true)
    try {
      if (kind === 'agent') {
        await publishAgent(workspaceId, entityId, draftHash)
        showToast(`Agent「${entityId}」已发布`, 'success')
      } else {
        const base = `/api/workspaces/${encodeURIComponent(workspaceId)}/nodes/${encodeURIComponent(entityId)}/code`
        await api<NodeCodeVersionResponse>(`${base}/publish`, {
          method: 'POST',
          body: JSON.stringify({ expected_hash: draftHash }),
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
        disabled={busy || published}
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
