import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, publishAgent } from '../../../api'
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
 * 发布成功后走 AgentEditor 的 onChanged 等价物：失效 Agent 目录/定义
 * 与画布草稿查询（invalidateStudioTurnEndQueries——覆盖面正好是这两类
 * 实体的读取面），并把按钮置为「已发布」防重复提交（AgentEditor
 * setHasDraft(false) 的语义）；失败在按钮下方内联展示。实体发布与
 * workflow revision 发布是独立动作：revision 引用新版本时用户再走顶栏
 * 「发布新版本」。 */

type NodeCodeVersionResponse =
  components['schemas']['WorkflowNodeCodeVersionResponse']

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

export function EntityDraftPublishButton({
  kind,
  entityId,
}: {
  kind: 'agent' | 'code'
  entityId: string
}) {
  const workspaceId = useSettingStore((s) => s.workspaceId)
  const showToast = useUiStore((s) => s.showToast)
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [published, setPublished] = useState(false)
  const [error, setError] = useState('')

  async function publish() {
    if (!workspaceId || busy || published) return
    setError('')
    setBusy(true)
    try {
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
