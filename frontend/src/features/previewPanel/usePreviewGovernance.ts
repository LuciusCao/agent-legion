/**
 * 预览面板治理动作 hook（#796 验收返工，从 PreviewPanelSection 抽出保体积
 * 预算）：发布/恢复默认（归档）是头部治理区的人工动作——agent 只写草稿
 * （reject_studio_agent_scope 在后端钉死），失败原因收进 actionError 由
 * 治理区展示（role=alert），不抛全局 toast。
 * 状态按 workspaceId 键控（codex P2）：react-router 复用实例跨 workspace
 * 导航时，A 的 actionError/pending 不得泄漏到 B；A 的迟到结果写入时携带
 * 发起时的 workspaceId 快照，渲染期派生过滤（迟到响应不进 B 的头部）。
 * #841：发布带调用方看到的草稿 html_hash（expected_hash CAS）；409/404 按
 * #749 同款口径给可行动文案（publishErrorMessage）。
 */
import { useState } from 'react'
import {
  useArchivePreviewPanel,
  usePublishPreviewPanel,
} from './usePreviewPanel'

type Action = 'publish' | 'archive'

// #841：发布 CAS 被服务端拒绝——人看到的草稿在点击前已被 agent（或其他
// 会话）覆盖。与 #749 检查器面板/聊天草稿卡同一交互模式：内联提示 + 引导
// 先看最新草稿（头部状态行随 3s 轮询刷新到新版本）。预览面板没有
// capability 占用语义，409 只此一义。
export const PREVIEW_DRAFT_OVERRIDDEN_HINT =
  '草稿已被 agent 或其他会话更新，请先预览最新草稿再发布'
// 无草稿可发：刚在别处发布/归档过（与 #749 两入口同款文案）。
export const PREVIEW_NO_DRAFT_HINT = '没有待发布的草稿（可能刚已发布过）'

const statusOf = (err: unknown) => (err as { status?: number } | null)?.status

/** 失败文案：发布的 409/404 走专用提示，其余（含归档）直显后端 detail。 */
function errorMessageFor(name: Action, cause: unknown): string {
  if (name === 'publish' && statusOf(cause) === 409)
    return PREVIEW_DRAFT_OVERRIDDEN_HINT
  if (name === 'publish' && statusOf(cause) === 404)
    return PREVIEW_NO_DRAFT_HINT
  return cause instanceof Error ? cause.message : '操作失败'
}
type Scoped = { workspaceId: string | undefined; value: Action | string }

/** 键匹配才取值：已切到别的 workspace 时一律视为无状态（渲染期过滤）。 */
function scopedValue<S extends Scoped>(
  state: S | null,
  workspaceId: string | undefined
): S['value'] | null {
  return state !== null && state.workspaceId === workspaceId
    ? state.value
    : null
}

export function usePreviewGovernance(workspaceId: string | undefined) {
  const publishMutation = usePublishPreviewPanel(workspaceId)
  const archiveMutation = useArchivePreviewPanel(workspaceId)
  const [pending, setPending] = useState<Scoped | null>(null)
  const [error, setError] = useState<Scoped | null>(null)

  function run(name: Action, call: () => Promise<unknown>) {
    return async () => {
      const owner = workspaceId
      setError(null)
      setPending({ workspaceId: owner, value: name })
      try {
        await call()
      } catch (cause) {
        setError({ workspaceId: owner, value: errorMessageFor(name, cause) })
      } finally {
        // 只清自己那次的 pending：更晚发起的（同/异 workspace）不受影响。
        setPending((current) =>
          scopedValue(current, owner) === name ? null : current
        )
      }
    }
  }

  return {
    publishing: scopedValue(pending, workspaceId) === 'publish',
    actionError: scopedValue(error, workspaceId) as string | null,
    /** expectedHash：调用方看到的草稿 html_hash（发布的 CAS 令牌）。 */
    publish: (expectedHash: string) =>
      void run('publish', () => publishMutation.mutateAsync(expectedHash))(),
    archive: () => void run('archive', () => archiveMutation.mutateAsync())(),
  }
}
